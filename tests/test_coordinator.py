"""Tests for coordinator scheduling behavior."""

from __future__ import annotations

import logging
from datetime import datetime
from zoneinfo import ZoneInfo

import pytest
from homeassistant.exceptions import ConfigEntryNotReady

from custom_components.indexa_capital.api import IndexaApiError, snapshot_to_dict
from custom_components.indexa_capital.const import (
    CONF_NOTIFY_SERVICE,
    CONF_REFRESH_END_TIME,
    CONF_REFRESH_START_TIME,
)
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
    assert coordinator.runtime_state.last_refresh_check_trigger == "test"
    assert coordinator.runtime_state.last_refresh_check_outcome == "accepted_fresher_snapshot"


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
    assert coordinator.runtime_state.last_refresh_check_outcome == "stale_snapshot"


async def test_cutoff_stops_retries(hass, mock_entry, sample_snapshot):
    """The window end should stop retries without clearing data."""
    mock_entry.add_to_hass(hass)
    client = FakeClient([sample_snapshot, sample_snapshot])
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


async def test_invalid_refresh_window_falls_back_to_defaults(hass, mock_entry, sample_snapshot):
    """An invalid configured refresh window should fall back to the default schedule."""
    mock_entry.add_to_hass(hass)
    hass.config_entries.async_update_entry(
        mock_entry,
        options={
            **mock_entry.options,
            CONF_REFRESH_START_TIME: "07:30:00",
            CONF_REFRESH_END_TIME: "00:00:00",
        },
    )
    client = FakeClient([sample_snapshot, sample_snapshot])
    coordinator = IndexaPortfolioCoordinator(hass, mock_entry, client)
    coordinator._local_now = lambda: datetime(
        2026, 5, 7, 8, 30, tzinfo=ZoneInfo("Europe/Madrid")
    )
    await coordinator.async_initialize()
    coordinator.runtime_state.last_fresh_date = "2026-04-21"
    coordinator.runtime_state.awaiting_fresh_data = True

    await coordinator._async_attempt_refresh("window_start")

    assert coordinator.refresh_start_time.isoformat() == "08:00:00"
    assert coordinator.refresh_end_time.isoformat() == "13:00:00"
    assert coordinator.runtime_state.last_refresh_check_trigger == "window_start"
    assert coordinator.runtime_state.last_refresh_check_outcome == "accepted_fresher_snapshot"
    assert coordinator.runtime_state.last_fresh_date == "2026-04-22"


async def test_invalid_refresh_window_logs_default_fallback(hass, mock_entry, sample_snapshot, caplog):
    """An invalid configured refresh window should emit a fallback warning."""
    mock_entry.add_to_hass(hass)
    hass.config_entries.async_update_entry(
        mock_entry,
        options={
            **mock_entry.options,
            CONF_REFRESH_START_TIME: "07:30:00",
            CONF_REFRESH_END_TIME: "00:00:00",
        },
    )
    coordinator = IndexaPortfolioCoordinator(hass, mock_entry, FakeClient([sample_snapshot]))
    coordinator._local_now = lambda: datetime(
        2026, 5, 7, 7, 45, tzinfo=ZoneInfo("Europe/Madrid")
    )

    with caplog.at_level(logging.WARNING):
        await coordinator._async_resume_if_needed()

    fallback_record = next(
        record
        for record in caplog.records
        if record.message == "Indexa invalid refresh window configured; using defaults instead"
    )

    assert fallback_record.trigger == "startup_resume"
    assert fallback_record.configured_refresh_start_time == "07:30:00"
    assert fallback_record.configured_refresh_end_time == "00:00:00"
    assert fallback_record.effective_refresh_start_time == "08:00:00"
    assert fallback_record.effective_refresh_end_time == "13:00:00"


