"""Shared test fixtures."""

from __future__ import annotations

import sys
from datetime import date
from pathlib import Path

import pytest
from homeassistant.util import dt as dt_util
from pytest_homeassistant_custom_component.common import MockConfigEntry

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from custom_components.indexa_capital.const import (
    CONF_REFRESH_END_TIME,
    CONF_REFRESH_INTERVAL_MINUTES,
    CONF_REFRESH_START_TIME,
    DEFAULT_REFRESH_END_TIME,
    DEFAULT_REFRESH_INTERVAL_MINUTES,
    DEFAULT_REFRESH_START_TIME,
    DOMAIN,
)
from custom_components.indexa_capital.models import (
    IndexaAccountSnapshot,
    IndexaPortfolioSnapshot,
)

pytest_plugins = ["pytest_homeassistant_custom_component"]


@pytest.fixture(autouse=True)
def auto_enable_custom_integrations(enable_custom_integrations):
    """Enable loading integrations from this repository's custom_components directory."""


@pytest.fixture(autouse=True)
def set_test_time_zone(hass):
    """Run scheduler tests in the integration's intended local timezone."""
    time_zone = dt_util.get_time_zone("Europe/Madrid")
    hass.config.time_zone = "Europe/Madrid"
    dt_util.DEFAULT_TIME_ZONE = time_zone


@pytest.fixture
def mock_entry():
    """Return a standard config entry for tests."""
    return MockConfigEntry(
        domain=DOMAIN,
        title="Indexa Capital",
        data={"api_token": "token-123"},
        options={
            CONF_REFRESH_START_TIME: DEFAULT_REFRESH_START_TIME,
            CONF_REFRESH_END_TIME: DEFAULT_REFRESH_END_TIME,
            CONF_REFRESH_INTERVAL_MINUTES: DEFAULT_REFRESH_INTERVAL_MINUTES,
        },
        unique_id="token-fingerprint",
    )


@pytest.fixture
def sample_snapshot():
    """Return a sample normalized snapshot."""
    return IndexaPortfolioSnapshot(
        accounts=[
            IndexaAccountSnapshot(
                account_number="ACC1",
                display_name="Retirement",
                currency="EUR",
                invested_amount=200.0,
                performance_amount=40.0,
                time_weighted_performance_percentage=10.0,
                money_weighted_performance_percentage=27.211794039042097,
                latest_history_date=date(2026, 4, 22),
                latest_history_value=1.1,
                time_return_index={
                    "2026-04-20": 1.0,
                    "2026-04-21": 1.1,
                    "2026-04-22": 1.1,
                },
                portfolio_value_history={
                    "2026-04-20": 100.0,
                    "2026-04-21": 110.0,
                    "2026-04-22": 240.0,
                },
                external_cash_flow_history={
                    "2026-04-20": -100.0,
                    "2026-04-21": -100.0,
                },
            ),
            IndexaAccountSnapshot(
                account_number="ACC2",
                display_name="Savings",
                currency="EUR",
                invested_amount=200.0,
                performance_amount=0.0,
                time_weighted_performance_percentage=0.0,
                money_weighted_performance_percentage=0.0,
                latest_history_date=date(2026, 4, 22),
                latest_history_value=1.0,
                time_return_index={
                    "2026-04-20": 1.0,
                    "2026-04-21": 1.0,
                    "2026-04-22": 1.0,
                },
                portfolio_value_history={
                    "2026-04-20": 200.0,
                    "2026-04-21": 200.0,
                    "2026-04-22": 200.0,
                },
                external_cash_flow_history={
                    "2026-04-20": -200.0,
                },
            ),
        ]
    )
