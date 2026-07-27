"""Button platform for Cramer AiConic."""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass

from homeassistant.components.button import ButtonEntity, ButtonEntityDescription
from homeassistant.const import EntityCategory
from homeassistant.core import HomeAssistant
from homeassistant.helpers.entity_platform import AddConfigEntryEntitiesCallback

from . import protocol
from .coordinator import CramerConfigEntry, CramerCoordinator
from .entity import CramerEntity


@dataclass(frozen=True, kw_only=True)
class CramerButtonDescription(ButtonEntityDescription):
    """Describes a Cramer button."""

    payload_fn: Callable[[int], str]
    action: str


BUTTONS: tuple[CramerButtonDescription, ...] = (
    CramerButtonDescription(
        key="start",
        translation_key="start",
        payload_fn=lambda mid: protocol.cmd_start_mower(message_id=mid),
        action="start mowing",
    ),
    CramerButtonDescription(
        key="pause",
        translation_key="pause",
        payload_fn=lambda mid: protocol.cmd_pause_mower(message_id=mid),
        action="pause",
    ),
    CramerButtonDescription(
        key="return_to_base",
        translation_key="return_to_base",
        payload_fn=lambda mid: protocol.cmd_park_mower(message_id=mid),
        action="return to base",
    ),
    CramerButtonDescription(
        key="refresh",
        translation_key="refresh",
        entity_category=EntityCategory.DIAGNOSTIC,
        payload_fn=lambda mid: protocol.cmd_get(protocol.P_GET_MOWER_STATUS, mid),
        action="refresh status",
    ),
)


async def async_setup_entry(
    hass: HomeAssistant,
    entry: CramerConfigEntry,
    async_add_entities: AddConfigEntryEntitiesCallback,
) -> None:
    """Set up the buttons."""
    coordinator = entry.runtime_data
    async_add_entities(
        CramerButton(coordinator, device_id, description)
        for device_id in coordinator.data
        for description in BUTTONS
    )


class CramerButton(CramerEntity, ButtonEntity):
    """A Cramer mower command button."""

    entity_description: CramerButtonDescription

    def __init__(
        self,
        coordinator: CramerCoordinator,
        device_id: str,
        description: CramerButtonDescription,
    ) -> None:
        super().__init__(coordinator, device_id)
        self.entity_description = description
        self._attr_unique_id = f"{device_id}_{description.key}"

    async def async_press(self) -> None:
        await self.coordinator.async_send(
            self._device_id,
            self.entity_description.payload_fn(self.coordinator.build_message_id()),
            self.entity_description.action,
        )
