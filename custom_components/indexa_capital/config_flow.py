"""Config flow for Indexa Capital."""

from __future__ import annotations

from datetime import time
from typing import Any

import voluptuous as vol
from homeassistant import config_entries
from homeassistant.const import CONF_API_TOKEN
from homeassistant.core import HomeAssistant, callback
from homeassistant.data_entry_flow import FlowResult
from homeassistant.helpers import selector
from homeassistant.helpers.aiohttp_client import async_get_clientsession

from .api import IndexaApiClient, IndexaApiError, IndexaAuthError
from .const import (
    CONF_NOTIFY_SERVICE,
    CONF_REFRESH_END_TIME,
    CONF_REFRESH_INTERVAL_MINUTES,
    CONF_REFRESH_START_TIME,
    DEFAULT_REFRESH_END_TIME,
    DEFAULT_REFRESH_INTERVAL_MINUTES,
    DEFAULT_REFRESH_START_TIME,
    DOMAIN,
)


async def validate_input(hass: HomeAssistant, data: dict[str, Any]) -> dict[str, Any]:
    """Validate the user input and return normalized metadata."""
    client = IndexaApiClient(async_get_clientsession(hass), data[CONF_API_TOKEN])
    profile = await client.async_validate_token()
    return {
        "title": "Indexa Capital",
        "token_fingerprint": client.token_fingerprint,
        "profile": profile,
    }


class IndexaCapitalConfigFlow(config_entries.ConfigFlow, domain=DOMAIN):
    """Handle a config flow for Indexa Capital."""

    VERSION = 1

    def __init__(self) -> None:
        self._reauth_entry = None

    async def async_step_user(self, user_input: dict[str, Any] | None = None) -> FlowResult:
        """Handle the initial step."""
        errors: dict[str, str] = {}
        if user_input is not None:
            try:
                info = await validate_input(self.hass, user_input)
            except IndexaAuthError:
                errors["base"] = "invalid_auth"
            except IndexaApiError:
                errors["base"] = "cannot_connect"
            else:
                await self.async_set_unique_id(info["token_fingerprint"])
                self._abort_if_unique_id_configured()
                return self.async_create_entry(
                    title=info["title"],
                    data={CONF_API_TOKEN: user_input[CONF_API_TOKEN]},
                )

        return self.async_show_form(
            step_id="user",
            data_schema=vol.Schema({vol.Required(CONF_API_TOKEN): str}),
            errors=errors,
        )

    async def async_step_reauth(self, entry_data: dict[str, Any]) -> FlowResult:
        """Start reauthentication."""
        self._reauth_entry = self.hass.config_entries.async_get_entry(self.context["entry_id"])
        return await self.async_step_reauth_confirm()

    async def async_step_reauth_confirm(
        self, user_input: dict[str, Any] | None = None
    ) -> FlowResult:
        """Confirm reauthentication."""
        errors: dict[str, str] = {}
        if user_input is not None and self._reauth_entry is not None:
            try:
                info = await validate_input(self.hass, user_input)
            except IndexaAuthError:
                errors["base"] = "invalid_auth"
            except IndexaApiError:
                errors["base"] = "cannot_connect"
            else:
                for entry in self._async_current_entries():
                    if (
                        entry.unique_id == info["token_fingerprint"]
                        and entry.entry_id != self._reauth_entry.entry_id
                    ):
                        return self.async_abort(reason="already_configured")
                self.hass.config_entries.async_update_entry(
                    self._reauth_entry,
                    data={CONF_API_TOKEN: user_input[CONF_API_TOKEN]},
                    unique_id=info["token_fingerprint"],
                )
                await self.hass.config_entries.async_reload(self._reauth_entry.entry_id)
                return self.async_abort(reason="reauth_successful")

        return self.async_show_form(
            step_id="reauth_confirm",
            data_schema=vol.Schema({vol.Required(CONF_API_TOKEN): str}),
            errors=errors,
        )

    @staticmethod
    @callback
    def async_get_options_flow(config_entry):
        """Return the options flow handler."""
        return IndexaCapitalOptionsFlow(config_entry)


class IndexaCapitalOptionsFlow(config_entries.OptionsFlow):
    """Handle Indexa Capital options."""

    def __init__(self, config_entry) -> None:
        self._config_entry = config_entry

    async def async_step_init(self, user_input: dict[str, Any] | None = None) -> FlowResult:
        """Manage integration options."""
        if user_input is not None:
            return self.async_create_entry(data=user_input)

        return self.async_show_form(
            step_id="init",
            data_schema=vol.Schema(
                {
                    vol.Optional(
                        CONF_NOTIFY_SERVICE,
                        default=self._config_entry.options.get(CONF_NOTIFY_SERVICE, ""),
                    ): str,
                    vol.Required(
                        CONF_REFRESH_START_TIME,
                        default=_serialize_time_selector_value(
                            self._config_entry.options.get(
                                CONF_REFRESH_START_TIME, DEFAULT_REFRESH_START_TIME
                            )
                        ),
                    ): selector.TimeSelector(),
                    vol.Required(
                        CONF_REFRESH_END_TIME,
                        default=_serialize_time_selector_value(
                            self._config_entry.options.get(
                                CONF_REFRESH_END_TIME, DEFAULT_REFRESH_END_TIME
                            )
                        ),
                    ): selector.TimeSelector(),
                    vol.Required(
                        CONF_REFRESH_INTERVAL_MINUTES,
                        default=self._config_entry.options.get(
                            CONF_REFRESH_INTERVAL_MINUTES, DEFAULT_REFRESH_INTERVAL_MINUTES
                        ),
                    ): vol.All(vol.Coerce(int), vol.Range(min=1)),
                }
            ),
        )


def _serialize_time_selector_value(value: time | str) -> str:
    """Return a frontend-safe default value for a time selector."""
    if isinstance(value, str):
        return value
    return value.isoformat()
