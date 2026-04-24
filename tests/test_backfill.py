"""Tests for historical statistics backfill."""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import date

import pytest
from homeassistant.helpers import entity_registry as er

from custom_components.indexa_capital import backfill
from custom_components.indexa_capital.backfill import (
    _normalize_statistic_start,
    async_backfill_entry_statistics,
    async_register_services,
)
from custom_components.indexa_capital.const import (
    DATA_COORDINATOR,
    DOMAIN,
    SERVICE_ATTR_END_DATE,
    SERVICE_ATTR_START_DATE,
    SERVICE_BACKFILL_STATISTICS,
)
from custom_components.indexa_capital.coordinator import IndexaPortfolioCoordinator
from custom_components.indexa_capital.models import (
    IndexaAccountSnapshot,
    IndexaPortfolioSnapshot,
)


class FakeClient:
    """Simple fake client returning a predefined snapshot."""

    def __init__(self, snapshot):
        self.snapshot = snapshot
        self.token_fingerprint = "fingerprint"

    async def async_fetch_portfolio_snapshot(self):
        return self.snapshot


@dataclass
class FakeRecorder:
    """Minimal recorder boundary for backfill tests."""

    imported_rows: dict[str, list[dict]] = field(default_factory=dict)

    async def async_block_till_done(self) -> None:
        """Mirror the recorder API used by the integration."""

    async def async_add_executor_job(self, target, *args):
        """Mirror the recorder executor helper used by the integration."""
        return target(*args)


def _history_snapshot() -> IndexaPortfolioSnapshot:
    """Return a simple daily history snapshot suitable for statistics backfill tests."""
    return IndexaPortfolioSnapshot(
        accounts=[
            IndexaAccountSnapshot(
                account_number="ACC1",
                display_name="Retirement",
                currency="EUR",
                invested_amount=100.0,
                performance_amount=20.0,
                time_weighted_performance_percentage=20.0,
                money_weighted_performance_percentage=20.0,
                latest_history_date=date(2026, 4, 22),
                latest_history_value=1.2,
                time_return_index={
                    "2026-04-20": 1.0,
                    "2026-04-21": 1.1,
                    "2026-04-22": 1.2,
                },
                portfolio_value_history={
                    "2026-04-20": 100.0,
                    "2026-04-21": 110.0,
                    "2026-04-22": 120.0,
                },
                external_cash_flow_history={
                    "2026-04-20": -100.0,
                },
            )
        ]
    )


def _create_sensor_registry_entries(hass, mock_entry):
    """Create registry entries for a pair of sensors used in backfill tests."""
    registry = er.async_get(hass)
    account_entry = registry.async_get_or_create(
        "sensor",
        DOMAIN,
        "fingerprint_ACC1_performance_percentage",
        config_entry=mock_entry,
        original_name="Retirement Performance percentage",
        unit_of_measurement="%",
    )
    aggregate_entry = registry.async_get_or_create(
        "sensor",
        DOMAIN,
        "fingerprint_total_performance_percentage",
        config_entry=mock_entry,
        original_name="Total Performance percentage",
        unit_of_measurement="%",
    )
    return account_entry, aggregate_entry


async def test_backfill_imports_and_skips_existing_statistics(
    hass, mock_entry, monkeypatch
):
    """Backfill should import entity statistics once and skip them on rerun."""
    recorder = FakeRecorder()

    def _import_statistics(_hass, metadata, rows):
        recorder.imported_rows.setdefault(metadata["statistic_id"], []).extend(rows)

    def _existing_starts(_hass, statistic_id, _start_time, _end_time):
        return {
            row["start"].isoformat()
            for row in recorder.imported_rows.get(statistic_id, [])
        }

    monkeypatch.setattr(backfill, "async_import_statistics", _import_statistics)
    monkeypatch.setattr(backfill, "_existing_statistic_starts", _existing_starts)
    monkeypatch.setattr(backfill, "_get_existing_metadata", lambda _hass, _ids: {})
    monkeypatch.setattr(backfill, "get_instance", lambda _hass: recorder)

    snapshot = _history_snapshot()
    mock_entry.add_to_hass(hass)
    coordinator = IndexaPortfolioCoordinator(hass, mock_entry, FakeClient(snapshot))
    await coordinator.async_initialize()
    account_entry, aggregate_entry = _create_sensor_registry_entries(hass, mock_entry)

    first_result = await async_backfill_entry_statistics(hass, mock_entry, coordinator)
    second_result = await async_backfill_entry_statistics(hass, mock_entry, coordinator)

    assert first_result.imported_points == 6
    assert second_result.imported_points == 0
    account_means = [row["mean"] for row in recorder.imported_rows[account_entry.entity_id]]
    aggregate_means = [row["mean"] for row in recorder.imported_rows[aggregate_entry.entity_id]]
    assert account_means == pytest.approx([0.0, 10.0, 20.0])
    assert aggregate_means == pytest.approx([0.0, 10.0, 20.0])


async def test_backfill_service_honors_date_filters(hass, mock_entry, monkeypatch):
    """The service should support scoped historical imports by date."""
    recorder = FakeRecorder()

    def _import_statistics(_hass, metadata, rows):
        recorder.imported_rows.setdefault(metadata["statistic_id"], []).extend(rows)

    def _existing_starts(_hass, statistic_id, _start_time, _end_time):
        return {
            row["start"].isoformat()
            for row in recorder.imported_rows.get(statistic_id, [])
        }

    monkeypatch.setattr(backfill, "async_import_statistics", _import_statistics)
    monkeypatch.setattr(backfill, "_existing_statistic_starts", _existing_starts)
    monkeypatch.setattr(backfill, "_get_existing_metadata", lambda _hass, _ids: {})
    monkeypatch.setattr(backfill, "get_instance", lambda _hass: recorder)
    hass.config.components.add("recorder")

    snapshot = _history_snapshot()
    mock_entry.add_to_hass(hass)
    coordinator = IndexaPortfolioCoordinator(hass, mock_entry, FakeClient(snapshot))
    await coordinator.async_initialize()
    account_entry, _ = _create_sensor_registry_entries(hass, mock_entry)
    hass.data.setdefault(DOMAIN, {})[mock_entry.entry_id] = {DATA_COORDINATOR: coordinator}
    async_register_services(hass)

    await hass.services.async_call(
        DOMAIN,
        SERVICE_BACKFILL_STATISTICS,
        {
            SERVICE_ATTR_START_DATE: date(2026, 4, 21),
            SERVICE_ATTR_END_DATE: date(2026, 4, 22),
        },
        blocking=True,
    )

    account_means = [row["mean"] for row in recorder.imported_rows[account_entry.entity_id]]
    assert account_means == pytest.approx([10.0, 20.0])


def test_normalize_statistic_start_accepts_float_timestamp():
    """Recorder start values may come back as UNIX timestamps."""
    assert _normalize_statistic_start(1776988800.0) == "2026-04-24T00:00:00+00:00"
