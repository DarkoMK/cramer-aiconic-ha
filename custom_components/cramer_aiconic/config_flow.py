"""Config flow for Cramer AiConic."""

from __future__ import annotations

import logging
from collections.abc import Mapping
from typing import Any

import voluptuous as vol

from homeassistant.config_entries import (
    ConfigFlow,
    ConfigFlowResult,
    OptionsFlow,
    ConfigEntry,
)
from homeassistant.const import CONF_PASSWORD, CONF_USERNAME
from homeassistant.core import callback
from homeassistant.helpers.aiohttp_client import async_get_clientsession
from homeassistant.helpers.selector import (
    BooleanSelector,
    NumberSelector,
    NumberSelectorConfig,
    NumberSelectorMode,
    SelectSelector,
    SelectSelectorConfig,
    SelectSelectorMode,
)

from .api import CramerAiConicApi, CramerApiError, CramerAuthError
from .const import (
    CONF_ENABLE_SETTINGS,
    CONF_REFRESH_TOKEN,
    CONF_REGION_CODE,
    CONF_SCAN_INTERVAL,
    DEFAULT_ENABLE_SETTINGS,
    DEFAULT_REGION_CODE,
    DEFAULT_SCAN_INTERVAL,
    DOMAIN,
    MAX_SCAN_INTERVAL,
    MIN_SCAN_INTERVAL,
)

_LOGGER = logging.getLogger(__name__)


class CramerAiConicConfigFlow(ConfigFlow, domain=DOMAIN):
    """Handle the Cramer AiConic config flow."""

    VERSION = 1

    def __init__(self) -> None:
        self._regions: list[str] = []

    async def _async_region_options(self) -> list[str]:
        if self._regions:
            return self._regions
        api = CramerAiConicApi(async_get_clientsession(self.hass), "", "")
        try:
            regions = await api.async_list_regions()
        except Exception:  # noqa: BLE001 - the list is a convenience only
            _LOGGER.debug("Region list unavailable, offering the default only")
            self._regions = [DEFAULT_REGION_CODE]
        else:
            codes = [r["regionCode"] for r in regions if r.get("regionCode")]
            self._regions = codes or [DEFAULT_REGION_CODE]
        return self._regions

    async def _async_try_login(
        self, username: str, password: str, region_code: str
    ) -> tuple[str | None, str | None, str | None]:
        """Return (user_id, refresh_token, error_key)."""
        api = CramerAiConicApi(
            async_get_clientsession(self.hass),
            username,
            password,
            region_code=region_code,
        )
        try:
            user_id = await api.async_validate_credentials()
        except CramerAuthError:
            return None, None, "invalid_auth"
        except CramerApiError:
            return None, None, "cannot_connect"
        except Exception:  # noqa: BLE001
            _LOGGER.exception("Unexpected error validating Cramer credentials")
            return None, None, "unknown"
        return user_id, api.refresh_token, None

    async def async_step_user(
        self, user_input: dict[str, Any] | None = None
    ) -> ConfigFlowResult:
        """Collect credentials."""
        errors: dict[str, str] = {}
        regions = await self._async_region_options()

        if user_input is not None:
            username = user_input[CONF_USERNAME].strip()
            region_code = user_input.get(CONF_REGION_CODE, DEFAULT_REGION_CODE)
            user_id, refresh_token, error = await self._async_try_login(
                username, user_input[CONF_PASSWORD], region_code
            )
            if error:
                errors["base"] = error
            else:
                await self.async_set_unique_id(user_id)
                self._abort_if_unique_id_configured()
                return self.async_create_entry(
                    title=f"Cramer AiConic ({username})",
                    data={
                        CONF_USERNAME: username,
                        CONF_PASSWORD: user_input[CONF_PASSWORD],
                        CONF_REGION_CODE: region_code,
                        CONF_REFRESH_TOKEN: refresh_token,
                    },
                )

        return self.async_show_form(
            step_id="user",
            data_schema=vol.Schema(
                {
                    vol.Required(
                        CONF_USERNAME,
                        default=(user_input or {}).get(CONF_USERNAME, ""),
                    ): str,
                    vol.Required(CONF_PASSWORD): str,
                    vol.Required(
                        CONF_REGION_CODE,
                        default=(
                            DEFAULT_REGION_CODE
                            if DEFAULT_REGION_CODE in regions
                            else regions[0]
                        ),
                    ): SelectSelector(
                        SelectSelectorConfig(
                            options=regions, mode=SelectSelectorMode.DROPDOWN
                        )
                    ),
                }
            ),
            errors=errors,
        )

    async def async_step_reauth(
        self, entry_data: Mapping[str, Any]
    ) -> ConfigFlowResult:
        """Handle re-authentication after credentials stopped working."""
        return await self.async_step_reauth_confirm()

    async def async_step_reauth_confirm(
        self, user_input: dict[str, Any] | None = None
    ) -> ConfigFlowResult:
        """Ask for a fresh password."""
        errors: dict[str, str] = {}
        entry = self._get_reauth_entry()

        if user_input is not None:
            region_code = entry.data.get(CONF_REGION_CODE, DEFAULT_REGION_CODE)
            _, refresh_token, error = await self._async_try_login(
                entry.data[CONF_USERNAME], user_input[CONF_PASSWORD], region_code
            )
            if error:
                errors["base"] = error
            else:
                return self.async_update_reload_and_abort(
                    entry,
                    data_updates={
                        CONF_PASSWORD: user_input[CONF_PASSWORD],
                        CONF_REFRESH_TOKEN: refresh_token,
                    },
                )

        return self.async_show_form(
            step_id="reauth_confirm",
            data_schema=vol.Schema({vol.Required(CONF_PASSWORD): str}),
            description_placeholders={"username": entry.data[CONF_USERNAME]},
            errors=errors,
        )

    @staticmethod
    @callback
    def async_get_options_flow(config_entry: ConfigEntry) -> OptionsFlow:
        return CramerAiConicOptionsFlow()


class CramerAiConicOptionsFlow(OptionsFlow):
    """Options: polling interval."""

    async def async_step_init(
        self, user_input: dict[str, Any] | None = None
    ) -> ConfigFlowResult:
        if user_input is not None:
            return self.async_create_entry(
                data={
                    CONF_SCAN_INTERVAL: int(user_input[CONF_SCAN_INTERVAL]),
                    CONF_ENABLE_SETTINGS: user_input[CONF_ENABLE_SETTINGS],
                }
            )

        current = self.config_entry.options.get(
            CONF_SCAN_INTERVAL, DEFAULT_SCAN_INTERVAL
        )
        settings = self.config_entry.options.get(
            CONF_ENABLE_SETTINGS, DEFAULT_ENABLE_SETTINGS
        )
        return self.async_show_form(
            step_id="init",
            data_schema=vol.Schema(
                {
                    vol.Required(CONF_SCAN_INTERVAL, default=current): NumberSelector(
                        NumberSelectorConfig(
                            min=MIN_SCAN_INTERVAL,
                            max=MAX_SCAN_INTERVAL,
                            step=5,
                            unit_of_measurement="s",
                            mode=NumberSelectorMode.BOX,
                        )
                    ),
                    vol.Required(CONF_ENABLE_SETTINGS, default=settings): BooleanSelector(),
                }
            ),
        )
