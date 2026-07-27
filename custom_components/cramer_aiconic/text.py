"""Text platform for Cramer AiConic — the weekdays a timer runs on.

Home Assistant has no multi-select entity, so the day set is exposed as a
comma-separated list. It round-trips exactly and stays automatable.
"""

from __future__ import annotations

from homeassistant.components.text import TextEntity, TextMode
from homeassistant.const import EntityCategory
from homeassistant.core import HomeAssistant
from homeassistant.exceptions import ServiceValidationError
from homeassistant.helpers.entity_platform import AddConfigEntryEntitiesCallback

from . import protocol
from .coordinator import CramerConfigEntry, CramerCoordinator
from .schedule_entity import CramerTimerEntity, slot_range


async def async_setup_entry(
    hass: HomeAssistant,
    entry: CramerConfigEntry,
    async_add_entities: AddConfigEntryEntitiesCallback,
) -> None:
    """Set up the schedule day pickers."""
    coordinator = entry.runtime_data
    if not coordinator.settings_enabled:
        return
    async_add_entities(
        CramerTimerDays(coordinator, device_id, index)
        for device_id in coordinator.data
        for index in slot_range()
    )


class CramerTimerDays(CramerTimerEntity, TextEntity):
    """Weekdays for a week timer, e.g. ``mon,wed,fri``."""

    _attr_translation_key = "timer_days"
    _attr_entity_category = EntityCategory.CONFIG
    _attr_mode = TextMode.TEXT
    _attr_native_min = 0
    _attr_native_max = 27
    # Empty, or day abbreviations separated by commas.
    _attr_pattern = r"^$|^(mon|tue|wed|thu|fri|sat|sun)(,(mon|tue|wed|thu|fri|sat|sun))*$"

    def __init__(
        self, coordinator: CramerCoordinator, device_id: str, index: int
    ) -> None:
        super().__init__(coordinator, device_id, index, "days")

    @property
    def native_value(self) -> str | None:
        timer = self.timer
        if timer is None:
            return None
        return ",".join(timer["days"])

    async def async_set_value(self, value: str) -> None:
        days = [part.strip().lower() for part in value.split(",") if part.strip()]
        try:
            protocol.days_to_mask(days)
        except ValueError as err:
            raise ServiceValidationError(str(err)) from err
        await self.coordinator.async_write_timer(
            self._device_id, self._index, days=days
        )
