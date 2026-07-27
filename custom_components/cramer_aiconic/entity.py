"""Shared entity base for Cramer AiConic."""

from __future__ import annotations

from homeassistant.helpers.device_registry import DeviceInfo
from homeassistant.helpers.update_coordinator import CoordinatorEntity

from .const import DOMAIN, FAILURES_BEFORE_UNAVAILABLE, MANUFACTURER
from .coordinator import CramerCoordinator, MowerState


class CramerEntity(CoordinatorEntity[CramerCoordinator]):
    """Base entity bound to one mower."""

    _attr_has_entity_name = True

    def __init__(self, coordinator: CramerCoordinator, device_id: str) -> None:
        super().__init__(coordinator)
        self._device_id = device_id
        device = coordinator.data[device_id].device
        self._attr_device_info = DeviceInfo(
            identifiers={(DOMAIN, device_id)},
            manufacturer=MANUFACTURER,
            model=device.model or device.product_type or None,
            name=device.name,
            serial_number=device.serial_number or None,
            sw_version=device.firmware_version,
        )

    @property
    def mower(self) -> MowerState:
        """The current state of this mower."""
        return self.coordinator.data[self._device_id]

    @property
    def available(self) -> bool:
        """Stay available through brief cloud hiccups.

        A single failed poll is common — the cloud throttles, or the access
        token gets invalidated elsewhere and is renewed on the next cycle.
        Dropping every entity to unavailable for one cycle makes the whole
        integration look like it is flapping, so tolerate a short run of
        failures and keep reporting the last known state.
        """
        if self._device_id not in self.coordinator.data:
            return False
        if not self.mower.available:
            return False
        if self.coordinator.last_update_success:
            return True
        return self.coordinator.consecutive_failures <= FAILURES_BEFORE_UNAVAILABLE
