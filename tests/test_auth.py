"""Tests for the token lifecycle.

The Cramer GUC server issues single-use, rotating refresh tokens: presenting
one returns a new one and revokes the old. These tests model that server
faithfully — reusing a spent token yields ``invalid_grant`` — because that is
exactly the behaviour that broke the previous integration.
"""

import asyncio
import base64
import json
import time

import pytest


def make_jwt(sub: str = "user-1", expires_in: int = 7200) -> str:
    claims = {"sub": sub, "exp": int(time.time()) + expires_in}
    payload = base64.urlsafe_b64encode(json.dumps(claims).encode()).rstrip(b"=")
    return f"header.{payload.decode()}.signature"


class FakeGuc:
    """A GUC token endpoint with single-use rotating refresh tokens."""

    def __init__(self, *, password: str = "correct-horse") -> None:
        self.password = password
        self.valid_refresh: str | None = None
        self.spent: set[str] = set()
        self.password_logins = 0
        self.refreshes = 0
        self.rejected_reuse = 0
        self._counter = 0
        self.fail_next_refresh_with: Exception | None = None

    def _issue(self) -> dict:
        self._counter += 1
        if self.valid_refresh:
            self.spent.add(self.valid_refresh)
        self.valid_refresh = f"refresh-{self._counter}"
        return {
            "access_token": make_jwt(),
            "refresh_token": self.valid_refresh,
            "expires_in": 7200,
            "token_type": "Bearer",
        }

    async def handle(self, url: str, data: dict, *, auth_error) -> dict:
        grant = data["grant_type"]
        if grant == "password":
            if data["password"] != self.password:
                raise auth_error("400: invalid_grant")
            self.password_logins += 1
            return self._issue()

        if grant == "refresh_token":
            if self.fail_next_refresh_with is not None:
                err = self.fail_next_refresh_with
                self.fail_next_refresh_with = None
                raise err
            token = data["refresh_token"]
            if token in self.spent or token != self.valid_refresh:
                self.rejected_reuse += 1
                raise auth_error('400: {"error":"invalid_grant"}')
            self.refreshes += 1
            return self._issue()

        raise AssertionError(f"unexpected grant_type {grant}")


@pytest.fixture
def guc():
    return FakeGuc()


@pytest.fixture
def make_api(api_module, guc):
    """Build an API client wired to the fake GUC, tracking persisted tokens."""

    def _make(refresh_token: str | None = None, password: str = "correct-horse"):
        persisted: list[str | None] = []

        async def on_tokens_updated(token, region):
            persisted.append(token)

        api = api_module.CramerAiConicApi(
            session=None,
            username="user@example.com",
            password=password,
            refresh_token=refresh_token,
            on_tokens_updated=on_tokens_updated,
        )
        api._region_resolved = True  # skip the network region lookup

        async def _post_form(url, data):
            return await guc.handle(url, data, auth_error=api_module.CramerAuthError)

        api._post_form = _post_form
        return api, persisted

    return _make


@pytest.mark.asyncio
async def test_first_call_logs_in_with_password(make_api, guc):
    api, persisted = make_api()
    token = await api.async_ensure_token()
    assert token
    assert guc.password_logins == 1
    assert persisted == ["refresh-1"]


@pytest.mark.asyncio
async def test_valid_token_is_reused(make_api, guc):
    api, _ = make_api()
    first = await api.async_ensure_token()
    second = await api.async_ensure_token()
    assert first == second
    assert guc.password_logins == 1
    assert guc.refreshes == 0


@pytest.mark.asyncio
async def test_expiring_token_is_refreshed_before_it_dies(make_api, guc, api_module):
    api, persisted = make_api()
    await api.async_ensure_token()
    # Walk the clock to inside the safety margin but before actual expiry.
    api._expires_at = time.monotonic() + api_module.TOKEN_EXPIRY_MARGIN - 1

    await api.async_ensure_token()

    assert guc.refreshes == 1
    assert guc.password_logins == 1
    assert persisted[-1] == "refresh-2"


