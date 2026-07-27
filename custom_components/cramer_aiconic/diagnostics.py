"""Diagnostics for Cramer AiConic."""

from __future__ import annotations

from dataclasses import asdict
from typing import Any

from homeassistant.components.diagnostics import async_redact_data
from homeassistant.const import CONF_PASSWORD, CONF_USERNAME
from homeassistant.core import HomeAssistant

from .const import CONF_REFRESH_TOKEN
from .coordinator import CramerConfigEntry

TO_REDACT = {
    CONF_PASSWORD,
    CONF_USERNAME,
    CONF_REFRESH_TOKEN,
    "mac",
    "sn",
    "serial_number",
    "latitude",
    "longitude",
    "mcu1",
}


async def async_get_config_entry_diagnostics(
    hass: HomeAssistant, entry: CramerConfigEntry
) -> dict[str, Any]:
    """Return diagnostics for a config entry."""
    coordinator = entry.runtime_data
    return {
        "entry": async_redact_data(dict(entry.data), TO_REDACT),
        "options": dict(entry.options),
        "mowers": {
            device_id: async_redact_data(asdict(state), TO_REDACT)
            for device_id, state in (coordinator.data or {}).items()
        },
    }
