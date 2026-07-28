"""Number platform for Cramer AiConic (cutting height)."""

from __future__ import annotations

from homeassistant.components.number import NumberDeviceClass, NumberEntity, NumberMode
from homeassistant.const import EntityCategory, UnitOfLength, UnitOfTime
from homeassistant.core import HomeAssistant
from homeassistant.helpers.entity_platform import AddConfigEntryEntitiesCallback

from . import protocol
from .coordinator import CramerConfigEntry, CramerCoordinator
from .entity import CramerEntity
from .schedule_entity import CramerTimerEntity, slot_range

# The wire format is wider than the machine: `CuttingHeight.fromUInt8` accepts
# 20..102 mm, but the Cramer app's own cutting-height slider only ever offers
# 20..80 (`EditCuttingHeightView`: `rangeTo(20.0f, 80.0f)`, default 80). Exposing
# the wire range let a dashboard offer heights the mower cannot actually cut, so
# these bound what the vendor bounds.
MIN_HEIGHT_MM = 20
MAX_HEIGHT_MM = 80

# What the protocol will still carry, for callers that need to recognise a
# reading the mower reports from outside the settable range rather than reject it.
WIRE_MAX_HEIGHT_MM = 102


async def async_setup_entry(
    hass: HomeAssistant,
    entry: CramerConfigEntry,
    async_add_entities: AddConfigEntryEntitiesCallback,
) -> None:
    """Set up the cutting height control."""
    coordinator = entry.runtime_data
    entities: list[NumberEntity] = [
        CramerCuttingHeight(coordinator, device_id) for device_id in coordinator.data
    ]
    if coordinator.settings_enabled:
        entities.extend(
            CramerTimerDuration(coordinator, device_id, index)
            for device_id in coordinator.data
            for index in slot_range()
        )
    async_add_entities(entities)


class CramerCuttingHeight(CramerEntity, NumberEntity):
    """Cutting height in millimetres."""

    _attr_translation_key = "cutting_height"
    _attr_device_class = NumberDeviceClass.DISTANCE
    _attr_native_unit_of_measurement = UnitOfLength.MILLIMETERS
    _attr_native_min_value = MIN_HEIGHT_MM
    _attr_native_max_value = MAX_HEIGHT_MM
    _attr_native_step = 1
    _attr_mode = NumberMode.SLIDER
    _attr_entity_category = EntityCategory.CONFIG

    def __init__(self, coordinator: CramerCoordinator, device_id: str) -> None:
        super().__init__(coordinator, device_id)
        self._attr_unique_id = f"{device_id}_cutting_height_set"

    @property
    def native_value(self) -> float | None:
        return self.mower.cutting_height

    async def async_set_native_value(self, value: float) -> None:
        await self.coordinator.async_send(
            self._device_id,
            protocol.cmd_set_cutting_height(
                int(value), message_id=self.coordinator.build_message_id()
            ),
            "set cutting height",
        )


class CramerTimerDuration(CramerTimerEntity, NumberEntity):
    """How long a week timer keeps the mower out, in minutes."""

    _attr_translation_key = "timer_duration"
    _attr_native_unit_of_measurement = UnitOfTime.MINUTES
    _attr_device_class = NumberDeviceClass.DURATION
    _attr_native_min_value = 0
    _attr_native_max_value = 1440
    _attr_native_step = 15
    _attr_mode = NumberMode.BOX
    _attr_entity_category = EntityCategory.CONFIG

    def __init__(
        self, coordinator: CramerCoordinator, device_id: str, index: int
    ) -> None:
        super().__init__(coordinator, device_id, index, "duration")

    @property
    def native_value(self) -> float | None:
        timer = self.timer
        return None if timer is None else timer["duration_minutes"]

    async def async_set_native_value(self, value: float) -> None:
        await self.coordinator.async_write_timer(
            self._device_id, self._index, duration_minutes=int(value)
        )
