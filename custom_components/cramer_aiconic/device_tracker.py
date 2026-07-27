"""Device tracker platform for Cramer AiConic (mower GPS position)."""

from __future__ import annotations

from homeassistant.components.device_tracker import SourceType, TrackerEntity
from homeassistant.core import HomeAssistant
from homeassistant.helpers.entity_platform import AddConfigEntryEntitiesCallback

from .coordinator import CramerConfigEntry, CramerCoordinator
from .entity import CramerEntity


async def async_setup_entry(
    hass: HomeAssistant,
    entry: CramerConfigEntry,
    async_add_entities: AddConfigEntryEntitiesCallback,
) -> None:
    """Set up the mower position trackers."""
    coordinator = entry.runtime_data
    async_add_entities(
        CramerDeviceTracker(coordinator, device_id) for device_id in coordinator.data
    )


class CramerDeviceTracker(CramerEntity, TrackerEntity):
    """Reports the mower's last known GPS position."""

    _attr_translation_key = "mower_position"

    def __init__(self, coordinator: CramerCoordinator, device_id: str) -> None:
        super().__init__(coordinator, device_id)
        self._attr_unique_id = f"{device_id}_position"

    @property
    def source_type(self) -> SourceType:
        return SourceType.GPS

    @property
    def latitude(self) -> float | None:
        return self.mower.latitude

    @property
    def longitude(self) -> float | None:
        return self.mower.longitude
