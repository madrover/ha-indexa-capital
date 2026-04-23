"""Normalization tests for live Indexa performance payload shapes."""

from __future__ import annotations

from aiohttp import ClientSession

from custom_components.indexa_capital.api import IndexaApiClient


async def test_normalize_account_uses_return_metrics_and_histories():
    """The live performance payload should populate both return metrics and histories."""
    client = IndexaApiClient(session=ClientSession(), token="token")
    try:
        snapshot = client._normalize_account(
            "I2FUMYYM",
            {},
            {
                "performance_chart": {
                    "portfolio": [
                        {
                            "date": "2026-04-21",
                            "total_amount": 38000.0,
                        },
                        {
                            "date": "2026-04-22",
                            "total_amount": 38624.68634599999,
                        },
                    ]
                },
                "return": {
                    "investment": 24511.73,
                    "pl": 14112.956345999992,
                    "time_return": 0.5758121846713904,
                    "money_return": 0.6123456789,
                    "index": {
                        "20260421": 1.56,
                        "20260422": 1.57,
                    },
                }
            },
        )
    finally:
        await client._session.close()

    assert snapshot.invested_amount == 24511.73
    assert snapshot.time_weighted_performance_percentage == 57.58121846713904
    assert snapshot.money_weighted_performance_percentage == 61.23456789
    assert snapshot.latest_history_date is not None
    assert snapshot.latest_history_date.isoformat() == "2026-04-22"
    assert snapshot.latest_history_value == 1.57
    assert snapshot.time_return_index == {
        "2026-04-21": 1.56,
        "2026-04-22": 1.57,
    }
    assert snapshot.portfolio_value_history == {
        "2026-04-21": 38000.0,
        "2026-04-22": 38624.68634599999,
    }


async def test_normalize_account_uses_type_based_fallback_name():
    """When Indexa exposes no label, use a more descriptive type-based fallback."""
    client = IndexaApiClient(session=ClientSession(), token="token")
    try:
        snapshot = client._normalize_account(
            "P7LVK1AS",
            {},
            {
                "return": {
                    "investment": 10241.56,
                    "pl": 4571.31,
                    "time_return": 0.7065853618586499,
                    "index": {"20260421": 1.70658536185865},
                }
            },
            {"type": "pension"},
        )
    finally:
        await client._session.close()

    assert snapshot.display_name == "Indexa Pension P7LVK1AS"


async def test_infer_external_cash_flows_from_daily_values_and_index():
    """External-flow inference should derive net flows from values and return index."""
    client = IndexaApiClient(session=ClientSession(), token="token")
    try:
        cash_flows = client._infer_external_cash_flows(
            {
                "2026-04-21": 1.0,
                "2026-04-22": 1.1,
                "2026-04-23": 1.1,
            },
            {
                "2026-04-21": 100.0,
                "2026-04-22": 110.0,
                "2026-04-23": 240.0,
            },
        )
    finally:
        await client._session.close()

    assert cash_flows == {
        "2026-04-21": -100.0,
        "2026-04-23": -130.0,
    }
