"""General services for Indexa Capital."""

from __future__ import annotations

import voluptuous as vol
from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant, ServiceCall
from homeassistant.exceptions import HomeAssistantError
from homeassistant.helpers import config_validation as cv

from .const import (
    DATA_COORDINATOR,
    DOMAIN,
    SERVICE_ATTR_ENTRY_ID,
    SERVICE_ATTR_MESSAGE,
    SERVICE_ATTR_TITLE,
    SERVICE_SEND_TEST_NOTIFICATION,
)
from .coordinator import IndexaPortfolioCoordinator

DEFAULT_NOTIFICATION_TITLE = "Indexa Capital"
DEFAULT_NOTIFICATION_MESSAGE = "Test notification from Indexa Capital."

_TEST_NOTIFICATION_SCHEMA = vol.Schema(
    {
        vol.Optional(SERVICE_ATTR_ENTRY_ID): cv.string,
        vol.Optional(SERVICE_ATTR_TITLE): cv.string,
        vol.Optional(SERVICE_ATTR_MESSAGE): cv.string,
    }
)


def async_register_services(hass: HomeAssistant) -> None:
    """Register integration services."""
    if hass.services.has_service(DOMAIN, SERVICE_SEND_TEST_NOTIFICATION):
        return

    async def _handle_test_notification(call: ServiceCall) -> None:
        await async_handle_test_notification_service(hass, call)

    hass.services.async_register(
        DOMAIN,
        SERVICE_SEND_TEST_NOTIFICATION,
        _handle_test_notification,
        schema=_TEST_NOTIFICATION_SCHEMA,
    )


async def async_unregister_services(hass: HomeAssistant) -> None:
    """Unregister integration services."""
    if hass.services.has_service(DOMAIN, SERVICE_SEND_TEST_NOTIFICATION):
        hass.services.async_remove(DOMAIN, SERVICE_SEND_TEST_NOTIFICATION)


async def async_handle_test_notification_service(
    hass: HomeAssistant, call: ServiceCall
) -> None:
    """Send a test notification for a configured Indexa entry."""
    coordinator = _resolve_target_coordinator(hass, call.data.get(SERVICE_ATTR_ENTRY_ID))
    if not coordinator.notify_service:
        raise HomeAssistantError(
            "Indexa Capital notify service is not configured. Set notify_service in the integration options."
        )

    title = call.data.get(SERVICE_ATTR_TITLE, DEFAULT_NOTIFICATION_TITLE)
    message = call.data.get(SERVICE_ATTR_MESSAGE, DEFAULT_NOTIFICATION_MESSAGE)

    try:
        await coordinator.async_send_notification(title=title, message=message)
    except ValueError as err:
        raise HomeAssistantError(str(err)) from err
    except Exception as err:
        raise HomeAssistantError(f"Indexa Capital test notification failed: {err}") from err


def _resolve_target_coordinator(
    hass: HomeAssistant, target_entry_id: str | None
) -> IndexaPortfolioCoordinator:
    """Resolve the coordinator targeted by a service call."""
    if DOMAIN not in hass.data or not hass.data[DOMAIN]:
        raise HomeAssistantError("Indexa Capital is not set up")

    if target_entry_id:
        entry = hass.config_entries.async_get_entry(target_entry_id)
        if entry is None or entry.domain != DOMAIN or target_entry_id not in hass.data[DOMAIN]:
            raise HomeAssistantError(f"Unknown Indexa Capital entry_id: {target_entry_id}")
        return hass.data[DOMAIN][target_entry_id][DATA_COORDINATOR]

    if len(hass.data[DOMAIN]) != 1:
        raise HomeAssistantError(
            "Multiple Indexa Capital entries are configured; provide entry_id to target one."
        )

    sole_entry_id = next(iter(hass.data[DOMAIN]))
    return hass.data[DOMAIN][sole_entry_id][DATA_COORDINATOR]
