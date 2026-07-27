"""The Cramer AiConic robotic mower integration."""

from __future__ import annotations

import logging

from homeassistant.const import CONF_PASSWORD, CONF_USERNAME, Platform
from homeassistant.core import HomeAssistant
from homeassistant.helpers.aiohttp_client import async_get_clientsession

from .api import CramerAiConicApi
from .const import (
    CONF_ENABLE_SETTINGS,
    CONF_REFRESH_TOKEN,
    CONF_REGION_CODE,
    CONF_SCAN_INTERVAL,
    DEFAULT_ENABLE_SETTINGS,
    DEFAULT_REGION_CODE,
    DEFAULT_SCAN_INTERVAL,
)
from .coordinator import CramerConfigEntry, CramerCoordinator

_LOGGER = logging.getLogger(__name__)

PLATFORMS: list[Platform] = [
    Platform.BINARY_SENSOR,
    Platform.BUTTON,
    Platform.DEVICE_TRACKER,
    Platform.LAWN_MOWER,
    Platform.NUMBER,
    Platform.SELECT,
    Platform.SENSOR,
    Platform.SWITCH,
    Platform.TEXT,
    Platform.TIME,
]


async def async_setup_entry(hass: HomeAssistant, entry: CramerConfigEntry) -> bool:
    """Set up Cramer AiConic from a config entry."""
    session = async_get_clientsession(hass)

    async def _persist_tokens(refresh_token: str | None, region_code: str | None) -> None:
        """Store the rotated refresh token immediately.

        The refresh token is single-use, so it must survive a restart at any
        moment — otherwise the next refresh presents a revoked token.
        """
        data = {**entry.data}
        changed = False
        if refresh_token and data.get(CONF_REFRESH_TOKEN) != refresh_token:
            data[CONF_REFRESH_TOKEN] = refresh_token
            changed = True
        if region_code and data.get(CONF_REGION_CODE) != region_code:
            data[CONF_REGION_CODE] = region_code
            changed = True
        if changed:
            hass.config_entries.async_update_entry(entry, data=data)

    api = CramerAiConicApi(
        session,
        entry.data[CONF_USERNAME],
        entry.data[CONF_PASSWORD],
        region_code=entry.data.get(CONF_REGION_CODE, DEFAULT_REGION_CODE),
        refresh_token=entry.data.get(CONF_REFRESH_TOKEN),
        on_tokens_updated=_persist_tokens,
    )

    scan_interval = entry.options.get(
        CONF_SCAN_INTERVAL, entry.data.get(CONF_SCAN_INTERVAL, DEFAULT_SCAN_INTERVAL)
    )
    settings_enabled = entry.options.get(CONF_ENABLE_SETTINGS, DEFAULT_ENABLE_SETTINGS)
    coordinator = CramerCoordinator(hass, entry, api, scan_interval, settings_enabled)
    coordinator.options_snapshot = dict(entry.options)
    await coordinator.async_config_entry_first_refresh()

    entry.runtime_data = coordinator
    await hass.config_entries.async_forward_entry_setups(entry, PLATFORMS)
    entry.async_on_unload(entry.add_update_listener(async_reload_entry))
    return True


async def async_unload_entry(hass: HomeAssistant, entry: CramerConfigEntry) -> bool:
    """Unload a config entry."""
    return await hass.config_entries.async_unload_platforms(entry, PLATFORMS)


async def async_reload_entry(hass: HomeAssistant, entry: CramerConfigEntry) -> None:
    """Reload only when the options changed.

    This listener also fires when ``entry.data`` is rewritten, which happens
    every time the single-use refresh token rotates. Reloading then would tear
    down and rebuild every entity a few times an hour for no reason.
    """
    coordinator = entry.runtime_data
    if coordinator.options_snapshot == dict(entry.options):
        return
    await hass.config_entries.async_reload(entry.entry_id)
