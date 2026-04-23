"""Diagnostics support for Indexa Capital."""

from __future__ import annotations

from dataclasses import asdict

from homeassistant.components.diagnostics import async_redact_data
from homeassistant.config_entries import ConfigEntry
from homeassistant.const import CONF_API_TOKEN
from homeassistant.core import HomeAssistant

from .api import snapshot_to_dict
from .const import DATA_COORDINATOR, DOMAIN

TO_REDACT = {CONF_API_TOKEN, "account_number", "display_name"}


async def async_get_config_entry_diagnostics(
    hass: HomeAssistant, entry: ConfigEntry
) -> dict[str, object]:
    """Return diagnostics for a config entry."""
    coordinator = hass.data[DOMAIN][entry.entry_id][DATA_COORDINATOR]
    payload = {
        "entry": {
            "data": dict(entry.data),
            "options": dict(entry.options),
        },
        "runtime_state": asdict(coordinator.runtime_state),
        "snapshot": snapshot_to_dict(coordinator.data),
    }
    return async_redact_data(payload, TO_REDACT)
