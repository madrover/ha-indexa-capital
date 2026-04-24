"""Historical statistics backfill support for Indexa Capital."""

from __future__ import annotations

import logging
from dataclasses import dataclass
from datetime import UTC, date, datetime, timedelta
from typing import Any

import voluptuous as vol
from homeassistant.components.recorder import DOMAIN as RECORDER_DOMAIN
from homeassistant.components.recorder import get_instance
from homeassistant.components.recorder.models import StatisticMeanType
from homeassistant.components.recorder.statistics import (
    async_import_statistics,
    get_metadata,
    statistics_during_period,
)
from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant, ServiceCall
from homeassistant.exceptions import HomeAssistantError
from homeassistant.helpers import config_validation as cv
from homeassistant.helpers import entity_registry as er

from .api import IndexaApiError, IndexaAuthError
from .const import (
    DATA_COORDINATOR,
    DOMAIN,
    SERVICE_ATTR_END_DATE,
    SERVICE_ATTR_ENTRY_ID,
    SERVICE_ATTR_START_DATE,
    SERVICE_BACKFILL_STATISTICS,
)
from .coordinator import IndexaPortfolioCoordinator
from .sensor import ACCOUNT_SENSORS, AGGREGATE_SENSORS

_LOGGER = logging.getLogger(__name__)

_SERVICE_SCHEMA = vol.Schema(
    {
        vol.Optional(SERVICE_ATTR_ENTRY_ID): cv.string,
        vol.Optional(SERVICE_ATTR_START_DATE): cv.date,
        vol.Optional(SERVICE_ATTR_END_DATE): cv.date,
    }
)


@dataclass(frozen=True, slots=True)
class BackfillResult:
    """Summary of a statistics backfill run."""

    entry_id: str
    imported_points: int
    statistic_ids: tuple[str, ...]


def async_register_services(hass: HomeAssistant) -> None:
    """Register integration services."""
    if hass.services.has_service(DOMAIN, SERVICE_BACKFILL_STATISTICS):
        return

    async def _handle_backfill_service(call: ServiceCall) -> None:
        await async_handle_backfill_service(hass, call)

    hass.services.async_register(
        DOMAIN,
        SERVICE_BACKFILL_STATISTICS,
        _handle_backfill_service,
        schema=_SERVICE_SCHEMA,
    )


async def async_unregister_services(hass: HomeAssistant) -> None:
    """Unregister integration services."""
    if hass.services.has_service(DOMAIN, SERVICE_BACKFILL_STATISTICS):
        hass.services.async_remove(DOMAIN, SERVICE_BACKFILL_STATISTICS)


async def async_handle_backfill_service(hass: HomeAssistant, call: ServiceCall) -> None:
    """Handle the historical statistics backfill service."""
    if RECORDER_DOMAIN not in hass.config.components:
        raise HomeAssistantError("Recorder must be enabled to backfill Indexa statistics")
    if DOMAIN not in hass.data or not hass.data[DOMAIN]:
        raise HomeAssistantError("Indexa Capital is not set up")

    start_date: date | None = call.data.get(SERVICE_ATTR_START_DATE)
    end_date: date | None = call.data.get(SERVICE_ATTR_END_DATE)
    if start_date and end_date and start_date > end_date:
        raise HomeAssistantError("start_date must be on or before end_date")

    target_entry_id = call.data.get(SERVICE_ATTR_ENTRY_ID)
    if target_entry_id:
        entry = hass.config_entries.async_get_entry(target_entry_id)
        if entry is None or entry.domain != DOMAIN or target_entry_id not in hass.data[DOMAIN]:
            raise HomeAssistantError(f"Unknown Indexa Capital entry_id: {target_entry_id}")
        target_entries = [entry]
    else:
        target_entries = [
            hass.config_entries.async_get_entry(entry_id)
            for entry_id in hass.data[DOMAIN]
            if hass.config_entries.async_get_entry(entry_id) is not None
        ]

    results: list[BackfillResult] = []
    for entry in target_entries:
        assert entry is not None
        coordinator: IndexaPortfolioCoordinator = hass.data[DOMAIN][entry.entry_id][
            DATA_COORDINATOR
        ]
        results.append(
            await async_backfill_entry_statistics(
                hass,
                entry,
                coordinator,
                start_date=start_date,
                end_date=end_date,
            )
        )

    total_points = sum(result.imported_points for result in results)
    _LOGGER.info(
        "Completed Indexa Capital historical backfill for %s config entr%s (%s imported points)",
        len(results),
        "y" if len(results) == 1 else "ies",
        total_points,
    )


