"""Normalization tests for live Indexa performance payload shapes."""

from __future__ import annotations

from aiohttp import ClientSession

from custom_components.indexa_capital.api import IndexaApiClient


async def test_normalize_account_uses_return_investment_and_index_history():
    """The live performance payload should populate investment and latest history data."""
    client = IndexaApiClient(session=ClientSession(), token="token")
    try:
        snapshot = client._normalize_account(
            "I2FUMYYM",
            {},
            {
                "return": {
                    "investment": 24511.73,
                    "pl": 14112.956345999992,
                    "time_return": 0.5758121846713904,
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
    assert snapshot.latest_history_date is not None
    assert snapshot.latest_history_date.isoformat() == "2026-04-22"
    assert snapshot.latest_history_value == 1.57


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
