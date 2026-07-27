"""Time platform for Cramer AiConic — week-timer start times."""

from __future__ import annotations

from datetime import time as dt_time

from homeassistant.components.time import TimeEntity
from homeassistant.const import EntityCategory
from homeassistant.core import HomeAssistant
from homeassistant.helpers.entity_platform import AddConfigEntryEntitiesCallback

from .coordinator import CramerConfigEntry, CramerCoordinator
from .schedule_entity import CramerTimerEntity, slot_range


async def async_setup_entry(
    hass: HomeAssistant,
    entry: CramerConfigEntry,
    async_add_entities: AddConfigEntryEntitiesCallback,
) -> None:
    """Set up the schedule start times."""
    coordinator = entry.runtime_data
    if not coordinator.settings_enabled:
        return
    async_add_entities(
        CramerTimerStart(coordinator, device_id, index)
        for device_id in coordinator.data
        for index in slot_range()
    )


class CramerTimerStart(CramerTimerEntity, TimeEntity):
    """When a week timer opens its mowing window."""

    _attr_translation_key = "timer_start"
    _attr_entity_category = EntityCategory.CONFIG

    def __init__(
        self, coordinator: CramerCoordinator, device_id: str, index: int
    ) -> None:
        super().__init__(coordinator, device_id, index, "start")

    @property
    def native_value(self) -> dt_time | None:
        timer = self.timer
        if timer is None:
            return None
        return dt_time(hour=timer["hour"], minute=timer["minute"])

    async def async_set_value(self, value: dt_time) -> None:
        await self.coordinator.async_write_timer(
            self._device_id, self._index, hour=value.hour, minute=value.minute
        )
