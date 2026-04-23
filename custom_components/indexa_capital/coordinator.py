"""Coordinator and scheduler for Indexa Capital."""

from __future__ import annotations

import logging
from collections.abc import Callable
from dataclasses import asdict
from datetime import date, datetime, time, timedelta
from typing import Any

from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant, callback
from homeassistant.exceptions import ConfigEntryAuthFailed, ConfigEntryNotReady
from homeassistant.helpers.event import async_call_later, async_track_point_in_time
from homeassistant.helpers.storage import Store
from homeassistant.helpers.update_coordinator import DataUpdateCoordinator
from homeassistant.util import dt as dt_util

from .api import (
    IndexaApiClient,
    IndexaApiError,
    IndexaAuthError,
    dict_to_snapshot,
    snapshot_to_dict,
)
from .const import (
    CONF_NOTIFY_SERVICE,
    CONF_REFRESH_END_TIME,
    CONF_REFRESH_INTERVAL_MINUTES,
    CONF_REFRESH_START_TIME,
    DEFAULT_REFRESH_END_TIME,
    DEFAULT_REFRESH_INTERVAL_MINUTES,
    DEFAULT_REFRESH_START_TIME,
    DOMAIN,
    STORAGE_KEY,
    STORAGE_VERSION,
)
from .models import IndexaPortfolioSnapshot, IndexaRuntimeState

_LOGGER = logging.getLogger(__name__)


