"""Switch platform for Cramer AiConic (automatic firmware updates)."""

from __future__ import annotations

from typing import Any

from homeassistant.components.switch import SwitchEntity
from homeassistant.const import EntityCategory
from homeassistant.core import HomeAssistant, callback
from homeassistant.exceptions import HomeAssistantError
from homeassistant.helpers.entity_platform import AddConfigEntryEntitiesCallback
from homeassistant.helpers.event import async_call_later

from . import protocol
from .const import SETTINGS_SYNC_PAUSE_SECONDS
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

    # Created before the settings gate below, and deliberately so: this switch
    # is what turns the settings pass off, so if it lived behind the gate then
    # starting up with settings disabled would leave no way to turn them back
    # on.
    async_add_entities(
        CramerSettingsSyncSwitch(coordinator, device_id) for device_id in coordinator.data
    )

    if not coordinator.settings_enabled:
        return
    entities: list[SwitchEntity] = []
    for device_id in coordinator.data:
        entities.append(CramerAutoUpdateSwitch(coordinator, device_id))
        entities.extend(
            CramerTimerEnabled(coordinator, device_id, index) for index in slot_range()
        )
    async_add_entities(entities)


class CramerSettingsSyncSwitch(CramerEntity, SwitchEntity):
    """Whether Home Assistant may open its MQTT settings session.

    The AWS IoT policy pins the MQTT client id to the account's Cognito
    identity, so Home Assistant and the phone app cannot both hold a
    connection — whoever connects last kicks the other off. Only the settings
    pass takes that slot, for a few seconds every fifteen minutes, which is
    still enough to interrupt mapping a zone in the app; turn this off for the
    duration and the phone keeps the link.

    It does *not* make Home Assistant invisible to the app. The account also
    holds a single token version, so the REST side evicts the app too — see
    ``api.CramerAiConicApi._begin_yield``, which handles that automatically by
    standing down for fifteen minutes whenever the app takes the account.

    Runtime-only by design. ``settings_enabled`` is re-read on every poll, so
    this takes effect immediately without reloading the entry — and a restart
    returns to the configured option, which means the pause can never outlive
    the session that asked for it.
    """

    _attr_translation_key = "settings_sync"
    _attr_entity_category = EntityCategory.CONFIG

    def __init__(self, coordinator: CramerCoordinator, device_id: str) -> None:
        super().__init__(coordinator, device_id)
        self._attr_unique_id = f"{device_id}_settings_sync"
        self._cancel_resume: Any = None

    @property
    def is_on(self) -> bool:
        return self.coordinator.settings_enabled

    @property
    def available(self) -> bool:
        # Answer from local state, so the switch can still be flipped while the
        # cloud is unreachable — which is exactly when someone is fighting the
        # app for the connection.
        return True

    async def async_turn_on(self, **kwargs: Any) -> None:
        self._cancel_pending_resume()
        self.coordinator.settings_enabled = True
        self.async_write_ha_state()

    async def async_turn_off(self, **kwargs: Any) -> None:
        self._cancel_pending_resume()
        self.coordinator.settings_enabled = False
        self._cancel_resume = async_call_later(
            self.hass, SETTINGS_SYNC_PAUSE_SECONDS, self._resume
        )
        self.async_write_ha_state()

    @callback
    def _resume(self, _now: Any) -> None:
        self._cancel_resume = None
        self.coordinator.settings_enabled = True
        self.async_write_ha_state()

    def _cancel_pending_resume(self) -> None:
        if self._cancel_resume is not None:
            self._cancel_resume()
            self._cancel_resume = None

    async def async_will_remove_from_hass(self) -> None:
        self._cancel_pending_resume()
        await super().async_will_remove_from_hass()


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