async def async_backfill_entry_statistics(
    hass: HomeAssistant,
    entry: ConfigEntry,
    coordinator: IndexaPortfolioCoordinator,
    *,
    start_date: date | None = None,
    end_date: date | None = None,
) -> BackfillResult:
    """Import historical statistics for a single config entry."""
    try:
        snapshot = await coordinator.client.async_fetch_portfolio_snapshot()
    except IndexaAuthError as err:
        raise HomeAssistantError("Indexa authentication failed during statistics backfill") from err
    except IndexaApiError as err:
        raise HomeAssistantError(
            f"Indexa API request failed during statistics backfill: {err}"
        ) from err

    registry = er.async_get(hass)
    entity_entries = {
        registry_entry.unique_id: registry_entry
        for registry_entry in er.async_entries_for_config_entry(registry, entry.entry_id)
        if registry_entry.domain == "sensor"
    }
    if not entity_entries:
        raise HomeAssistantError("No Indexa sensor entities are registered for this config entry")

    statistics_payloads = _build_statistics_payloads(
        coordinator,
        snapshot,
        entity_entries,
        await hass.async_add_executor_job(
            _get_existing_metadata,
            hass,
            {registry_entry.entity_id for registry_entry in entity_entries.values()},
        ),
        start_date=start_date,
        end_date=end_date,
    )

    imported_points = 0
    imported_statistic_ids: list[str] = []
    for metadata, statistic_rows in statistics_payloads:
        if not statistic_rows:
            continue

        existing_starts = await hass.async_add_executor_job(
            _existing_statistic_starts,
            hass,
            metadata["statistic_id"],
            statistic_rows[0]["start"],
            statistic_rows[-1]["start"] + timedelta(hours=1),
        )
        rows_to_import = [
            row
            for row in statistic_rows
            if row["start"].isoformat() not in existing_starts
        ]
        if not rows_to_import:
            continue

        async_import_statistics(hass, metadata, rows_to_import)
        imported_points += len(rows_to_import)
        imported_statistic_ids.append(metadata["statistic_id"])

    if imported_points:
        await get_instance(hass).async_block_till_done()

    return BackfillResult(
        entry_id=entry.entry_id,
        imported_points=imported_points,
        statistic_ids=tuple(imported_statistic_ids),
    )


def _build_statistics_payloads(
    coordinator: IndexaPortfolioCoordinator,
    snapshot,
    entity_entries: dict[str, er.RegistryEntry],
    existing_metadata: dict[str, tuple[int, dict[str, Any]]],
    *,
    start_date: date | None,
    end_date: date | None,
) -> list[tuple[dict[str, Any], list[dict[str, Any]]]]:
    """Build Recorder statistics payloads for all known Indexa sensor entities."""
    metric_rows: dict[str, list[dict[str, Any]]] = {}
    metric_metadata: dict[str, dict[str, Any]] = {}

    for history_date in snapshot.history_dates():
        parsed_date = date.fromisoformat(history_date)
        if start_date and parsed_date < start_date:
            continue
        if end_date and parsed_date > end_date:
            continue

        dated_snapshot = snapshot.snapshot_at(history_date)
        if dated_snapshot is None:
            continue
        statistic_start = datetime.combine(parsed_date, datetime.min.time(), tzinfo=UTC)

        for account in dated_snapshot.accounts:
            for description in ACCOUNT_SENSORS:
                unique_id = (
                    f"{coordinator.client.token_fingerprint}_{account.account_number}_{description.key}"
                )
                registry_entry = entity_entries.get(unique_id)
                if registry_entry is None:
                    continue
                value = description.value_fn(account, coordinator)
                if value is None:
                    continue
                unit = description.native_unit_of_measurement or account.currency
                metric_metadata.setdefault(
                    registry_entry.entity_id,
                    _build_statistic_metadata(registry_entry, unit, existing_metadata),
                )
                metric_rows.setdefault(registry_entry.entity_id, []).append(
                    _build_statistic_row(statistic_start, float(value))
                )

        for description in AGGREGATE_SENSORS:
            unique_id = f"{coordinator.client.token_fingerprint}_{description.key}"
            registry_entry = entity_entries.get(unique_id)
            if registry_entry is None:
                continue
            value = description.value_fn(dated_snapshot, coordinator)
            if value is None:
                continue
            unit = description.native_unit_of_measurement or dated_snapshot.currency
            metric_metadata.setdefault(
                registry_entry.entity_id,
                _build_statistic_metadata(registry_entry, unit, existing_metadata),
            )
            metric_rows.setdefault(registry_entry.entity_id, []).append(
                _build_statistic_row(statistic_start, float(value))
            )

    return [
        (metric_metadata[entity_id], rows)
        for entity_id, rows in metric_rows.items()
    ]


def _build_statistic_metadata(
    registry_entry: er.RegistryEntry,
    unit_of_measurement: str,
    existing_metadata: dict[str, tuple[int, dict[str, Any]]],
) -> dict[str, Any]:
    """Build Recorder statistic metadata for an entity."""
    if registry_entry.entity_id in existing_metadata:
        _metadata_id, metadata = existing_metadata[registry_entry.entity_id]
        return dict(metadata)

    return {
        "has_sum": False,
        "mean_type": StatisticMeanType.ARITHMETIC,
        "name": registry_entry.original_name or registry_entry.entity_id,
        "source": RECORDER_DOMAIN,
        "statistic_id": registry_entry.entity_id,
        "unit_class": None,
        "unit_of_measurement": unit_of_measurement,
    }


def _build_statistic_row(start: datetime, value: float) -> dict[str, Any]:
    """Build a Recorder statistic row for a measurement sensor."""
    return {
        "start": start,
        "mean": value,
        "min": value,
        "max": value,
    }


def _existing_statistic_starts(
    hass: HomeAssistant,
    statistic_id: str,
    start_time: datetime,
    end_time: datetime,
) -> set[str]:
    """Return already-stored statistic timestamps for a statistic ID."""
    existing_rows = statistics_during_period(
        hass,
        start_time,
        end_time,
        {statistic_id},
        "hour",
        None,
        {"mean"},
    )
    return {
        row["start"].isoformat()
        for row in existing_rows.get(statistic_id, [])
        if "start" in row
    }


def _get_existing_metadata(
    hass: HomeAssistant, statistic_ids: set[str]
) -> dict[str, tuple[int, dict[str, Any]]]:
    """Fetch existing Recorder metadata for the requested statistic IDs."""
    return get_metadata(hass, statistic_ids=statistic_ids)
