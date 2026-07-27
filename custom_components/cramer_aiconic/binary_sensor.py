"""Binary sensor platform for Cramer AiConic."""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass

from homeassistant.components.binary_sensor import (
    BinarySensorDeviceClass,
    BinarySensorEntity,
    BinarySensorEntityDescription,
)
from homeassistant.const import EntityCategory
from homeassistant.core import HomeAssistant
from homeassistant.helpers.entity_platform import AddConfigEntryEntitiesCallback

from .coordinator import CramerConfigEntry, CramerCoordinator, MowerState
from .entity import CramerEntity


@dataclass(frozen=True, kw_only=True)
class CramerBinarySensorDescription(BinarySensorEntityDescription):
    """Describes a Cramer binary sensor."""

    value_fn: Callable[[MowerState], bool]


BINARY_SENSORS: tuple[CramerBinarySensorDescription, ...] = (
    CramerBinarySensorDescription(
        key="online",
        device_class=BinarySensorDeviceClass.CONNECTIVITY,
        entity_category=EntityCategory.DIAGNOSTIC,
        value_fn=lambda m: m.is_online,
    ),
    CramerBinarySensorDescription(
        key="charging",
        translation_key="charging",
        device_class=BinarySensorDeviceClass.BATTERY_CHARGING,
        value_fn=lambda m: m.main_state == "charging",
    ),
    CramerBinarySensorDescription(
        key="in_charging_station",
        translation_key="in_charging_station",
        value_fn=lambda m: m.in_charging_station,
    ),
    CramerBinarySensorDescription(
        key="problem",
        device_class=BinarySensorDeviceClass.PROBLEM,
        value_fn=lambda m: m.is_error,
    ),
    CramerBinarySensorDescription(
        key="rtk_fix",
        translation_key="rtk_fix",
        entity_category=EntityCategory.DIAGNOSTIC,
        value_fn=lambda m: m.has_rtk_fix,
    ),
    CramerBinarySensorDescription(
        key="upside_down",
        translation_key="upside_down",
        device_class=BinarySensorDeviceClass.PROBLEM,
        entity_category=EntityCategory.DIAGNOSTIC,
        value_fn=lambda m: "upside_down" in m.status_flags,
    ),
)


async def async_setup_entry(
    hass: HomeAssistant,
    entry: CramerConfigEntry,
    async_add_entities: AddConfigEntryEntitiesCallback,
) -> None:
    """Set up the binary sensors."""
    coordinator = entry.runtime_data
    async_add_entities(
        CramerBinarySensor(coordinator, device_id, description)
        for device_id in coordinator.data
        for description in BINARY_SENSORS
    )


class CramerBinarySensor(CramerEntity, BinarySensorEntity):
    """A Cramer mower binary sensor."""

    entity_description: CramerBinarySensorDescription

    def __init__(
        self,
        coordinator: CramerCoordinator,
        device_id: str,
        description: CramerBinarySensorDescription,
    ) -> None:
        super().__init__(coordinator, device_id)
        self.entity_description = description
        self._attr_unique_id = f"{device_id}_{description.key}"

    @property
    def is_on(self) -> bool:
        return self.entity_description.value_fn(self.mower)