class IndexaPortfolioCoordinator(DataUpdateCoordinator[IndexaPortfolioSnapshot | None]):
    """Manage scheduled daily refresh attempts."""

    config_entry: ConfigEntry

    def __init__(
        self,
        hass: HomeAssistant,
        entry: ConfigEntry,
        client: IndexaApiClient,
    ) -> None:
        super().__init__(hass, _LOGGER, config_entry=entry, name=DOMAIN)
        self.client = client
        self.runtime_state = IndexaRuntimeState()
        self._store = Store[dict[str, Any]](
            hass, STORAGE_VERSION, f"{STORAGE_KEY}_{entry.entry_id}"
        )
        self._unsub_window_start: Callable[[], None] | None = None
        self._unsub_window_end: Callable[[], None] | None = None
        self._unsub_retry: Callable[[], None] | None = None

    async def async_initialize(self) -> None:
        """Load persisted state, perform first refresh, and schedule upcoming work."""
        await self._async_load_state()
        try:
            self.data = await self.client.async_fetch_portfolio_snapshot()
        except IndexaAuthError as err:
            raise ConfigEntryAuthFailed from err
        except IndexaApiError as err:
            if self.data is None:
                raise ConfigEntryNotReady(f"Initial Indexa snapshot fetch failed: {err}") from err
            _LOGGER.warning(
                "Initial Indexa snapshot fetch failed, using stored snapshot instead: %s",
                err,
            )
        else:
            await self._async_save_state()

        self._schedule_next_window_start()
        await self._async_resume_if_needed()

    async def async_shutdown(self) -> None:
        """Tear down timers."""
        self._cancel_retry()
        self._cancel_window_start()
        self._cancel_window_end()

    async def _async_load_state(self) -> None:
        """Load persisted runtime state."""
        if not (stored := await self._store.async_load()):
            return

        if "runtime_state" in stored:
            self.runtime_state = IndexaRuntimeState(**stored["runtime_state"])
            self.data = dict_to_snapshot(stored.get("snapshot"))
            return

        # Backward compatibility for the original runtime-state-only storage payload.
        self.runtime_state = IndexaRuntimeState(**stored)

    async def _async_save_state(self) -> None:
        """Persist runtime state."""
        await self._store.async_save(
            {
                "runtime_state": asdict(self.runtime_state),
                "snapshot": snapshot_to_dict(self.data),
            }
        )

    @property
    def refresh_start_time(self):
        """Return the configured refresh start time."""
        return self._coerce_time(
            self.config_entry.options.get(CONF_REFRESH_START_TIME, DEFAULT_REFRESH_START_TIME)
        )

    @property
    def refresh_end_time(self):
        """Return the configured refresh end time."""
        return self._coerce_time(
            self.config_entry.options.get(CONF_REFRESH_END_TIME, DEFAULT_REFRESH_END_TIME)
        )

    @property
    def refresh_interval_minutes(self) -> int:
        """Return the configured refresh interval."""
        return int(
            self.config_entry.options.get(
                CONF_REFRESH_INTERVAL_MINUTES, DEFAULT_REFRESH_INTERVAL_MINUTES
            )
        )

    @property
    def notify_service(self) -> str | None:
        """Return the configured notify service."""
        value = self.config_entry.options.get(CONF_NOTIFY_SERVICE)
        return str(value) if value else None

    async def _async_resume_if_needed(self) -> None:
        """Resume retry behavior after a Home Assistant restart."""
        today = self._local_now().date().isoformat()
        if self.runtime_state.last_successful_refresh_date == today:
            self.runtime_state.awaiting_fresh_data = False
            await self._async_save_state()
            return
        if not self._is_within_refresh_window():
            self.runtime_state.awaiting_fresh_data = False
            await self._async_save_state()
            return
        self.runtime_state.awaiting_fresh_data = True
        await self._async_save_state()
        self._schedule_window_end()
        await self._async_attempt_refresh("startup_resume")
        if self.runtime_state.awaiting_fresh_data:
            self._schedule_retry()

    @callback
    def _schedule_next_window_start(self) -> None:
        """Schedule the next daily refresh start."""
        self._cancel_window_start()
        now = self._local_now()
        start_dt = self._combine_local(now.date(), self.refresh_start_time)
        if now >= start_dt:
            start_dt += timedelta(days=1)
        self._unsub_window_start = async_track_point_in_time(
            self.hass,
            self._handle_window_start,
            start_dt,
        )

    @callback
    def _schedule_window_end(self) -> None:
        """Schedule the cutoff for today's retries."""
        self._cancel_window_end()
        end_dt = self._combine_local(self._local_now().date(), self.refresh_end_time)
        if self._local_now() >= end_dt:
            return
        self._unsub_window_end = async_track_point_in_time(
            self.hass,
            self._handle_window_end,
            end_dt,
        )

    @callback
    def _schedule_retry(self) -> None:
        """Schedule the next retry inside the active window."""
        self._cancel_retry()
        self._unsub_retry = async_call_later(
            self.hass,
            timedelta(minutes=self.refresh_interval_minutes).total_seconds(),
            self._handle_retry,
        )

    @callback
    def _cancel_window_start(self) -> None:
        if self._unsub_window_start:
            self._unsub_window_start()
            self._unsub_window_start = None

    @callback
    def _cancel_window_end(self) -> None:
        if self._unsub_window_end:
            self._unsub_window_end()
            self._unsub_window_end = None

    @callback
    def _cancel_retry(self) -> None:
        if self._unsub_retry:
            self._unsub_retry()
            self._unsub_retry = None

    @callback
    def _handle_window_start(self, now: datetime) -> None:
        """Start the daily refresh window."""
        self.hass.async_create_task(self._async_handle_window_start())

    async def _async_handle_window_start(self) -> None:
        today = self._local_now().date().isoformat()
        if self.runtime_state.last_successful_refresh_date != today:
            self.runtime_state.awaiting_fresh_data = True
            await self._async_save_state()
        self._schedule_window_end()
        await self._async_attempt_refresh("window_start")
        if self.runtime_state.awaiting_fresh_data:
            self._schedule_retry()
        self._schedule_next_window_start()

    @callback
    def _handle_window_end(self, now: datetime) -> None:
        """Stop retries at the configured cutoff."""
        self.hass.async_create_task(self._async_handle_window_end())

    async def _async_handle_window_end(self) -> None:
        self.runtime_state.awaiting_fresh_data = False
        self._cancel_retry()
        await self._async_save_state()

    @callback
    def _handle_retry(self, now: datetime) -> None:
        """Perform a scheduled retry."""
        self.hass.async_create_task(self._async_handle_retry())

    async def _async_handle_retry(self) -> None:
        self._unsub_retry = None
        await self._async_attempt_refresh("retry")
        if self.runtime_state.awaiting_fresh_data and self._is_within_refresh_window():
            self._schedule_retry()

    async def _async_attempt_refresh(self, trigger: str) -> None:
        """Fetch fresh data and stop retries once a new history date appears."""
        if not self._is_within_refresh_window():
            self.runtime_state.awaiting_fresh_data = False
            await self._async_save_state()
            return

        try:
            snapshot = await self.client.async_fetch_portfolio_snapshot()
        except IndexaAuthError as err:
            raise ConfigEntryAuthFailed from err
        except IndexaApiError as err:
            _LOGGER.warning("Indexa refresh attempt (%s) failed: %s", trigger, err)
            return

        if snapshot is None:
            _LOGGER.debug("Indexa refresh attempt (%s) returned no snapshot", trigger)
            return

        latest_date = snapshot.latest_history_date
        previous_fresh_date = self.runtime_state.last_fresh_date

        if not latest_date or latest_date.isoformat() <= (previous_fresh_date or ""):
            _LOGGER.debug("Indexa refresh (%s) did not return a fresher history date", trigger)
            return

        self.async_set_updated_data(snapshot)
        self.runtime_state.last_fresh_date = latest_date.isoformat()
        today = self._local_now().date().isoformat()
        self.runtime_state.last_successful_refresh_date = today
        self.runtime_state.awaiting_fresh_data = False
        self._cancel_retry()
        await self._async_save_state()
        await self._async_maybe_send_notification(latest_date)

    async def _async_maybe_send_notification(self, latest_date: date) -> None:
        """Send one success notification per day if configured."""
        today = self._local_now().date().isoformat()
        if not self.notify_service or self.runtime_state.last_notification_date == today:
            return

        if "." not in self.notify_service:
            _LOGGER.warning(
                "Invalid notify service configured for Indexa Capital: %s",
                self.notify_service,
            )
            return
        domain, service = self.notify_service.split(".", 1)
        try:
            await self.hass.services.async_call(
                domain,
                service,
                {
                    "title": "Indexa Capital",
                    "message": (
                        f"Daily portfolio refresh completed for {latest_date.isoformat()}."
                    ),
                },
                blocking=True,
            )
        except Exception as err:  # pragma: no cover - defensive HA service boundary
            _LOGGER.warning("Indexa notification delivery failed: %s", err)
            return
        self.runtime_state.last_notification_date = today
        await self._async_save_state()

    def _combine_local(self, day: date, time_value) -> datetime:
        """Combine a local date and time into an aware datetime."""
        time_zone = dt_util.get_time_zone(self.hass.config.time_zone)
        return datetime.combine(day, time_value, tzinfo=time_zone)

    def _coerce_time(self, value: time | str) -> time:
        """Normalize Home Assistant option values into `time`."""
        if isinstance(value, time):
            return value
        return time.fromisoformat(value)

    def _local_now(self) -> datetime:
        """Return local now."""
        return dt_util.as_local(dt_util.now())

    def _is_within_refresh_window(self) -> bool:
        """Return whether the current local time is inside the refresh window."""
        now = self._local_now()
        start_dt = self._combine_local(now.date(), self.refresh_start_time)
        end_dt = self._combine_local(now.date(), self.refresh_end_time)
        return start_dt <= now < end_dt
