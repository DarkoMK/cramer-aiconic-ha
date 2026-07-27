"""Lawn mower platform for Cramer AiConic."""

from __future__ import annotations

import logging
from typing import Any

import voluptuous as vol

from homeassistant.components.lawn_mower import (
    LawnMowerActivity,
    LawnMowerEntity,
    LawnMowerEntityFeature,
)
from homeassistant.core import HomeAssistant
from homeassistant.helpers import config_validation as cv, entity_platform
from homeassistant.helpers.entity_platform import AddConfigEntryEntitiesCallback

from . import protocol
from .coordinator import CramerConfigEntry, CramerCoordinator
from .entity import CramerEntity

_LOGGER = logging.getLogger(__name__)

SERVICE_PARK = "park"
SERVICE_START_ZONE = "start_mowing_options"
SERVICE_SET_SCHEDULE = "set_schedule"
SERVICE_CLEAR_SCHEDULE = "clear_schedule"
SERVICE_DRIVE = "drive"

DAY_OPTIONS = protocol.DAY_ORDER

#: SignedMainState -> LawnMowerActivity
ACTIVITY_MAP: dict[str, LawnMowerActivity] = {
    "cutting": LawnMowerActivity.MOWING,
    "secondary_area": LawnMowerActivity.MOWING,
    "spot_cutting": LawnMowerActivity.MOWING,
    "leaving": LawnMowerActivity.MOWING,
    "mapping": LawnMowerActivity.MOWING,
    "verification": LawnMowerActivity.MOWING,
    "searching": LawnMowerActivity.RETURNING,
    "transportation": LawnMowerActivity.RETURNING,
    "charging": LawnMowerActivity.DOCKED,
    "parked": LawnMowerActivity.DOCKED,
    "waiting": LawnMowerActivity.DOCKED,
    "idle": LawnMowerActivity.PAUSED,
    "paused": LawnMowerActivity.PAUSED,
    "power_up": LawnMowerActivity.DOCKED,
    "power_down": LawnMowerActivity.DOCKED,
    "error": LawnMowerActivity.ERROR,
    "fatal_error": LawnMowerActivity.ERROR,
    "alarm": LawnMowerActivity.ERROR,
    "recovery": LawnMowerActivity.ERROR,
    "disconnected": LawnMowerActivity.ERROR,
}


async def async_setup_entry(
    hass: HomeAssistant,
    entry: CramerConfigEntry,
    async_add_entities: AddConfigEntryEntitiesCallback,
) -> None:
    """Set up the lawn mower entities."""
    coordinator = entry.runtime_data
    async_add_entities(
        CramerLawnMower(coordinator, device_id) for device_id in coordinator.data
    )

    platform = entity_platform.async_get_current_platform()
    platform.async_register_entity_service(
        SERVICE_PARK,
        {
            vol.Optional("minutes"): vol.All(
                vol.Coerce(int), vol.Range(min=0, max=65534)
            )
        },
        "async_park",
    )
    platform.async_register_entity_service(
        SERVICE_SET_SCHEDULE,
        {
            vol.Required("timer_index"): vol.All(
                vol.Coerce(int), vol.Range(min=0, max=protocol.MAX_TIMER_INDEX)
            ),
            vol.Required("days"): vol.All(cv.ensure_list, [vol.In(DAY_OPTIONS)]),
            vol.Required("start_time"): cv.time,
            vol.Required("duration_minutes"): vol.All(
                vol.Coerce(int), vol.Range(min=1, max=65535)
            ),
            vol.Optional("enabled", default=True): cv.boolean,
        },
        "async_set_schedule",
    )
    platform.async_register_entity_service(
        SERVICE_CLEAR_SCHEDULE,
        {
            vol.Optional("timer_index"): vol.All(
                vol.Coerce(int), vol.Range(min=0, max=protocol.MAX_TIMER_INDEX)
            )
        },
        "async_clear_schedule",
    )
    platform.async_register_entity_service(
        SERVICE_DRIVE,
        {
            vol.Required("speed"): vol.All(
                vol.Coerce(int),
                vol.Range(
                    min=-protocol.DRIVE_SPEED_LIMIT, max=protocol.DRIVE_SPEED_LIMIT
                ),
            ),
            vol.Optional("angular_velocity", default=0): vol.All(
                vol.Coerce(int),
                vol.Range(
                    min=-protocol.DRIVE_ANGULAR_LIMIT, max=protocol.DRIVE_ANGULAR_LIMIT
                ),
            ),
            vol.Optional("set_waypoint", default=False): cv.boolean,
        },
        "async_drive",
    )
    platform.async_register_entity_service(
        SERVICE_START_ZONE,
        {
            vol.Optional("override_schedule", default=True): cv.boolean,
            vol.Optional("map_index"): vol.All(
                vol.Coerce(int), vol.Range(min=0, max=255)
            ),
        },
        "async_start_with_options",
    )


