"""Select platform for Cramer AiConic mower preferences."""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass

from homeassistant.components.select import SelectEntity, SelectEntityDescription
from homeassistant.const import EntityCategory
from homeassistant.core import HomeAssistant
from homeassistant.helpers.entity_platform import AddConfigEntryEntitiesCallback

from . import protocol
from .coordinator import CramerConfigEntry, CramerCoordinator, MowerState
from .entity import CramerEntity


@dataclass(frozen=True, kw_only=True)
class CramerSelectDescription(SelectEntityDescription):
    """Describes a Cramer select."""

    value_fn: Callable[[MowerState], str | None]
    command_fn: Callable[[str, int], str]
    action: str
    options_fn: Callable[[MowerState], list[str]] | None = None


SELECTS: tuple[CramerSelectDescription, ...] = (
    CramerSelectDescription(
        key="front_light",
        translation_key="front_light",
        options=protocol.FRONT_LIGHT_OPTIONS,
        entity_category=EntityCategory.CONFIG,
        value_fn=lambda m: m.front_light,
        command_fn=protocol.cmd_set_front_light,
        action="set the front light",
    ),
    CramerSelectDescription(
        key="rear_light",
        translation_key="rear_light",
        options=protocol.REAR_LIGHT_OPTIONS,
        entity_category=EntityCategory.CONFIG,
        value_fn=lambda m: m.rear_light,
        command_fn=protocol.cmd_set_rear_light,
        action="set the rear light",
    ),
    CramerSelectDescription(
        key="sound",
        translation_key="sound",
        options=protocol.SOUND_OPTIONS,
        entity_category=EntityCategory.CONFIG,
        value_fn=lambda m: m.sound,
        command_fn=protocol.cmd_set_sound,
        action="set the sound mode",
    ),
    CramerSelectDescription(
        key="obstacle_handling",
        translation_key="obstacle_handling",
        options=protocol.OBSTACLE_OPTIONS,
        entity_category=EntityCategory.CONFIG,
        value_fn=lambda m: m.obstacle_handling,
        command_fn=protocol.cmd_set_obstacle_handling,
        action="set obstacle handling",
    ),
    CramerSelectDescription(
        key="selected_site",
        translation_key="selected_site",
        entity_category=EntityCategory.CONFIG,
        value_fn=lambda m: m.selected_site,
        command_fn=protocol.cmd_set_selected_site,
        action="select the site",
        options_fn=lambda m: m.available_sites,
    ),
)


async def async_setup_entry(
    hass: HomeAssistant,
    entry: CramerConfigEntry,
    async_add_entities: AddConfigEntryEntitiesCallback,
) -> None:
    """Set up the preference selects."""
    coordinator = entry.runtime_data
    if not coordinator.settings_enabled:
        return
    async_add_entities(
        CramerSelect(coordinator, device_id, description)
        for device_id in coordinator.data
        for description in SELECTS
    )


class CramerSelect(CramerEntity, SelectEntity):
    """A Cramer mower preference."""

    entity_description: CramerSelectDescription

    def __init__(
        self,
        coordinator: CramerCoordinator,
        device_id: str,
        description: CramerSelectDescription,
    ) -> None:
        super().__init__(coordinator, device_id)
        self.entity_description = description
        self._attr_unique_id = f"{device_id}_{description.key}"

    @property
    def options(self) -> list[str]:
        """Fixed for the preference selects; discovered for the site select."""
        if self.entity_description.options_fn is None:
            return list(self.entity_description.options or [])
        discovered = self.entity_description.options_fn(self.mower)
        current = self.entity_description.value_fn(self.mower)
        if current and current not in discovered:
            return [*discovered, current]
        return discovered

    @property
    def current_option(self) -> str | None:
        value = self.entity_description.value_fn(self.mower)
        return value if value in self.options else None

    async def async_select_option(self, option: str) -> None:
        await self.coordinator.async_send(
            self._device_id,
            self.entity_description.command_fn(
                option, self.coordinator.build_message_id()
            ),
            self.entity_description.action,
            refresh_settings=True,
        )
