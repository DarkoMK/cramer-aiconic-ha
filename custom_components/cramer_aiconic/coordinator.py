"""Polling coordinator for Cramer AiConic mowers.

Three data sources are combined, in order of how cheap they are:

* ``get_datapoints`` over HTTP every poll. The mower pushes datapoint 746
  itself roughly every 30 s, so state and battery are always fresh without
  asking it anything.
* A read command followed by an HTTP datapoint read, for the handful of
  datapoints the cloud caches but the mower does not push (81, 471, 509).
* A short MQTT session every few minutes, for the settings the cloud answers
  but never caches — light modes, sound, obstacle handling, speed, selected
  site, radio status and GPS.
"""

from __future__ import annotations

import asyncio
import logging
import time
from dataclasses import dataclass, field
from datetime import datetime, timedelta
from typing import Any

from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant
from homeassistant.exceptions import ConfigEntryAuthFailed, HomeAssistantError
from homeassistant.helpers import device_registry as dr
from homeassistant.helpers.update_coordinator import DataUpdateCoordinator, UpdateFailed
from homeassistant.util import dt as dt_util

from . import protocol
from .api import (
    CramerAiConicApi,
    CramerApiError,
    CramerAuthError,
    CramerDevice,
    CramerRateLimitError,
)
from .const import (
    COMMAND_PACING,
    DOMAIN,
    FAILURES_BEFORE_UNAVAILABLE,
    ENRICH_EVERY_CYCLES,
    MQTT_RESPONSE_TIMEOUT,
    POST_COMMAND_REFRESH_DELAY,
    SETTINGS_REFRESH_SECONDS,
)
from .mqtt_link import CramerMqttError, build_reader

_LOGGER = logging.getLogger(__name__)

CramerConfigEntry = ConfigEntry["CramerCoordinator"]


@dataclass
class MowerState:
    """Everything the integration knows about one mower."""

    device: CramerDevice
    # -- from the HTTP datapoints
    main_state: str | None = None
    main_state_code: int | None = None
    sub_state: int | None = None
    battery: int | None = None
    status_flags: list[str] = field(default_factory=list)
    next_start_stop: datetime | None = None
    next_start_stop_source: str | None = None
    signal_quality: int | None = None
    wireless_status: int | None = None
    backend_notification: int | None = None
    cutting_height: int | None = None
    default_cutting_height: int | None = None
    operation_mode: str | None = None
    site_name: str | None = None
    map_name: str | None = None
    last_status_push: datetime | None = None
    # -- position
    latitude: float | None = None
    longitude: float | None = None
    gps_hdop: int | None = None
    # -- from the MQTT settings pass
    front_light: str | None = None
    rear_light: str | None = None
    sound: str | None = None
    obstacle_handling: str | None = None
    default_speed: int | None = None
    default_speed_kind: str | None = None
    selected_site: str | None = None
    available_sites: list[str] = field(default_factory=list)
    auto_update: bool | None = None
    lte_signal: int | None = None
    sim_card_status: int | None = None
    rtk_connection_status: int | None = None
    week_timers: list[dict[str, Any]] = field(default_factory=list)
    #: Slot edits applied locally but not yet confirmed by a settings read.
    #: Reading the schedule back takes the best part of a minute, so without
    #: this a second edit would be built from pre-edit values and silently
    #: undo the first.
    pending_timers: dict[int, dict[str, Any]] = field(default_factory=dict)
    area_cut: int | None = None
    area_remaining: int | None = None
    estimated_remaining_minutes: int | None = None
    firmware_version: str | None = None
    zones: list[dict[str, Any]] = field(default_factory=list)
    settings_updated: datetime | None = None
    settings_error: str | None = None
    #: When the in-flight settings read began, so a read cannot discard an
    #: edit made after it started. Per mower, because two mowers read
    #: independently.
    settings_read_started: float = 0.0

    @property
    def enabled_timers(self) -> list[dict[str, Any]]:
        return [t for t in self.week_timers if t.get("enabled")]

    @property
    def active_zone(self) -> str | None:
        """The map the mower is currently set to work."""
        return self.map_name

    def timer_is_defined(self, index: int) -> bool:
        """Whether the mower itself holds a timer in this slot."""
        return any(t.get("index") == index for t in self.week_timers)

    def timer(self, index: int) -> dict[str, Any] | None:
        """The week timer in a slot, including edits not yet read back."""
        confirmed = next(
            (t for t in self.week_timers if t.get("index") == index), None
        )
        pending = self.pending_timers.get(index)
        if pending is None:
            return confirmed
        merged = dict(confirmed or {"index": index, "map_index": 0})
        merged.update(pending["values"])
        return merged

    @property
    def available(self) -> bool:
        return self.main_state is not None

    @property
    def is_online(self) -> bool:
        return self.device.is_online

    @property
    def in_charging_station(self) -> bool:
        return "in_charging_station" in self.status_flags

    @property
    def has_rtk_fix(self) -> bool:
        return "rtk_fix" in self.status_flags

    @property
    def is_active(self) -> bool:
        return self.main_state in protocol.ACTIVE_STATES

    @property
    def is_returning(self) -> bool:
        return self.main_state in protocol.DOCKING_STATES

    @property
    def is_error(self) -> bool:
        return self.main_state in protocol.ERROR_STATES


