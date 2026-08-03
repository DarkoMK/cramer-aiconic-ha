"""Async client for the Cramer AiConic (Globe gConnect) cloud API.

Authentication notes — these drive the whole design of this module:

* The GUC token endpoint issues an access token valid for 7200 s together with
  a refresh token.
* **The refresh token is single-use and rotates.** Presenting a refresh token
  returns a *new* one and immediately revokes the old one
  (``invalid_grant`` on reuse). Losing the rotated value — by crashing before
  persisting it, or by running two refreshes concurrently — permanently breaks
  authentication.

So this client:

* serialises every token acquisition behind one lock, so two callers can never
  burn the same refresh token;
* persists each rotated refresh token through ``on_tokens_updated`` the moment
  it is received;
* refreshes proactively (``TOKEN_EXPIRY_MARGIN`` before expiry) rather than
  waiting for a 401;
* falls back to a full username/password login whenever a refresh fails, so a
  lost or revoked refresh token self-heals instead of requiring the user to
  reconfigure the integration.

One more constraint shapes the request layer. The account carries a single
**token version**, and every acquisition bumps it: after a second client logs
in, the first client's access token returns
``401 {"code": 88889, "msg": "Invalid token version"}``. Refreshing does this
too, not just logging in, so the two can never hold a session at the same
time. Because ``async_ensure_token`` renews proactively, any 401 on a request
means the owner picked up their phone — not that the token aged out. Renewing
there would take the account straight back off them, which is what made the
app unusable. So the client stands down instead; see ``_begin_yield``.
"""

from __future__ import annotations

import asyncio
import base64
import binascii
import json
import logging
import time
from collections.abc import Awaitable, Callable
from dataclasses import dataclass, field
from typing import Any

import aiohttp
from aiohttp import ClientError, ClientResponseError

from .const import (
    API_CODE_OK,
    API_CODE_SERVICE_UNAVAILABLE,
    CLIENT_ID,
    CLIENT_SECRET,
    DEFAULT_REGION_CODE,
    FALLBACK_GLOBE_BASE,
    FALLBACK_GUC_BASE,
    NO_AUTH_BASE,
    REGION_LIST_PATH,
    SESSION_YIELD_SECONDS,
    TARGET,
    TENANT,
    TOKEN_EXPIRY_MARGIN,
    TOKEN_SCOPES,
    USER_AGENT,
)

_LOGGER = logging.getLogger(__name__)

REQUEST_TIMEOUT = aiohttp.ClientTimeout(total=30)
RETRY_ATTEMPTS = 3
RETRY_BACKOFF = 2.0


class CramerApiError(Exception):
    """A call to the Cramer cloud failed."""


class CramerAuthError(CramerApiError):
    """Authentication failed and cannot be recovered without new credentials."""


class CramerRateLimitError(CramerApiError):
    """The cloud is temporarily refusing requests."""


class CramerEvictedError(CramerApiError):
    """Another client holds the account session, so this one has stood down.

    Deliberately *not* a :class:`CramerAuthError`: the credentials are fine and
    raising the reauth flow would ask the owner to re-enter a correct password
    every time they opened the phone app.
    """


@dataclass
class CramerDevice:
    """A device attached to the account."""

    device_id: str
    product_id: str
    name: str
    model: str
    serial_number: str
    mac: str
    product_type: str
    is_online: bool
    firmware_version: str | None = None
    raw: dict[str, Any] = field(default_factory=dict)


def _decode_jwt(token: str) -> dict[str, Any]:
    """Decode a JWT payload without verifying the signature."""
    try:
        payload = token.split(".")[1]
        payload += "=" * (-len(payload) % 4)
        return json.loads(base64.urlsafe_b64decode(payload))
    except (IndexError, ValueError, binascii.Error, UnicodeDecodeError):
        return {}


