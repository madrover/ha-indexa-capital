"""Tests for general Indexa services."""

from __future__ import annotations

import pytest
from homeassistant.exceptions import HomeAssistantError

from custom_components.indexa_capital.const import (
    CONF_NOTIFY_SERVICE,
    DATA_COORDINATOR,
    DOMAIN,
    SERVICE_ATTR_MESSAGE,
    SERVICE_ATTR_TITLE,
    SERVICE_SEND_TEST_NOTIFICATION,
)
from custom_components.indexa_capital.coordinator import IndexaPortfolioCoordinator
from custom_components.indexa_capital.services import async_register_services


class FakeClient:
    """Simple fake client."""

    def __init__(self, snapshot):
        self.snapshot = snapshot
        self.token_fingerprint = "fingerprint"

    async def async_fetch_portfolio_snapshot(self):
        return self.snapshot


async def test_send_test_notification_service_calls_notify(
    hass, mock_entry, sample_snapshot
):
    """The test notification service should call the configured notify target."""
    mock_entry.add_to_hass(hass)
    hass.config_entries.async_update_entry(
        mock_entry,
        options={**mock_entry.options, CONF_NOTIFY_SERVICE: "notify.mobile_app_iphone"},
    )
    coordinator = IndexaPortfolioCoordinator(hass, mock_entry, FakeClient(sample_snapshot))
    await coordinator.async_initialize()
    hass.data.setdefault(DOMAIN, {})[mock_entry.entry_id] = {DATA_COORDINATOR: coordinator}
    async_register_services(hass)
    received = {}

    async def _notify(call):
        received.update(call.data)

    hass.services.async_register("notify", "mobile_app_iphone", _notify)

    await hass.services.async_call(
        DOMAIN,
        SERVICE_SEND_TEST_NOTIFICATION,
        {
            SERVICE_ATTR_TITLE: "Test title",
            SERVICE_ATTR_MESSAGE: "Test message",
        },
        blocking=True,
    )

    assert received == {"title": "Test title", "message": "Test message"}
    assert coordinator.runtime_state.last_notification_error is None
    assert coordinator.runtime_state.last_notification_success_at is not None


async def test_send_test_notification_service_requires_configured_notify_service(
    hass, mock_entry, sample_snapshot
):
    """The test notification service should fail clearly without notify_service."""
    mock_entry.add_to_hass(hass)
    coordinator = IndexaPortfolioCoordinator(hass, mock_entry, FakeClient(sample_snapshot))
    await coordinator.async_initialize()
    hass.data.setdefault(DOMAIN, {})[mock_entry.entry_id] = {DATA_COORDINATOR: coordinator}
    async_register_services(hass)

    with pytest.raises(HomeAssistantError, match="notify service is not configured"):
        await hass.services.async_call(
            DOMAIN,
            SERVICE_SEND_TEST_NOTIFICATION,
            blocking=True,
        )


async def test_send_test_notification_service_rejects_invalid_notify_service(
    hass, mock_entry, sample_snapshot
):
    """The test notification service should fail clearly on malformed services."""
    mock_entry.add_to_hass(hass)
    hass.config_entries.async_update_entry(
        mock_entry,
        options={**mock_entry.options, CONF_NOTIFY_SERVICE: "mobile_app_iphone"},
    )
    coordinator = IndexaPortfolioCoordinator(hass, mock_entry, FakeClient(sample_snapshot))
    await coordinator.async_initialize()
    hass.data.setdefault(DOMAIN, {})[mock_entry.entry_id] = {DATA_COORDINATOR: coordinator}
    async_register_services(hass)

    with pytest.raises(HomeAssistantError, match="Notify service is not configured correctly"):
        await hass.services.async_call(
            DOMAIN,
            SERVICE_SEND_TEST_NOTIFICATION,
            blocking=True,
        )
