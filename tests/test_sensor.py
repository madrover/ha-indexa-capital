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
    account_descriptions = {description.key: description for description in ACCOUNT_SENSORS}
    aggregate_descriptions = {description.key: description for description in AGGREGATE_SENSORS}

    contribution_sensor = IndexaAccountSensor(
        coordinator,
        mock_entry,
        "ACC1",
        account_descriptions["contributions_amount"],
    )
    performance_amount_sensor = IndexaAccountSensor(
        coordinator,
        mock_entry,
        "ACC1",
        account_descriptions["performance_amount"],
    )
    time_weighted_sensor = IndexaAccountSensor(
        coordinator,
        mock_entry,
        "ACC1",
        account_descriptions["performance_percentage"],
    )
    money_weighted_sensor = IndexaAccountSensor(
        coordinator,
        mock_entry,
        "ACC1",
        account_descriptions["money_weighted_performance_percentage"],
    )
    total_contribution_sensor = IndexaAggregateSensor(
        coordinator, mock_entry, aggregate_descriptions["total_contributions_amount"]
    )
    total_time_weighted_sensor = IndexaAggregateSensor(
        coordinator, mock_entry, aggregate_descriptions["total_performance_percentage"]
    )
    total_money_weighted_sensor = IndexaAggregateSensor(
        coordinator,
        mock_entry,
        aggregate_descriptions["total_money_weighted_performance_percentage"],
    )

    assert contribution_sensor.native_value == 200.0
    assert contribution_sensor.native_unit_of_measurement == "EUR"
    assert performance_amount_sensor.native_value == 40.0
    assert performance_amount_sensor.native_unit_of_measurement == "EUR"
    assert time_weighted_sensor.native_value == 10.0
    assert money_weighted_sensor.native_value == pytest.approx(27.2117940390)
    assert total_contribution_sensor.native_value == 400.0
    assert total_time_weighted_sensor.native_value == pytest.approx(3.3333333333)
    assert total_money_weighted_sensor.native_value == pytest.approx(11.4730648425)
    assert total_money_weighted_sensor.extra_state_attributes["notification_configured"] is False
    assert (
        total_money_weighted_sensor.extra_state_attributes["last_notification_attempt_at"] is None
    )
    assert total_money_weighted_sensor.extra_state_attributes["last_refresh_check_outcome"] in {
        "accepted_fresher_snapshot",
        "already_succeeded_today",
        None,
    }


async def test_runtime_state_listener_updates_after_notification_attempt(
    hass, mock_entry, sample_snapshot
):
    """Runtime-only notification state changes should notify listeners."""
    mock_entry.add_to_hass(hass)
    coordinator = IndexaPortfolioCoordinator(hass, mock_entry, FakeClient(sample_snapshot))
    await coordinator.async_initialize()
    calls = 0

    def _listener() -> None:
        nonlocal calls
        calls += 1

    remove_listener = coordinator.async_add_listener(_listener)

    async def _notify(call):
        return None

    hass.config_entries.async_update_entry(
        mock_entry,
        options={**mock_entry.options, "notify_service": "notify.mobile_app_iphone"},
    )
    hass.services.async_register("notify", "mobile_app_iphone", _notify)

    await coordinator.async_send_notification(title="Indexa Capital", message="Test")

    remove_listener()
    assert calls >= 1