async def test_initialize_reconciles_fresh_runtime_state_without_notification(
    hass, mock_entry, sample_snapshot
):
    """Startup fetch should update freshness markers without sending notifications."""
    mock_entry.add_to_hass(hass)
    hass.config_entries.async_update_entry(
        mock_entry,
        options={
            **mock_entry.options,
            CONF_NOTIFY_SERVICE: "notify.mobile_app_iphone",
        },
    )
    coordinator = IndexaPortfolioCoordinator(hass, mock_entry, FakeClient([sample_snapshot]))
    coordinator._store = FakeStore()
    coordinator._local_now = lambda: datetime(
        2026, 4, 22, 7, 0, tzinfo=ZoneInfo("Europe/Madrid")
    )

    await coordinator.async_initialize()

    assert coordinator.runtime_state.last_fresh_date == "2026-04-22"
    assert coordinator.runtime_state.last_successful_refresh_date == "2026-04-22"
    assert coordinator.runtime_state.awaiting_fresh_data is False
    assert coordinator.runtime_state.last_notification_date is None
    assert coordinator.runtime_state.last_notification_attempt_at is None
    assert coordinator.runtime_state.last_refresh_check_trigger == "startup_resume"
    assert coordinator.runtime_state.last_refresh_check_outcome == "already_succeeded_today"


async def test_initialize_notifies_when_startup_detects_fresher_data_within_window(
    hass, mock_entry, sample_snapshot
):
    """Startup should notify when it is the first in-window path to detect fresher data."""
    mock_entry.add_to_hass(hass)
    hass.config_entries.async_update_entry(
        mock_entry,
        options={
            **mock_entry.options,
            CONF_NOTIFY_SERVICE: "notify.mobile_app_iphone",
        },
    )
    coordinator = IndexaPortfolioCoordinator(hass, mock_entry, FakeClient([sample_snapshot]))
    coordinator._store = FakeStore(
        {
            "runtime_state": {
                "last_fresh_date": "2026-04-21",
                "last_notification_date": None,
                "last_notification_attempt_at": None,
                "last_notification_success_at": None,
                "last_notification_error": None,
                "last_successful_refresh_date": "2026-04-21",
                "awaiting_fresh_data": False,
            }
        }
    )
    coordinator._local_now = lambda: datetime(
        2026, 4, 22, 8, 30, tzinfo=ZoneInfo("Europe/Madrid")
    )
    received = {}

    async def _notify(call):
        received.update(call.data)

    hass.services.async_register("notify", "mobile_app_iphone", _notify)

    await coordinator.async_initialize()

    assert coordinator.runtime_state.last_fresh_date == "2026-04-22"
    assert coordinator.runtime_state.last_successful_refresh_date == "2026-04-22"
    assert coordinator.runtime_state.last_notification_date == "2026-04-22"
    assert coordinator.runtime_state.last_notification_success_at is not None
    assert received == {
        "title": "Indexa Capital",
        "message": "Daily portfolio refresh completed for 2026-04-22.",
    }


async def test_initialize_keeps_runtime_state_when_startup_snapshot_is_not_fresher(
    hass, mock_entry, sample_snapshot
):
    """Startup fetch should not overwrite freshness markers for stale snapshots."""
    mock_entry.add_to_hass(hass)
    coordinator = IndexaPortfolioCoordinator(hass, mock_entry, FakeClient([sample_snapshot]))
    coordinator._store = FakeStore(
        {
            "runtime_state": {
                "last_fresh_date": "2026-04-22",
                "last_notification_date": None,
                "last_notification_attempt_at": None,
                "last_notification_success_at": None,
                "last_notification_error": None,
                "last_successful_refresh_date": "2026-04-20",
                "awaiting_fresh_data": True,
            },
            "snapshot": snapshot_to_dict(sample_snapshot),
        }
    )
    coordinator._local_now = lambda: datetime(
        2026, 4, 23, 7, 0, tzinfo=ZoneInfo("Europe/Madrid")
    )

    await coordinator.async_initialize()

    assert coordinator.runtime_state.last_fresh_date == "2026-04-22"
    assert coordinator.runtime_state.last_successful_refresh_date == "2026-04-20"


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
    assert coordinator.runtime_state.last_notification_attempt_at is not None
    assert coordinator.runtime_state.last_notification_success_at is None
    assert coordinator.runtime_state.last_notification_error == "notify failed"
    assert (
        coordinator._store.payload["runtime_state"]["last_successful_refresh_date"]
        == "2026-04-22"
    )


