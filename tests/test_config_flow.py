"""Tests for the Indexa Capital config flow."""

from __future__ import annotations

from datetime import time

from homeassistant.const import CONF_API_TOKEN
from homeassistant.data_entry_flow import FlowResultType

from custom_components.indexa_capital.api import IndexaAuthError, fingerprint_token
from custom_components.indexa_capital.const import (
    CONF_REFRESH_END_TIME,
    CONF_REFRESH_INTERVAL_MINUTES,
    CONF_REFRESH_START_TIME,
    DOMAIN,
)


async def test_user_flow_success(hass, monkeypatch):
    """A valid token should create an entry."""

    async def _validate(hass, data):
        return {
            "title": "Indexa Capital",
            "token_fingerprint": fingerprint_token(data[CONF_API_TOKEN]),
            "profile": {},
        }

    monkeypatch.setattr(
        "custom_components.indexa_capital.config_flow.validate_input",
        _validate,
    )

    result = await hass.config_entries.flow.async_init(
        DOMAIN,
        context={"source": "user"},
        data={CONF_API_TOKEN: "abc123"},
    )

    assert result["type"] is FlowResultType.CREATE_ENTRY
    assert result["data"] == {CONF_API_TOKEN: "abc123"}


async def test_user_flow_invalid_auth(hass, monkeypatch):
    """Invalid auth should show an error."""

    async def _validate(hass, data):
        raise IndexaAuthError

    monkeypatch.setattr(
        "custom_components.indexa_capital.config_flow.validate_input",
        _validate,
    )

    result = await hass.config_entries.flow.async_init(
        DOMAIN,
        context={"source": "user"},
        data={CONF_API_TOKEN: "bad"},
    )

    assert result["type"] is FlowResultType.FORM
    assert result["errors"] == {"base": "invalid_auth"}


async def test_duplicate_token_aborts(hass, mock_entry, monkeypatch):
    """A duplicate fingerprint should abort."""

    mock_entry.add_to_hass(hass)

    async def _validate(hass, data):
        return {
            "title": "Indexa Capital",
            "token_fingerprint": mock_entry.unique_id,
            "profile": {},
        }

    monkeypatch.setattr(
        "custom_components.indexa_capital.config_flow.validate_input",
        _validate,
    )

    result = await hass.config_entries.flow.async_init(
        DOMAIN,
        context={"source": "user"},
        data={CONF_API_TOKEN: "dup"},
    )

    assert result["type"] is FlowResultType.ABORT
    assert result["reason"] == "already_configured"


async def test_reauth_updates_token(hass, mock_entry, monkeypatch):
    """Reauth should replace the stored token."""

    mock_entry.add_to_hass(hass)

    async def _validate(hass, data):
        return {
            "title": "Indexa Capital",
            "token_fingerprint": fingerprint_token(data[CONF_API_TOKEN]),
            "profile": {},
        }

    monkeypatch.setattr(
        "custom_components.indexa_capital.config_flow.validate_input",
        _validate,
    )
    async def _reload(entry_id):
        return None

    monkeypatch.setattr(hass.config_entries, "async_reload", _reload)

    result = await hass.config_entries.flow.async_init(
        DOMAIN,
        context={"source": "reauth", "entry_id": mock_entry.entry_id},
        data=mock_entry.data,
    )
    assert result["type"] is FlowResultType.FORM

    result = await hass.config_entries.flow.async_configure(
        result["flow_id"],
        user_input={CONF_API_TOKEN: "new-token"},
    )

    assert result["type"] is FlowResultType.ABORT
    assert result["reason"] == "reauth_successful"
    assert (
        hass.config_entries.async_get_entry(mock_entry.entry_id).data[CONF_API_TOKEN]
        == "new-token"
    )


async def test_options_flow_serializes_time_defaults(hass, mock_entry):
    """Options form should expose time defaults in a frontend-safe format."""

    mock_entry.add_to_hass(hass)
    result = await hass.config_entries.options.async_init(mock_entry.entry_id)

    assert result["type"] is FlowResultType.FORM
    start_key = next(
        key for key in result["data_schema"].schema if key.schema == CONF_REFRESH_START_TIME
    )
    end_key = next(
        key for key in result["data_schema"].schema if key.schema == CONF_REFRESH_END_TIME
    )
    assert start_key.default() == "08:00:00"
    assert end_key.default() == "11:00:00"


async def test_options_flow_updates_schedule(hass, mock_entry):
    """Options flow should save edited schedule values."""

    mock_entry.add_to_hass(hass)
    result = await hass.config_entries.options.async_init(mock_entry.entry_id)

    result = await hass.config_entries.options.async_configure(
        result["flow_id"],
        user_input={
            CONF_REFRESH_START_TIME: "09:15:00",
            CONF_REFRESH_END_TIME: time(hour=12, minute=30),
            CONF_REFRESH_INTERVAL_MINUTES: 10,
        },
    )

    assert result["type"] is FlowResultType.CREATE_ENTRY
    assert result["data"][CONF_REFRESH_START_TIME] == "09:15:00"
    assert result["data"][CONF_REFRESH_END_TIME] == time(hour=12, minute=30)
    assert result["data"][CONF_REFRESH_INTERVAL_MINUTES] == 10
