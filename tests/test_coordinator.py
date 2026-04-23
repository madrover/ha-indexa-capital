"""Tests for coordinator scheduling behavior."""

from __future__ import annotations

from datetime import datetime
from zoneinfo import ZoneInfo

import pytest
from homeassistant.exceptions import ConfigEntryNotReady

from custom_components.indexa_capital.api import IndexaApiError, snapshot_to_dict
from custom_components.indexa_capital.const import CONF_NOTIFY_SERVICE
from custom_components.indexa_capital.coordinator import IndexaPortfolioCoordinator


class FakeClient:
    """Simple client returning predefined snapshots."""

    def __init__(self, snapshots):
        self.snapshots = list(snapshots)
        self.token_fingerprint = "fingerprint"

    async def async_fetch_portfolio_snapshot(self):
        if not self.snapshots:
            return None
        snapshot = self.snapshots.pop(0)
        if isinstance(snapshot, Exception):
            raise snapshot
        return snapshot


class FakeStore:
    """In-memory replacement for Home Assistant storage in tests."""

    def __init__(self, payload=None):
        self.payload = payload
        self.saved_payloads = []

    async def async_load(self):
        return self.payload

    async def async_save(self, payload):
        self.payload = payload
        self.saved_payloads.append(payload)


async def test_refresh_stops_after_fresh_data(hass, mock_entry, sample_snapshot):
    """A fresh snapshot should stop retries and persist success state."""
    mock_entry.add_to_hass(hass)
    client = FakeClient([sample_snapshot, sample_snapshot])
    coordinator = IndexaPortfolioCoordinator(hass, mock_entry, client)
    coordinator._local_now = lambda: datetime(2026, 4, 22, 7, 0, tzinfo=ZoneInfo("Europe/Madrid"))
    await coordinator.async_initialize()
    coordinator._local_now = lambda: datetime(2026, 4, 22, 8, 0, tzinfo=ZoneInfo("Europe/Madrid"))
    coordinator.runtime_state.last_fresh_date = "2026-04-21"
    coordinator.runtime_state.awaiting_fresh_data = True
    await coordinator._async_attempt_refresh("test")

    assert coordinator.data == sample_snapshot
    assert coordinator.runtime_state.last_fresh_date == "2026-04-22"
    assert coordinator.runtime_state.awaiting_fresh_data is False


async def test_no_new_date_keeps_previous_state(hass, mock_entry, sample_snapshot):
    """A stale snapshot should leave the last good data untouched."""
    mock_entry.add_to_hass(hass)
    client = FakeClient([sample_snapshot, sample_snapshot])
    coordinator = IndexaPortfolioCoordinator(hass, mock_entry, client)
    coordinator._local_now = lambda: datetime(
        2026, 4, 22, 7, 15, tzinfo=ZoneInfo("Europe/Madrid")
    )
    await coordinator.async_initialize()
    coordinator._local_now = lambda: datetime(
        2026, 4, 22, 8, 15, tzinfo=ZoneInfo("Europe/Madrid")
    )
    coordinator.runtime_state.last_fresh_date = "2026-04-22"
    coordinator.runtime_state.awaiting_fresh_data = True
    previous_data = coordinator.data
    await coordinator._async_attempt_refresh("test")

    assert coordinator.data == previous_data
    assert coordinator.runtime_state.awaiting_fresh_data is True


async def test_cutoff_stops_retries(hass, mock_entry, sample_snapshot):
    """The window end should stop retries without clearing data."""
    mock_entry.add_to_hass(hass)
    client = FakeClient([sample_snapshot])
    coordinator = IndexaPortfolioCoordinator(hass, mock_entry, client)
    coordinator._local_now = lambda: datetime(
        2026, 4, 22, 7, 59, tzinfo=ZoneInfo("Europe/Madrid")
    )
    await coordinator.async_initialize()
    coordinator._local_now = lambda: datetime(
        2026, 4, 22, 10, 59, tzinfo=ZoneInfo("Europe/Madrid")
    )
    coordinator.runtime_state.awaiting_fresh_data = True
    await coordinator._async_handle_window_end()

    assert coordinator.runtime_state.awaiting_fresh_data is False
    assert coordinator.data == sample_snapshot


async def test_notification_failure_does_not_undo_refresh_state(hass, mock_entry, sample_snapshot):
    """A notify error should not undo a successful fresh-data refresh."""
    mock_entry.add_to_hass(hass)
    hass.config_entries.async_update_entry(
        mock_entry,
        options={
            **mock_entry.options,
            CONF_NOTIFY_SERVICE: "notify.mobile_app_iphone",
        },
    )
    client = FakeClient([sample_snapshot, sample_snapshot])
    coordinator = IndexaPortfolioCoordinator(hass, mock_entry, client)
    coordinator._store = FakeStore()
    coordinator._local_now = lambda: datetime(2026, 4, 22, 7, 0, tzinfo=ZoneInfo("Europe/Madrid"))
    await coordinator.async_initialize()
    coordinator._local_now = lambda: datetime(2026, 4, 22, 8, 0, tzinfo=ZoneInfo("Europe/Madrid"))
    coordinator.runtime_state.last_fresh_date = "2026-04-21"
    coordinator.runtime_state.awaiting_fresh_data = True

    async def _raise_notify(call):
        raise RuntimeError("notify failed")

    hass.services.async_register("notify", "mobile_app_iphone", _raise_notify)

    await coordinator._async_attempt_refresh("test")

    assert coordinator.runtime_state.last_fresh_date == "2026-04-22"
    assert coordinator.runtime_state.awaiting_fresh_data is False
    assert coordinator.runtime_state.last_notification_date is None
    assert (
        coordinator._store.payload["runtime_state"]["last_successful_refresh_date"]
        == "2026-04-22"
    )


async def test_initialize_uses_stored_snapshot_on_transient_api_failure(
    hass, mock_entry, sample_snapshot
):
    """Startup should restore the last good snapshot when the API is temporarily unavailable."""
    mock_entry.add_to_hass(hass)
    client = FakeClient([IndexaApiError("temporary outage")])
    coordinator = IndexaPortfolioCoordinator(hass, mock_entry, client)
    coordinator._store = FakeStore(
        {
            "runtime_state": {
                "last_fresh_date": "2026-04-21",
                "last_notification_date": None,
                "last_successful_refresh_date": "2026-04-21",
                "awaiting_fresh_data": False,
            },
            "snapshot": snapshot_to_dict(sample_snapshot),
        }
    )
    coordinator._local_now = lambda: datetime(2026, 4, 22, 7, 0, tzinfo=ZoneInfo("Europe/Madrid"))

    await coordinator.async_initialize()

    assert coordinator.data == sample_snapshot


async def test_initialize_without_stored_snapshot_raises_not_ready(hass, mock_entry):
    """Startup should still fail cleanly when there is no snapshot to restore."""
    mock_entry.add_to_hass(hass)
    client = FakeClient([IndexaApiError("temporary outage")])
    coordinator = IndexaPortfolioCoordinator(hass, mock_entry, client)
    coordinator._store = FakeStore()
    coordinator._local_now = lambda: datetime(2026, 4, 22, 7, 0, tzinfo=ZoneInfo("Europe/Madrid"))

    with pytest.raises(ConfigEntryNotReady):
        await coordinator.async_initialize()