@pytest.mark.asyncio
async def test_rotated_refresh_token_is_persisted_every_time(make_api, guc):
    api, persisted = make_api()
    await api.async_ensure_token()
    for _ in range(4):
        api._expires_at = 0
        await api.async_ensure_token()

    assert guc.refreshes == 4
    assert persisted == ["refresh-1", "refresh-2", "refresh-3", "refresh-4", "refresh-5"]
    # Every persisted value must be the one the server considers current.
    assert persisted[-1] == guc.valid_refresh


@pytest.mark.asyncio
async def test_concurrent_callers_never_burn_the_same_refresh_token(make_api, guc):
    """The regression that killed the old integration.

    Twenty simultaneous requests must trigger exactly one refresh; two
    concurrent refreshes would spend the same single-use token and the loser
    would be permanently unauthenticated.
    """
    api, _ = make_api()
    await api.async_ensure_token()
    api._expires_at = 0

    tokens = await asyncio.gather(*(api.async_ensure_token() for _ in range(20)))

    assert len(set(tokens)) == 1
    assert guc.refreshes == 1
    assert guc.rejected_reuse == 0
    assert guc.password_logins == 1


@pytest.mark.asyncio
async def test_revoked_refresh_token_falls_back_to_password_login(make_api, guc):
    """A stale refresh token from disk must self-heal, not brick the entry."""
    api, persisted = make_api(refresh_token="refresh-from-a-previous-life")

    token = await api.async_ensure_token()

    assert token
    assert guc.rejected_reuse == 1
    assert guc.password_logins == 1
    assert persisted[-1] == guc.valid_refresh


@pytest.mark.asyncio
async def test_network_failure_during_refresh_falls_back_to_password(
    make_api, guc, api_module
):
    api, _ = make_api()
    await api.async_ensure_token()
    api._expires_at = 0
    guc.fail_next_refresh_with = api_module.CramerApiError("connection reset")

    await api.async_ensure_token()

    assert guc.password_logins == 2


@pytest.mark.asyncio
async def test_spent_refresh_token_is_dropped_before_reuse(make_api, guc, api_module):
    """After a refresh the old token is dead; it must never be presented again."""
    api, _ = make_api()
    await api.async_ensure_token()
    api._expires_at = 0
    await api.async_ensure_token()
    spent = "refresh-1"

    api._expires_at = 0
    await api.async_ensure_token()

    assert spent in guc.spent
    assert guc.rejected_reuse == 0


@pytest.mark.asyncio
async def test_wrong_password_raises_auth_error(make_api, api_module):
    api, _ = make_api(password="wrong")
    with pytest.raises(api_module.CramerAuthError):
        await api.async_ensure_token()


@pytest.mark.asyncio
async def test_force_bypasses_the_validity_check(make_api, guc):
    api, _ = make_api()
    await api.async_ensure_token()
    await api.async_ensure_token(force=True)
    assert guc.refreshes == 1


@pytest.mark.asyncio
async def test_user_id_is_extracted_from_the_access_token(make_api):
    api, _ = make_api()
    await api.async_ensure_token()
    assert api.user_id == "user-1"


@pytest.mark.asyncio
async def test_persistence_failure_does_not_break_authentication(
    api_module, guc
):
    """Storage problems must not take authentication down with them."""

    async def exploding_persist(token, region):
        raise RuntimeError("disk on fire")

    api = api_module.CramerAiConicApi(
        session=None,
        username="user@example.com",
        password="correct-horse",
        on_tokens_updated=exploding_persist,
    )
    api._region_resolved = True

    async def _post_form(url, data):
        return await guc.handle(url, data, auth_error=api_module.CramerAuthError)

    api._post_form = _post_form

    assert await api.async_ensure_token()
