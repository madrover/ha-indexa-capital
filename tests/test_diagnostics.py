"""Diagnostics tests for Indexa Capital."""

from __future__ import annotations

from custom_components.indexa_capital.const import DATA_COORDINATOR, DOMAIN
from custom_components.indexa_capital.coordinator import IndexaPortfolioCoordinator
from custom_components.indexa_capital.diagnostics import async_get_config_entry_diagnostics


class FakeClient:
    """Simple fake client."""

    def __init__(self, snapshot):
        self.snapshot = snapshot
        self.token_fingerprint = "fingerprint"

    async def async_fetch_portfolio_snapshot(self):
        return self.snapshot


async def test_diagnostics_redact_sensitive_fields(hass, mock_entry, sample_snapshot):
    """Diagnostics should redact secrets and personal data."""
    mock_entry.add_to_hass(hass)
    coordinator = IndexaPortfolioCoordinator(hass, mock_entry, FakeClient(sample_snapshot))
    await coordinator.async_initialize()
    hass.data.setdefault(DOMAIN, {})[mock_entry.entry_id] = {DATA_COORDINATOR: coordinator}

    diagnostics = await async_get_config_entry_diagnostics(hass, mock_entry)

    assert diagnostics["entry"]["data"]["api_token"] == "**REDACTED**"
    assert diagnostics["snapshot"]["accounts"][0]["account_number"] == "**REDACTED**"