class CramerLawnMower(CramerEntity, LawnMowerEntity):
    """The mower itself."""

    _attr_name = None
    _attr_supported_features = (
        LawnMowerEntityFeature.START_MOWING
        | LawnMowerEntityFeature.PAUSE
        | LawnMowerEntityFeature.DOCK
    )

    def __init__(self, coordinator: CramerCoordinator, device_id: str) -> None:
        super().__init__(coordinator, device_id)
        self._attr_unique_id = device_id

    @property
    def activity(self) -> LawnMowerActivity | None:
        state = self.mower.main_state
        if state is None:
            return None
        return ACTIVITY_MAP.get(state, LawnMowerActivity.ERROR)

    @property
    def extra_state_attributes(self) -> dict[str, Any]:
        mower = self.mower
        return {
            "raw_state": mower.main_state,
            "raw_state_code": mower.main_state_code,
            "sub_state": mower.sub_state,
            "status_flags": mower.status_flags,
            "next_start_stop_source": mower.next_start_stop_source,
            "site_name": mower.site_name,
            "map_name": mower.map_name,
            "operation_mode": mower.operation_mode,
        }

    async def async_start_mowing(self) -> None:
        """Start mowing the whole map, ignoring the week timer."""
        await self.coordinator.async_send(
            self._device_id,
            protocol.cmd_start_mower(message_id=self.coordinator.build_message_id()),
            "start mowing",
        )

    async def async_pause(self) -> None:
        """Pause the mower where it stands."""
        await self.coordinator.async_send(
            self._device_id,
            protocol.cmd_pause_mower(message_id=self.coordinator.build_message_id()),
            "pause",
        )

    async def async_dock(self) -> None:
        """Send the mower back to the charging station."""
        await self.coordinator.async_send(
            self._device_id,
            protocol.cmd_park_mower(message_id=self.coordinator.build_message_id()),
            "dock",
        )

    # -- custom services ----------------------------------------------------
    async def async_park(self, minutes: int | None = None) -> None:
        """Park in the charging station, optionally for a fixed duration."""
        park_time = 0xFFFF if minutes is None else minutes
        await self.coordinator.async_send(
            self._device_id,
            protocol.cmd_park_mower(
                park_time, message_id=self.coordinator.build_message_id()
            ),
            "park",
        )

    async def async_set_schedule(
        self,
        timer_index: int,
        days: list[str],
        start_time,
        duration_minutes: int,
        enabled: bool = True,
    ) -> None:
        """Write one of the mower's week timers."""
        site, map_name = self.coordinator.site_and_map(self._device_id)
        await self.coordinator.async_send(
            self._device_id,
            protocol.cmd_set_week_timer(
                timer_index,
                site,
                map_name,
                start_time.hour,
                start_time.minute,
                days,
                duration_minutes,
                enabled=enabled,
                message_id=self.coordinator.build_message_id(),
            ),
            "set the schedule",
            refresh_settings=True,
        )

    async def async_clear_schedule(self, timer_index: int | None = None) -> None:
        """Clear one week timer, or all of them when no index is given."""
        index = protocol.TIMER_INDEX_ALL if timer_index is None else timer_index
        await self.coordinator.async_send(
            self._device_id,
            protocol.cmd_clear_week_timer(
                index, message_id=self.coordinator.build_message_id()
            ),
            "clear the schedule",
            refresh_settings=True,
        )

    async def async_drive(
        self, speed: int, angular_velocity: int = 0, set_waypoint: bool = False
    ) -> None:
        """Nudge the mower manually.

        The mower only accepts this in mapping/manual mode and coasts to a stop
        once commands stop arriving, so one call moves it a little rather than
        sending it somewhere. Call it repeatedly to drive continuously.
        """
        await self.coordinator.async_send(
            self._device_id,
            protocol.cmd_drive_mower(
                speed,
                angular_velocity,
                protocol.WAYPOINT_HERE if set_waypoint else protocol.WAYPOINT_NONE,
                message_id=self.coordinator.build_message_id(),
            ),
            "drive",
        )

    async def async_start_with_options(
        self, override_schedule: bool = True, map_index: int | None = None
    ) -> None:
        """Start mowing with explicit schedule/map options."""
        await self.coordinator.async_send(
            self._device_id,
            protocol.cmd_start_mower(
                override_schedule=0xFFFF if override_schedule else 0,
                map_settings_index=254 if map_index is None else map_index,
                message_id=self.coordinator.build_message_id(),
            ),
            "start mowing",
        )
