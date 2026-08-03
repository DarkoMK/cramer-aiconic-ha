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
        # Stale readings with no polling errors usually mean the account is
        # being left to the phone app, which is otherwise invisible.
        "session": {
            "yielded_to_phone_app": coordinator.api.is_yielded,
            "yield_remaining_seconds": round(coordinator.api.yield_remaining),
        },
        "mowers": {
            # ``is_stale`` and the contact age are properties, so ``asdict``
            # does not see them — and they are the first thing worth knowing
            # when the readings look wrong.
            device_id: async_redact_data(
                asdict(state)
                | {
                    "is_stale": state.is_stale,
                    "contact_age_seconds": (
                        None
                        if state.contact_age is None
                        else state.contact_age.total_seconds()
                    ),
                },
                TO_REDACT,
            )
            for device_id, state in (coordinator.data or {}).items()
        },
    }