class CramerAiConicApi:
    """Talks to the Globe gConnect cloud on behalf of one account."""

    def __init__(
        self,
        session: aiohttp.ClientSession,
        username: str,
        password: str,
        *,
        region_code: str | None = None,
        refresh_token: str | None = None,
        on_tokens_updated: Callable[[str | None, str | None], Awaitable[None]] | None = None,
    ) -> None:
        self._session = session
        self._username = username
        self._password = password
        self._region_code = region_code or DEFAULT_REGION_CODE
        self._on_tokens_updated = on_tokens_updated

        self._guc_base = FALLBACK_GUC_BASE
        self._globe_base = FALLBACK_GLOBE_BASE
        self._region_resolved = False

        self._access_token: str | None = None
        self._refresh_token: str | None = refresh_token
        self._expires_at: float = 0.0
        self._user_id: str | None = None
        #: Monotonic deadline before which this client must not touch the
        #: account, because another one has taken it.
        self._yield_until: float = 0.0

        self._token_lock = asyncio.Lock()

    # -- properties ---------------------------------------------------------
    @property
    def region_code(self) -> str:
        return self._region_code

    @property
    def is_yielded(self) -> bool:
        """Whether the account is currently being left to another client."""
        return time.monotonic() < self._yield_until

    @property
    def yield_remaining(self) -> float:
        """Seconds left before the session is reclaimed."""
        return max(0.0, self._yield_until - time.monotonic())

    @property
    def refresh_token(self) -> str | None:
        return self._refresh_token

    @property
    def user_id(self) -> str | None:
        return self._user_id

    # -- low-level HTTP -----------------------------------------------------
    async def _post_form(self, url: str, data: dict[str, str]) -> dict[str, Any]:
        try:
            async with self._session.post(
                url,
                data=data,
                headers={"User-Agent": USER_AGENT},
                timeout=REQUEST_TIMEOUT,
            ) as resp:
                body = await resp.text()
                if resp.status in (400, 401):
                    raise CramerAuthError(f"{resp.status} from {url}: {body[:200]}")
                resp.raise_for_status()
                return json.loads(body)
        except ClientResponseError as err:
            raise CramerApiError(f"HTTP {err.status} from {url}") from err
        except (ClientError, asyncio.TimeoutError) as err:
            raise CramerApiError(f"Network error calling {url}: {err}") from err
        except json.JSONDecodeError as err:
            raise CramerApiError(f"Malformed response from {url}") from err

    async def _authed_request(
        self, method: str, path: str, *, json_body: dict[str, Any] | None = None
    ) -> dict[str, Any]:
        """Call the Globe API, refreshing the token first and retrying once on 401.

        The Globe API signals application errors with a non-zero ``code`` field
        in an HTTP 200 body, so both layers are checked.
        """
        url = f"{self._globe_base}{path}"
        last_error: Exception | None = None

        for attempt in range(RETRY_ATTEMPTS):
            token = await self.async_ensure_token()
            headers = {
                "Authorization": f"Bearer {token}",
                "User-Agent": USER_AGENT,
                "Accept": "application/json",
            }
            try:
                async with self._session.request(
                    method, url, headers=headers, json=json_body, timeout=REQUEST_TIMEOUT
                ) as resp:
                    if resp.status == 401:
                        # ``async_ensure_token`` above renewed the token if it
                        # was anywhere near expiry, so the server had no reason
                        # to reject it on age. The only other thing that
                        # invalidates it is somebody else acquiring a token on
                        # this account — the phone app. Taking it back here is
                        # what logged the owner out within one poll of opening
                        # the app, so stand down instead.
                        self._begin_yield(path)
                        raise CramerEvictedError(
                            "The phone app has taken the account session; "
                            f"standing down for {SESSION_YIELD_SECONDS // 60} minutes"
                        )
                    resp.raise_for_status()
                    payload = await resp.json(content_type=None)
            except ClientResponseError as err:
                raise CramerApiError(f"HTTP {err.status} from {path}") from err
            except (ClientError, asyncio.TimeoutError) as err:
                last_error = CramerApiError(f"Network error calling {path}: {err}")
                await asyncio.sleep(RETRY_BACKOFF * (attempt + 1))
                continue

            if not isinstance(payload, dict):
                raise CramerApiError(f"Unexpected response shape from {path}")

            code = payload.get("code")
            if code in (None, API_CODE_OK):
                return payload
            if code == API_CODE_SERVICE_UNAVAILABLE:
                last_error = CramerRateLimitError(
                    f"{path}: {payload.get('msg', 'service unavailable')}"
                )
                await asyncio.sleep(RETRY_BACKOFF * (attempt + 1))
                continue
            raise CramerApiError(f"{path} returned code {code}: {payload.get('msg')}")

        raise last_error or CramerApiError(f"{path} failed after {RETRY_ATTEMPTS} attempts")

    # -- region -------------------------------------------------------------
    async def async_resolve_region(self) -> None:
        """Resolve the GUC/Globe hosts for the configured region."""
        if self._region_resolved:
            return
        url = f"{NO_AUTH_BASE}{REGION_LIST_PATH}"
        try:
            async with self._session.post(
                url, headers={"User-Agent": USER_AGENT}, timeout=REQUEST_TIMEOUT
            ) as resp:
                resp.raise_for_status()
                payload = await resp.json(content_type=None)
        except (ClientError, asyncio.TimeoutError, json.JSONDecodeError) as err:
            _LOGGER.warning(
                "Could not fetch region list (%s); using built-in defaults", err
            )
            self._region_resolved = True
            return

        regions = payload.get("list") or []
        chosen = next(
            (r for r in regions if r.get("regionCode") == self._region_code), None
        )
        if chosen is None:
            _LOGGER.warning(
                "Region %s not in region list; using built-in defaults", self._region_code
            )
        else:
            api_url = chosen.get("apiUrl") or {}
            self._guc_base = api_url.get("guc") or self._guc_base
            self._globe_base = api_url.get("globe") or self._globe_base
        self._region_resolved = True
        _LOGGER.debug("Region %s -> guc=%s globe=%s", self._region_code, self._guc_base, self._globe_base)

    async def async_list_regions(self) -> list[dict[str, Any]]:
        """Return the public region list (used by the config flow)."""
        url = f"{NO_AUTH_BASE}{REGION_LIST_PATH}"
        async with self._session.post(
            url, headers={"User-Agent": USER_AGENT}, timeout=REQUEST_TIMEOUT
        ) as resp:
            resp.raise_for_status()
            payload = await resp.json(content_type=None)
        return payload.get("list") or []

    # -- authentication -----------------------------------------------------
    def _apply_token_response(self, payload: dict[str, Any]) -> None:
        access = payload.get("access_token")
        if not access:
            raise CramerAuthError("Token response contained no access_token")
        self._access_token = access
        # Keep the previous refresh token if the server omits a new one.
        self._refresh_token = payload.get("refresh_token") or self._refresh_token
        expires_in = int(payload.get("expires_in", 7200))
        self._expires_at = time.monotonic() + expires_in
        claims = _decode_jwt(access)
        self._user_id = claims.get("sub") or self._user_id

    async def _persist_tokens(self) -> None:
        if self._on_tokens_updated is not None:
            try:
                await self._on_tokens_updated(self._refresh_token, self._region_code)
            except Exception:  # noqa: BLE001 - persistence must never break auth
                _LOGGER.exception("Failed to persist rotated refresh token")

    async def _password_login(self) -> None:
        await self.async_resolve_region()
        _LOGGER.debug("Performing password login for %s", self._username)
        payload = await self._post_form(
            f"{self._guc_base}/connect/token",
            {
                "username": self._username,
                "password": self._password,
                "Tenant": TENANT,
                "Target": TARGET,
                "grant_type": "password",
                "scope": TOKEN_SCOPES,
                "client_id": CLIENT_ID,
                "client_secret": CLIENT_SECRET,
            },
        )
        self._apply_token_response(payload)
        await self._persist_tokens()

    async def _refresh_login(self) -> None:
        await self.async_resolve_region()
        token = self._refresh_token
        if not token:
            raise CramerAuthError("No refresh token available")
        _LOGGER.debug("Refreshing access token")
        payload = await self._post_form(
            f"{self._guc_base}/connect/token",
            {
                "refresh_token": token,
                "Tenant": TENANT,
                "Target": TARGET,
                "grant_type": "refresh_token",
                "scope": TOKEN_SCOPES,
                "client_id": CLIENT_ID,
                "client_secret": CLIENT_SECRET,
            },
        )
        # The old refresh token is now dead whatever happens next; drop it
        # before applying the response so a partial failure cannot leave us
        # retrying with a revoked value.
        self._refresh_token = None
        self._apply_token_response(payload)
        await self._persist_tokens()

    def _begin_yield(self, path: str) -> None:
        """Leave the account to whoever took it, and drop the dead token.

        The evicted access token will 401 for the rest of its nominal life, so
        it is cleared here; reclaiming the session after the window has to
        start from a fresh acquisition.
        """
        self._access_token = None
        self._expires_at = 0.0
        self._yield_until = time.monotonic() + SESSION_YIELD_SECONDS
        _LOGGER.info(
            "Evicted from the Cramer account by another client (401 on %s) — "
            "leaving it to the phone app for %d minutes",
            path,
            SESSION_YIELD_SECONDS // 60,
        )

    async def async_ensure_token(self, *, force: bool = False) -> str:
        """Return a valid access token, acquiring or renewing one if needed."""
        if self.is_yielded:
            # Acquiring here would bump the token version and kick the phone
            # app straight back out, which is the whole thing being avoided.
            raise CramerEvictedError(
                "Standing down for another "
                f"{self.yield_remaining / 60:.0f} minutes; the phone app has "
                "the account session"
            )

        if (
            not force
            and self._access_token
            and time.monotonic() < self._expires_at - TOKEN_EXPIRY_MARGIN
        ):
            return self._access_token

        async with self._token_lock:
            # Another caller may have refreshed while we waited for the lock.
            if (
                not force
                and self._access_token
                and time.monotonic() < self._expires_at - TOKEN_EXPIRY_MARGIN
            ):
                return self._access_token

            if self._refresh_token:
                try:
                    await self._refresh_login()
                except CramerApiError as err:
                    _LOGGER.info(
                        "Refresh token rejected (%s); falling back to password login", err
                    )
                else:
                    return self._access_token  # type: ignore[return-value]

            await self._password_login()
            return self._access_token  # type: ignore[return-value]

    async def async_validate_credentials(self) -> str:
        """Log in with username/password. Returns the account user id."""
        await self._password_login()
        if not self._user_id:
            raise CramerAuthError("Login succeeded but no user id was returned")
        return self._user_id

    # -- devices ------------------------------------------------------------
    async def async_get_devices(self) -> list[CramerDevice]:
        """List the devices subscribed to this account."""
        await self.async_ensure_token()
        if not self._user_id:
            raise CramerApiError("No user id available")
        payload = await self._authed_request(
            "GET", f"/v2/user/{self._user_id}/subscribe/devices"
        )
        devices: list[CramerDevice] = []
        for item in payload.get("list") or []:
            device_id = str(item.get("deviceId") or item.get("device_id") or "")
            product_id = str(item.get("product_id") or "")
            if not device_id or not product_id:
                continue
            devices.append(
                CramerDevice(
                    device_id=device_id,
                    product_id=product_id,
                    name=str(item.get("name") or item.get("model") or "Mower"),
                    model=str(item.get("displayModel") or item.get("model") or ""),
                    serial_number=str(item.get("sn") or ""),
                    mac=str(item.get("mac") or ""),
                    product_type=str(item.get("product_type") or item.get("model") or ""),
                    is_online=bool(item.get("is_online")),
                    firmware_version=(
                        str(item["firmware_version"])
                        if item.get("firmware_version") not in (None, 0, "0")
                        else None
                    ),
                    raw=item,
                )
            )
        return devices

    async def async_get_datapoints(
        self, product_id: str, device_id: str, indices: list[int]
    ) -> dict[int, tuple[str, str]]:
        """Read cached datapoints.

        Returns ``{index: (hex_data, iso_timestamp)}``, skipping datapoints the
        cloud has no value for yet.
        """
        payload = await self._authed_request(
            "POST",
            f"/product/{product_id}/device/{device_id}/get_datapoints",
            json_body={"parameters": indices},
        )
        info = payload.get("info") or []
        out: dict[int, tuple[str, str]] = {}
        for index, entry in zip(indices, info):
            if not isinstance(entry, dict):
                continue
            data = entry.get("data") or ""
            if not data:
                continue
            out[index] = (data, entry.get("timestamp") or "")
        return out

    async def async_send_command(
        self, product_id: str, device_id: str, payload_hex: str
    ) -> bool:
        """Send a binary command frame. Returns the device's online flag."""
        payload = await self._authed_request(
            "POST",
            f"/product/{product_id}/device/{device_id}/send_command",
            json_body={"payload": payload_hex},
        )
        info = payload.get("info") or {}
        return bool(info.get("is_online", True))

    async def async_last_known_info(
        self, product_id: str, device_id: str
    ) -> dict[str, Any]:
        """Last known GPS position and login time, served from the cloud cache."""
        return await self._authed_request(
            "GET", f"/v4/product/{product_id}/device/{device_id}/last-known-info"
        )

    # -- AWS IoT ------------------------------------------------------------
    async def async_get_mqtt_info(self) -> dict[str, Any]:
        """Cognito identity and AWS IoT endpoint for this account."""
        payload = await self._authed_request("GET", "/v3/service/get/mqtt")
        info = payload.get("info")
        if not isinstance(info, dict) or "endpoint" not in info:
            raise CramerApiError("MQTT parameters missing from the cloud response")
        return info

    async def async_get_aws_credentials(self, mqtt_info: dict[str, Any]) -> dict[str, Any]:
        """Trade the Cognito identity token for temporary AWS credentials."""
        region = mqtt_info["region"]
        url = f"https://cognito-identity.{region}.amazonaws.com/"
        body = {
            "IdentityId": mqtt_info["identityId"],
            "Logins": {"cognito-identity.amazonaws.com": mqtt_info["token"]},
        }
        headers = {
            "Content-Type": "application/x-amz-json-1.1",
            "X-Amz-Target": "AWSCognitoIdentityService.GetCredentialsForIdentity",
        }
        try:
            async with self._session.post(
                url, headers=headers, json=body, timeout=REQUEST_TIMEOUT
            ) as resp:
                resp.raise_for_status()
                payload = await resp.json(content_type=None)
        except ClientResponseError as err:
            raise CramerApiError(f"Cognito returned HTTP {err.status}") from err
        except (ClientError, asyncio.TimeoutError) as err:
            raise CramerApiError(f"Could not reach Cognito: {err}") from err

        credentials = payload.get("Credentials")
        if not isinstance(credentials, dict):
            raise CramerApiError("Cognito returned no credentials")
        return credentials