def _parse_timestamp(value: str | None) -> datetime | None:
    if not value:
        return None
    return dt_util.parse_datetime(value)


class CramerCoordinator(DataUpdateCoordinator[dict[str, MowerState]]):
    """Polls the Cramer cloud for mower state."""

    config_entry: CramerConfigEntry

    def __init__(
        self,
        hass: HomeAssistant,
        entry: CramerConfigEntry,
        api: CramerAiConicApi,
        scan_interval: int,
        settings_enabled: bool = True,
    ) -> None:
        super().__init__(
            hass,
            _LOGGER,
            name=DOMAIN,
            config_entry=entry,
            update_interval=timedelta(seconds=scan_interval),
        )
        self.api = api
        self.settings_enabled = settings_enabled
        self._devices: list[CramerDevice] = []
        self._cycle = 0
        self._message_id = 0
        self._states: dict[str, MowerState] = {}
        self._settings_lock = asyncio.Lock()
        self._settings_last_run: float = 0.0
        self.options_snapshot: dict[str, Any] = {}
        self.consecutive_failures = 0

    # -- helpers ------------------------------------------------------------
    def _next_message_id(self) -> int:
        """Frame message ids cycle 1..255; 0 reads as 'unset'."""
        self._message_id = (self._message_id % 255) + 1
        return self._message_id

    def build_message_id(self) -> int:
        return self._next_message_id()

    async def _async_load_devices(self) -> None:
        """Refresh the account's device list.

        The list changes almost never, so a throttled refresh must not take
        the poll down with it — the cached list is still perfectly good. Only
        a first load, where there is nothing cached, is fatal.
        """
        try:
            devices = await self.api.async_get_devices()
        except CramerAuthError:
            raise
        except CramerApiError as err:
            if not self._devices:
                raise
            _LOGGER.debug("Device list refresh failed, keeping cached list: %s", err)
            return
        mowers = [
            d for d in devices if d.product_type and d.product_type.upper().startswith("RLM")
        ]
        self._devices = mowers or devices

    # -- update -------------------------------------------------------------
    async def _async_update_data(self) -> dict[str, MowerState]:
        try:
            if not self._devices or self._cycle % ENRICH_EVERY_CYCLES == 0:
                await self._async_load_devices()

            if not self._devices:
                raise UpdateFailed("No devices found on the Cramer account")

            for device in self._devices:
                await self._async_update_device(device)

            self._cycle += 1
            self.consecutive_failures = 0
        except CramerAuthError as err:
            raise ConfigEntryAuthFailed(str(err)) from err
        except CramerRateLimitError as err:
            self._note_failure()
            raise UpdateFailed(f"Cramer cloud is throttling requests: {err}") from err
        except CramerApiError as err:
            self._note_failure()
            raise UpdateFailed(str(err)) from err

        # The settings pass is best-effort and must never fail the poll.
        if self.settings_enabled and self._settings_due():
            self.config_entry.async_create_background_task(
                self.hass, self._async_refresh_settings(), f"{DOMAIN}_settings"
            )

        return self._states

    def _note_failure(self) -> None:
        """Count a failed poll and, at the tolerance limit, tell the entities.

        Home Assistant only notifies listeners on the *first* failure — once
        ``last_update_success`` is already False it stops. Without a nudge at
        the crossing point the entities would never re-evaluate, so the
        tolerance window would silently become "available forever with stale
        data", which is worse than flapping.
        """
        self.consecutive_failures += 1
        if self.consecutive_failures == FAILURES_BEFORE_UNAVAILABLE + 1:
            self.hass.loop.call_soon(self.async_update_listeners)

    def _settings_due(self) -> bool:
        return time.monotonic() - self._settings_last_run >= SETTINGS_REFRESH_SECONDS

    async def _async_update_device(self, device: CramerDevice) -> None:
        state = self._states.get(device.device_id) or MowerState(device=device)
        state.device = device

        datapoints = await self.api.async_get_datapoints(
            device.product_id, device.device_id, protocol.POLL_DATAPOINTS
        )
        decoded = self._decode(datapoints, protocol.DATAPOINT_DECODERS)
        self._apply_state(state, decoded)
        self._states[device.device_id] = state

        if self._cycle % ENRICH_EVERY_CYCLES == 0:
            await self._async_request_enrichment(device)
            await self._async_update_position(state)

    @staticmethod
    def _decode(
        datapoints: dict[int, tuple[str, str]], decoders: dict[int, Any]
    ) -> dict[int, dict[str, Any]]:
        decoded: dict[int, dict[str, Any]] = {}
        for index, (hex_data, timestamp) in datapoints.items():
            decoder = decoders.get(index)
            if decoder is None:
                continue
            try:
                values = decoder(bytes.fromhex(hex_data))
            except (ValueError, IndexError) as err:
                _LOGGER.debug("Could not decode datapoint %s (%s): %s", index, hex_data, err)
                continue
            values["_timestamp"] = timestamp
            decoded[index] = values
        return decoded

    def _apply_state(self, state: MowerState, decoded: dict[int, dict[str, Any]]) -> None:
        """Merge the HTTP datapoints into the mower state.

        Datapoint 746 is pushed by the mower every ~30 s and is the freshest
        source for state and battery. Datapoint 81 carries richer detail but
        only refreshes when asked, so it must not overwrite the push.
        """
        push = decoded.get(protocol.DP_STATUS_PUSH)
        status = decoded.get(protocol.DP_MOWER_STATUS)

        if primary := (push or status):
            state.main_state = primary["main_state"]
            state.main_state_code = primary["main_state_code"]
            state.battery = primary["battery"]
            state.next_start_stop_source = primary["next_start_stop_source"]
            state.backend_notification = primary["backend_notification"]
            nss = primary.get("next_start_stop")
            state.next_start_stop = dt_util.utc_from_timestamp(nss) if nss else None
        if push:
            state.last_status_push = _parse_timestamp(push.get("_timestamp"))

        if status:
            state.sub_state = status["sub_state"]
            state.status_flags = status["status_flags"]
            state.signal_quality = status["signal_quality"]
            state.wireless_status = status["wireless_status"]
            if not push:
                state.last_status_push = _parse_timestamp(status.get("_timestamp"))

        if height := decoded.get(protocol.DP_CUTTING_HEIGHT):
            state.cutting_height = height["cutting_height"]
            state.default_cutting_height = height["default_cutting_height"]

        if mode := decoded.get(protocol.DP_OPERATION_MODE):
            state.operation_mode = mode["operation_mode"]
            state.site_name = mode["site_name"] or None
            state.map_name = mode["map_name"] or None

        self._apply_gnss(state, decoded.get(protocol.DP_GNSS))

    @staticmethod
    def _apply_gnss(state: MowerState, gnss: dict[str, Any] | None) -> None:
        if not gnss:
            return
        # A mower with no fix reports 0/0; that is the Atlantic, not the lawn.
        if gnss["latitude"] or gnss["longitude"]:
            state.latitude = gnss["latitude"]
            state.longitude = gnss["longitude"]
            state.gps_hdop = gnss["hdop"]

    async def _async_request_enrichment(self, device: CramerDevice) -> None:
        """Ask the mower to refresh the datapoints the cloud caches."""
        for parameter_id in protocol.ENRICH_COMMANDS:
            try:
                await self.api.async_send_command(
                    device.product_id,
                    device.device_id,
                    protocol.cmd_get(parameter_id, self._next_message_id()),
                )
            except CramerRateLimitError:
                _LOGGER.debug("Throttled while refreshing datapoint %s", parameter_id)
                return
            except CramerApiError as err:
                _LOGGER.debug("Refresh of datapoint %s failed: %s", parameter_id, err)
            await asyncio.sleep(COMMAND_PACING)

    async def _async_update_position(self, state: MowerState) -> None:
        """Read the position the cloud keeps for the mower.

        The GNSS datapoint (95) is answered over MQTT but never cached, so the
        HTTP fallback is the cloud's own last-known-info record.
        """
        if state.latitude is not None and state.longitude is not None:
            return
        try:
            info = await self.api.async_last_known_info(
                state.device.product_id, state.device.device_id
            )
        except CramerApiError as err:
            _LOGGER.debug("last-known-info unavailable: %s", err)
            return
        try:
            latitude = float(info["latitude"])
            longitude = float(info["longitude"])
        except (KeyError, TypeError, ValueError):
            return
        if latitude or longitude:
            state.latitude = latitude
            state.longitude = longitude

    # -- MQTT settings pass -------------------------------------------------
    async def _async_refresh_settings(self) -> None:
        """Read the settings the cloud will not cache, over a brief MQTT session."""
        if self._settings_lock.locked():
            return
        async with self._settings_lock:
            self._settings_last_run = time.monotonic()
            _LOGGER.debug("Starting MQTT settings pass for %d device(s)", len(self._devices))
            for device in self._devices:
                state = self._states.get(device.device_id)
                if state is None:
                    continue
                try:
                    await self._async_read_settings(device, state)
                    self._publish_firmware(state)
                    state.settings_error = None
                    _LOGGER.debug("Settings pass for %s completed", device.name)
                except (CramerMqttError, CramerApiError, OSError) as err:
                    state.settings_error = str(err)
                    _LOGGER.warning("Settings refresh failed for %s: %s", device.name, err)
                except Exception:  # noqa: BLE001 - never let this kill the task
                    state.settings_error = "unexpected error"
                    _LOGGER.exception("Unexpected error during settings refresh")
            self.async_update_listeners()

    async def _async_read_settings(self, device: CramerDevice, state: MowerState) -> None:
        state.settings_read_started = time.monotonic()
        mqtt_info = await self.api.async_get_mqtt_info()
        credentials = await self.api.async_get_aws_credentials(mqtt_info)
        reader = await self.hass.async_add_executor_job(
            build_reader, mqtt_info, credentials, device.device_id
        )

        try:
            await self.hass.async_add_executor_job(reader.connect)
            wanted: set[int] = set()
            for parameter_id in protocol.MQTT_SETTING_COMMANDS:
                payload = self._settings_payload(parameter_id, state)
                if payload is None:
                    continue
                try:
                    await self.api.async_send_command(
                        device.product_id, device.device_id, payload
                    )
                except CramerApiError as err:
                    _LOGGER.debug("Settings request %s failed: %s", parameter_id, err)
                    continue
                wanted.add(parameter_id + 1)
                # The mower silently drops requests that arrive back to back.
                await asyncio.sleep(COMMAND_PACING)

            await self.hass.async_add_executor_job(
                reader.wait_for, wanted, MQTT_RESPONSE_TIMEOUT
            )
            frames = await self.hass.async_add_executor_job(reader.collected)
        finally:
            await self.hass.async_add_executor_job(reader.disconnect)

        _LOGGER.debug(
            "Settings pass collected %d frame(s): %s", len(frames), sorted(frames)
        )
        if not frames:
            raise CramerMqttError("No responses received from the mower")

        self._apply_settings(state, frames)
        state.settings_updated = dt_util.utcnow()

    def _settings_payload(self, parameter_id: int, state: MowerState) -> str | None:
        """Build the read command, or None if it cannot be asked for yet.

        Reading the week timers needs the site and map names, which come from
        the operation-mode datapoint on the normal poll.
        """
        message_id = self._next_message_id()
        if parameter_id == protocol.P_GET_ALL_WEEK_TIMERS:
            if not state.site_name or not state.map_name:
                return None
            return protocol.cmd_get_week_timers(
                state.site_name, state.map_name, message_id
            )
        if parameter_id == protocol.P_GET_MAPS:
            if not state.site_name:
                return None
            return protocol.cmd_get_maps(state.site_name, 0, message_id)
        return protocol.cmd_get(parameter_id, message_id)

    def _apply_settings(self, state: MowerState, frames: dict[int, bytes]) -> None:
        decoded: dict[int, dict[str, Any]] = {}
        for parameter_id, body in frames.items():
            decoder = protocol.MQTT_DECODERS.get(parameter_id)
            if decoder is None:
                continue
            try:
                decoded[parameter_id] = decoder(body)
            except (ValueError, IndexError) as err:
                _LOGGER.debug("Could not decode MQTT frame %s: %s", parameter_id, err)

        if value := decoded.get(protocol.DP_FRONT_LIGHT):
            state.front_light = value["front_light"]
        if value := decoded.get(protocol.DP_REAR_LIGHT):
            state.rear_light = value["rear_light"]
        if value := decoded.get(protocol.DP_SOUND):
            state.sound = value["sound"]
        if value := decoded.get(protocol.DP_OBSTACLE_HANDLING):
            state.obstacle_handling = value["obstacle_handling"]
        if value := decoded.get(protocol.DP_DEFAULT_SPEED):
            state.default_speed = value["default_speed"]
            state.default_speed_kind = value["default_speed_kind"]
        if value := decoded.get(protocol.DP_SELECTED_SITE):
            state.selected_site = value["selected_site"]
        if value := decoded.get(protocol.DP_SITE_NAMES):
            state.available_sites = value["site_names"]
        if value := decoded.get(protocol.DP_AUTO_UPDATE):
            state.auto_update = value["auto_update"]
        if value := decoded.get(protocol.DP_WEEK_TIMERS):
            state.week_timers = value["week_timers"]
            # Anything written before this read started is now confirmed (or
            # was rejected); either way the mower's answer wins.
            # Confirmed by this read, so the mower's answer wins — except for
            # drafts that were never sent, and edits made after the read began.
            state.pending_timers = {
                index: entry
                for index, entry in state.pending_timers.items()
                if not entry["sent"] or entry["at"] >= state.settings_read_started
            }
        if value := decoded.get(protocol.DP_MAP_COVERAGE):
            state.area_cut = value["area_cut"]
            state.area_remaining = value["area_remaining"]
            state.estimated_remaining_minutes = value["estimated_remaining_minutes"]
        if value := decoded.get(protocol.DP_SW_PACKAGE):
            state.firmware_version = value["firmware_version"]
        if value := decoded.get(protocol.DP_MAPS):
            state.zones = value["maps"]
        if value := decoded.get(protocol.DP_WIRELESS):
            state.lte_signal = value["lte_signal"]
            state.sim_card_status = value["sim_card_status"]
            state.rtk_connection_status = value["rtk_connection_status"]
        self._apply_gnss(state, decoded.get(protocol.DP_GNSS))

        # A settings pass also carries a fresh full status frame.
        http_shaped = {
            index: {**values, "_timestamp": ""}
            for index, values in decoded.items()
            if index in protocol.DATAPOINT_DECODERS
        }
        if http_shaped:
            self._apply_state(state, http_shaped)

    def _publish_firmware(self, state: MowerState) -> None:
        """Show the firmware the mower reports on its device page."""
        if not state.firmware_version:
            return
        registry = dr.async_get(self.hass)
        entry = registry.async_get_device(
            identifiers={(DOMAIN, state.device.device_id)}
        )
        if entry is not None and entry.sw_version != state.firmware_version:
            registry.async_update_device(
                entry.id, sw_version=state.firmware_version
            )

    async def async_write_timer(
        self,
        device_id: str,
        index: int,
        *,
        hour: int | None = None,
        minute: int | None = None,
        days: list[str] | None = None,
        duration_minutes: int | None = None,
        enabled: bool | None = None,
    ) -> None:
        """Rewrite one week timer, keeping whatever was not passed in.

        The mower has no partial-update command, so every edit sends the whole
        slot; the unspecified fields come from the last values it reported.
        """
        state = self._states.get(device_id)
        if state is None:
            raise HomeAssistantError(f"Unknown mower {device_id}")
        site, map_name = self.site_and_map(device_id)
        current = state.timer(index) or {}

        merged = {
            "hour": current.get("hour", 0) if hour is None else hour,
            "minute": current.get("minute", 0) if minute is None else minute,
            "days": list(current.get("days", [])) if days is None else list(days),
            "duration_minutes": (
                current.get("duration_minutes", 0)
                if duration_minutes is None
                else duration_minutes
            ),
            "enabled": current.get("enabled", False) if enabled is None else enabled,
        }
        merged["start"] = f"{merged['hour']:02d}:{merged['minute']:02d}"

        # Filling in a blank slot takes several edits — days, then a time, then
        # a duration. Sending after the first one would leave a zero-length
        # timer sitting on the mower, so hold the draft locally until the slot
        # describes something the mower can actually run.
        defined = state.timer_is_defined(index)
        complete = bool(merged["days"]) and merged["duration_minutes"] > 0
        send = defined or complete

        state.pending_timers[index] = {
            "values": merged,
            "at": time.monotonic(),
            "sent": send,
        }
        self.async_update_listeners()

        if not send:
            _LOGGER.debug(
                "Timer %s is still incomplete (days=%s, duration=%s); holding the "
                "draft until it can be written",
                index + 1,
                merged["days"],
                merged["duration_minutes"],
            )
            return

        payload = protocol.cmd_set_week_timer(
            index,
            site,
            map_name,
            merged["hour"],
            merged["minute"],
            merged["days"],
            merged["duration_minutes"],
            enabled=merged["enabled"],
            message_id=self._next_message_id(),
        )
        await self.async_send(
            device_id, payload, f"update timer {index + 1}", refresh_settings=True
        )

    def site_and_map(self, device_id: str) -> tuple[str, str]:
        """Site and map names for a mower, needed by the schedule commands."""
        state = self._states.get(device_id)
        if state is None or not state.site_name or not state.map_name:
            raise HomeAssistantError(
                "The mower's site and map are not known yet; try again once the "
                "integration has completed a settings read"
            )
        return state.site_name, state.map_name

    def forget_timer_draft(self, device_id: str, index: int | None) -> None:
        """Drop local drafts after the mower has been told to clear a slot."""
        state = self._states.get(device_id)
        if state is None:
            return
        if index is None:
            state.pending_timers.clear()
        else:
            state.pending_timers.pop(index, None)

    async def async_refresh_settings_now(self) -> None:
        """Force a settings read (used after changing one)."""
        self._settings_last_run = 0.0
        await self._async_refresh_settings()

    # -- commands -----------------------------------------------------------
    async def async_send(
        self,
        device_id: str,
        payload_hex: str,
        action: str,
        *,
        refresh_settings: bool = False,
        read_back: int | None = None,
    ) -> None:
        """Send a command frame to a mower and schedule a state refresh.

        ``read_back`` is the parameter id of the matching read command. The
        cloud re-caches a datapoint only when the mower answers a read, so a
        write on its own leaves the cached copy at its pre-write value; without
        the read-back the refresh below would restate the old setting.
        """
        state = self._states.get(device_id)
        if state is None:
            raise HomeAssistantError(f"Unknown mower {device_id}")

        try:
            online = await self.api.async_send_command(
                state.device.product_id, device_id, payload_hex
            )
        except CramerAuthError as err:
            raise ConfigEntryAuthFailed(str(err)) from err
        except CramerApiError as err:
            raise HomeAssistantError(f"Could not {action}: {err}") from err

        if not online:
            raise HomeAssistantError(f"Could not {action}: the mower is offline")

        self.config_entry.async_create_background_task(
            self.hass,
            self._async_delayed_refresh(
                refresh_settings, read_back=read_back, device_id=device_id
            ),
            f"{DOMAIN}_refresh_after_{action}",
        )

    async def _async_delayed_refresh(
        self,
        settings: bool = False,
        *,
        read_back: int | None = None,
        device_id: str | None = None,
    ) -> None:
        if read_back is not None and device_id is not None:
            # The mower drops requests that arrive on the heels of another, so
            # let the write land before asking for the value back.
            await asyncio.sleep(COMMAND_PACING)
            await self._async_read_back(device_id, read_back)
        await asyncio.sleep(POST_COMMAND_REFRESH_DELAY)
        if settings and self.settings_enabled:
            await self.async_refresh_settings_now()
        await self.async_request_refresh()

    async def _async_read_back(self, device_id: str, parameter_id: int) -> None:
        """Ask the mower to re-report one datapoint, so the cloud re-caches it."""
        state = self._states.get(device_id)
        if state is None:
            return
        try:
            await self.api.async_send_command(
                state.device.product_id,
                device_id,
                protocol.cmd_get(parameter_id, self._next_message_id()),
            )
        except CramerApiError as err:
            # Best effort: the next enrichment cycle will refresh it anyway.
            _LOGGER.debug("Read-back of parameter %s failed: %s", parameter_id, err)
