"""Switch platform for Cramer AiConic (automatic firmware updates)."""

from __future__ import annotations

from typing import Any

from homeassistant.components.switch import SwitchEntity
from homeassistant.const import EntityCategory
from homeassistant.core import HomeAssistant
from homeassistant.exceptions import HomeAssistantError
from homeassistant.helpers.entity_platform import AddConfigEntryEntitiesCallback

from . import protocol
from .coordinator import CramerConfigEntry, CramerCoordinator
from .entity import CramerEntity
from .schedule_entity import CramerTimerEntity, slot_range


async def async_setup_entry(
    hass: HomeAssistant,
    entry: CramerConfigEntry,
    async_add_entities: AddConfigEntryEntitiesCallback,
) -> None:
    """Set up the auto-update switch."""
    coordinator = entry.runtime_data
    if not coordinator.settings_enabled:
        return
    entities: list[SwitchEntity] = []
    for device_id in coordinator.data:
        entities.append(CramerAutoUpdateSwitch(coordinator, device_id))
        entities.extend(
            CramerTimerEnabled(coordinator, device_id, index) for index in slot_range()
        )
    async_add_entities(entities)


class CramerAutoUpdateSwitch(CramerEntity, SwitchEntity):
    """Automatic firmware updates on the mower."""

    _attr_translation_key = "auto_update"
    _attr_entity_category = EntityCategory.CONFIG

    def __init__(self, coordinator: CramerCoordinator, device_id: str) -> None:
        super().__init__(coordinator, device_id)
        self._attr_unique_id = f"{device_id}_auto_update"

    @property
    def is_on(self) -> bool | None:
        return self.mower.auto_update

    async def async_turn_on(self, **kwargs: Any) -> None:
        await self._async_set(True)

    async def async_turn_off(self, **kwargs: Any) -> None:
        await self._async_set(False)

    async def _async_set(self, enabled: bool) -> None:
        await self.coordinator.async_send(
            self._device_id,
            protocol.cmd_set_auto_update(
                enabled, message_id=self.coordinator.build_message_id()
            ),
            "change automatic updates",
            refresh_settings=True,
        )


class CramerTimerEnabled(CramerTimerEntity, SwitchEntity):
    """Whether a week timer slot is active."""

    _attr_translation_key = "timer_enabled"
    _attr_entity_category = EntityCategory.CONFIG

    def __init__(
        self, coordinator: CramerCoordinator, device_id: str, index: int
    ) -> None:
        super().__init__(coordinator, device_id, index, "enabled")

    @property
    def is_on(self) -> bool:
        timer = self.timer
        return bool(timer and timer["enabled"])

    async def async_turn_on(self, **kwargs: Any) -> None:
        timer = self.timer
        if timer is None or not timer["days"] or not timer["duration_minutes"]:
            raise HomeAssistantError(
                "Set the days and duration for this slot before enabling it"
            )
        await self.coordinator.async_write_timer(
            self._device_id, self._index, enabled=True
        )

    async def async_turn_off(self, **kwargs: Any) -> None:
        await self.coordinator.async_write_timer(
            self._device_id, self._index, enabled=False
        )
