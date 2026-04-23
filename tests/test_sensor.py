"""Tests for Indexa sensors."""

from __future__ import annotations

import pytest

from custom_components.indexa_capital.coordinator import IndexaPortfolioCoordinator
from custom_components.indexa_capital.sensor import (
    ACCOUNT_SENSORS,
    AGGREGATE_SENSORS,
    IndexaAccountSensor,
    IndexaAggregateSensor,
)


class FakeClient:
    """Simple fake client."""

    def __init__(self, snapshot):
        self.snapshot = snapshot
        self.token_fingerprint = "fingerprint"

    async def async_fetch_portfolio_snapshot(self):
        return self.snapshot


async def test_sensors_created_and_weighted(hass, mock_entry, sample_snapshot):
    """Sensors should expose per-account and aggregate values."""
    mock_entry.add_to_hass(hass)
    coordinator = IndexaPortfolioCoordinator(hass, mock_entry, FakeClient(sample_snapshot))
    await coordinator.async_initialize()
    account_sensor = IndexaAccountSensor(
        coordinator,
        mock_entry,
        "ACC1",
        ACCOUNT_SENSORS[0],
    )
    aggregate_sensor = IndexaAggregateSensor(coordinator, mock_entry, AGGREGATE_SENSORS[1])

    assert account_sensor.native_value == 50.0
    assert account_sensor.native_unit_of_measurement == "EUR"
    assert aggregate_sensor.native_value == pytest.approx(3.5)