async def test_notification_success_records_runtime_state(hass, mock_entry, sample_snapshot):
    """A successful notify call should record notification status."""
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

    async def _notify(call):
        return None

    hass.services.async_register("notify", "mobile_app_iphone", _notify)

    await coordinator._async_attempt_refresh("test")

    assert coordinator.runtime_state.last_notification_date == "2026-04-22"
    assert coordinator.runtime_state.last_notification_attempt_at is not None
    assert coordinator.runtime_state.last_notification_success_at is not None
    assert coordinator.runtime_state.last_notification_error is None


async def test_refresh_logs_stale_snapshot_decision(
    hass, mock_entry, sample_snapshot, caplog
):
    """Scheduled refresh logging should expose stale-snapshot decision context."""
    mock_entry.add_to_hass(hass)
    client = FakeClient([sample_snapshot, sample_snapshot])
    coordinator = IndexaPortfolioCoordinator(hass, mock_entry, client)
    coordinator._local_now = lambda: datetime(
        2026, 4, 22, 7, 0, tzinfo=ZoneInfo("Europe/Madrid")
    )
    await coordinator.async_initialize()
    coordinator._local_now = lambda: datetime(
        2026, 4, 22, 8, 0, tzinfo=ZoneInfo("Europe/Madrid")
    )
    coordinator.runtime_state.last_fresh_date = "2026-04-22"
    coordinator.runtime_state.awaiting_fresh_data = True

    with caplog.at_level(logging.INFO):
        await coordinator._async_attempt_refresh("retry")

    attempt_record = next(
        record for record in caplog.records if record.message == "Indexa refresh attempt starting"
    )
    stale_record = next(
        record
        for record in caplog.records
        if record.message == "Indexa snapshot rejected as not fresher"
    )
    summary_record = next(
        record
        for record in caplog.records
        if record.message == "Indexa refresh did not return a fresher history date"
    )

    assert attempt_record.trigger == "retry"
    assert attempt_record.previous_last_fresh_date == "2026-04-22"
    assert attempt_record.awaiting_fresh_data is True
    assert stale_record.trigger == "retry"
    assert stale_record.latest_history_date == "2026-04-22"
    assert stale_record.previous_last_fresh_date == "2026-04-22"
    assert summary_record.trigger == "retry"
    assert summary_record.latest_history_date == "2026-04-22"


async def test_notification_skip_logs_reason(hass, mock_entry, sample_snapshot, caplog):
    """Scheduled notification skips should log the gate reason with context."""
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
    coordinator._local_now = lambda: datetime(
        2026, 4, 22, 7, 0, tzinfo=ZoneInfo("Europe/Madrid")
    )
    await coordinator.async_initialize()
    coordinator._local_now = lambda: datetime(
        2026, 4, 22, 8, 0, tzinfo=ZoneInfo("Europe/Madrid")
    )
    coordinator.runtime_state.last_fresh_date = "2026-04-21"
    coordinator.runtime_state.last_notification_date = "2026-04-22"
    coordinator.runtime_state.awaiting_fresh_data = True

    with caplog.at_level(logging.INFO):
        await coordinator._async_attempt_refresh("window_start")

    skip_record = next(
        record for record in caplog.records if record.message == "Indexa notification skipped"
    )

    assert skip_record.latest_history_date == "2026-04-22"
    assert skip_record.notify_service_configured is True
    assert skip_record.last_notification_date == "2026-04-22"
    assert skip_record.today == "2026-04-22"


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
                "last_notification_attempt_at": None,
                "last_notification_success_at": None,
                "last_notification_error": None,
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
