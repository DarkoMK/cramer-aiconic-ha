"""Sensor platform for Cramer AiConic."""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass
from datetime import datetime

from homeassistant.components.sensor import (
    SensorDeviceClass,
    SensorEntity,
    SensorEntityDescription,
    SensorStateClass,
)
from homeassistant.const import (
    PERCENTAGE,
    EntityCategory,
    UnitOfArea,
    UnitOfLength,
    UnitOfTime,
)
from homeassistant.core import HomeAssistant
from homeassistant.helpers.entity_platform import AddConfigEntryEntitiesCallback

from . import protocol
from .coordinator import CramerConfigEntry, CramerCoordinator, MowerState
from .entity import CramerEntity


@dataclass(frozen=True, kw_only=True)
class CramerSensorDescription(SensorEntityDescription):
    """Describes a Cramer sensor."""

    value_fn: Callable[[MowerState], str | int | float | datetime | None]
    attrs_fn: Callable[[MowerState], dict[str, object]] | None = None


SENSORS: tuple[CramerSensorDescription, ...] = (
    CramerSensorDescription(
        key="state",
        translation_key="mower_state",
        device_class=SensorDeviceClass.ENUM,
        options=sorted(set(protocol.MAIN_STATE.values())),
        value_fn=lambda m: m.main_state,
    ),
    CramerSensorDescription(
        key="battery",
        device_class=SensorDeviceClass.BATTERY,
        native_unit_of_measurement=PERCENTAGE,
        state_class=SensorStateClass.MEASUREMENT,
        value_fn=lambda m: m.battery,
    ),
    CramerSensorDescription(
        key="next_start_stop",
        translation_key="next_start_stop",
        device_class=SensorDeviceClass.TIMESTAMP,
        value_fn=lambda m: m.next_start_stop,
    ),
    CramerSensorDescription(
        key="next_start_stop_source",
        translation_key="next_start_stop_source",
        entity_category=EntityCategory.DIAGNOSTIC,
        value_fn=lambda m: m.next_start_stop_source,
    ),
    CramerSensorDescription(
        key="signal_quality",
        translation_key="signal_quality",
        native_unit_of_measurement=PERCENTAGE,
        state_class=SensorStateClass.MEASUREMENT,
        entity_category=EntityCategory.DIAGNOSTIC,
        value_fn=lambda m: m.signal_quality,
    ),
    CramerSensorDescription(
        key="cutting_height",
        translation_key="cutting_height",
        native_unit_of_measurement=UnitOfLength.MILLIMETERS,
        device_class=SensorDeviceClass.DISTANCE,
        entity_category=EntityCategory.DIAGNOSTIC,
        value_fn=lambda m: m.cutting_height,
    ),
    CramerSensorDescription(
        key="operation_mode",
        translation_key="operation_mode",
        device_class=SensorDeviceClass.ENUM,
        options=sorted(set(protocol.OPERATION_MODE.values())),
        entity_category=EntityCategory.DIAGNOSTIC,
        value_fn=lambda m: m.operation_mode,
    ),
    CramerSensorDescription(
        key="site_name",
        translation_key="site_name",
        entity_category=EntityCategory.DIAGNOSTIC,
        value_fn=lambda m: m.site_name,
    ),
    CramerSensorDescription(
        key="map_name",
        translation_key="map_name",
        entity_category=EntityCategory.DIAGNOSTIC,
        value_fn=lambda m: m.map_name,
    ),
    CramerSensorDescription(
        key="default_speed",
        translation_key="default_speed",
        entity_category=EntityCategory.DIAGNOSTIC,
        value_fn=lambda m: m.default_speed,
    ),
    CramerSensorDescription(
        key="lte_signal",
        translation_key="lte_signal",
        state_class=SensorStateClass.MEASUREMENT,
        entity_category=EntityCategory.DIAGNOSTIC,
        value_fn=lambda m: m.lte_signal,
    ),
    CramerSensorDescription(
        key="area_cut",
        translation_key="area_cut",
        native_unit_of_measurement=UnitOfArea.SQUARE_METERS,
        device_class=SensorDeviceClass.AREA,
        state_class=SensorStateClass.MEASUREMENT,
        value_fn=lambda m: m.area_cut,
    ),
    CramerSensorDescription(
        key="area_remaining",
        translation_key="area_remaining",
        native_unit_of_measurement=UnitOfArea.SQUARE_METERS,
        device_class=SensorDeviceClass.AREA,
        state_class=SensorStateClass.MEASUREMENT,
        value_fn=lambda m: m.area_remaining,
    ),
    CramerSensorDescription(
        key="estimated_remaining",
        translation_key="estimated_remaining",
        native_unit_of_measurement=UnitOfTime.MINUTES,
        device_class=SensorDeviceClass.DURATION,
        value_fn=lambda m: m.estimated_remaining_minutes,
    ),
    CramerSensorDescription(
        key="firmware_version",
        translation_key="firmware_version",
        entity_category=EntityCategory.DIAGNOSTIC,
        value_fn=lambda m: m.firmware_version,
    ),
    CramerSensorDescription(
        key="zones",
        translation_key="zones",
        value_fn=lambda m: len(m.zones) if m.zones else None,
        attrs_fn=lambda m: {
            "active_zone": m.active_zone,
            "zones": [
                {
                    **zone,
                    "active": zone["name"] == m.active_zone,
                    # Coverage is reported for whichever map the mower is
                    # working, so it only belongs to the active zone.
                    "area_cut": m.area_cut if zone["name"] == m.active_zone else None,
                    "area_remaining": (
                        m.area_remaining if zone["name"] == m.active_zone else None
                    ),
                }
                for zone in m.zones
            ],
        },
    ),
    CramerSensorDescription(
        key="schedule",
        translation_key="schedule",
        value_fn=lambda m: len(m.enabled_timers) if m.week_timers else None,
        attrs_fn=lambda m: {
            "timers": m.week_timers,
            "summary": [
                f"{t['start']} for {t['duration_minutes']} min on "
                f"{', '.join(t['days']) or 'no days'}"
                for t in m.enabled_timers
            ],
        },
    ),
    CramerSensorDescription(
        key="settings_updated",
        translation_key="settings_updated",
        device_class=SensorDeviceClass.TIMESTAMP,
        entity_category=EntityCategory.DIAGNOSTIC,
        entity_registry_enabled_default=False,
        value_fn=lambda m: m.settings_updated,
    ),
    CramerSensorDescription(
        key="last_status_push",
        translation_key="last_status_push",
        device_class=SensorDeviceClass.TIMESTAMP,
        entity_category=EntityCategory.DIAGNOSTIC,
        value_fn=lambda m: m.last_status_push,
    ),
    # Minutes rather than a timestamp because this exists to be compared
    # against a threshold — "has it been quiet for more than N minutes" is a
    # numeric_state trigger on this, and needs no template. Whole minutes
    # keep it to one recorder row a minute instead of one per poll.
    CramerSensorDescription(
        key="last_contact_age",
        translation_key="last_contact_age",
        device_class=SensorDeviceClass.DURATION,
        native_unit_of_measurement=UnitOfTime.MINUTES,
        state_class=SensorStateClass.MEASUREMENT,
        entity_category=EntityCategory.DIAGNOSTIC,
        value_fn=lambda m: (
            None if m.contact_age is None else int(m.contact_age.total_seconds() // 60)
        ),
    ),
)


async def async_setup_entry(
    hass: HomeAssistant,
    entry: CramerConfigEntry,
    async_add_entities: AddConfigEntryEntitiesCallback,
) -> None:
    """Set up the sensors."""
    coordinator = entry.runtime_data
    async_add_entities(
        CramerSensor(coordinator, device_id, description)
        for device_id in coordinator.data
        for description in SENSORS
    )


class CramerSensor(CramerEntity, SensorEntity):
    """A Cramer mower sensor."""

    entity_description: CramerSensorDescription

    def __init__(
        self,
        coordinator: CramerCoordinator,
        device_id: str,
        description: CramerSensorDescription,
    ) -> None:
        super().__init__(coordinator, device_id)
        self.entity_description = description
        self._attr_unique_id = f"{device_id}_{description.key}"

    @property
    def native_value(self) -> str | int | float | datetime | None:
        return self.entity_description.value_fn(self.mower)

    @property
    def extra_state_attributes(self) -> dict[str, object]:
        attributes = super().extra_state_attributes
        if self.entity_description.attrs_fn is not None:
            attributes |= self.entity_description.attrs_fn(self.mower)
        return attributes
