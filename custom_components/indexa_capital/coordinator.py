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
        _LOGGER.info("Indexa startup refresh starting")
        try:
            snapshot = await self.client.async_fetch_portfolio_snapshot()
        except IndexaAuthError as err:
            raise ConfigEntryAuthFailed from err
        except IndexaApiError as err:
            self._record_refresh_check(
                trigger="startup",
                latest_history_date=None,
                outcome="api_error",
                error=str(err),
            )
            if self.data is None:
                raise ConfigEntryNotReady(f"Initial Indexa snapshot fetch failed: {err}") from err
            _LOGGER.warning(
                "Initial Indexa snapshot fetch failed, using stored snapshot instead: %s",
                err,
            )
        else:
            self.data = snapshot
            accepted_fresher_snapshot = await self._async_accept_fresher_snapshot(
                snapshot,
                trigger="startup",
                notify=False,
                publish_update=False,
            )
            if accepted_fresher_snapshot and self._is_within_refresh_window():
                await self._async_maybe_send_notification(snapshot.latest_history_date)
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

    async def async_record_runtime_state_change(self) -> None:
        """Persist runtime-only changes and refresh listeners."""
        await self._async_save_state()
        self.async_update_listeners()

    @property
    def refresh_start_time(self):
        """Return the effective refresh start time."""
        if self._has_valid_configured_refresh_window():
            return self._configured_refresh_start_time
        return DEFAULT_REFRESH_START_TIME

    @property
    def refresh_end_time(self):
        """Return the effective refresh end time."""
        if self._has_valid_configured_refresh_window():
            return self._configured_refresh_end_time
        return DEFAULT_REFRESH_END_TIME

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

    @property
    def notification_configured(self) -> bool:
        """Return whether notification delivery is configured."""
        return bool(self.notify_service)

    async def _async_resume_if_needed(self) -> None:
        """Resume retry behavior after a Home Assistant restart."""
        today = self._local_now().date().isoformat()
        if self.runtime_state.last_successful_refresh_date == today:
            self.runtime_state.awaiting_fresh_data = False
            self._record_refresh_check(
                trigger="startup_resume",
                latest_history_date=self.runtime_state.last_fresh_date,
                outcome="already_succeeded_today",
            )
            await self.async_record_runtime_state_change()
            _LOGGER.info(
                "Indexa startup resume skipped: refresh already succeeded today",
                extra={
                    "trigger": "startup_resume",
                    "today": today,
                    "latest_history_date": self.runtime_state.last_fresh_date,
                    "last_successful_refresh_date": self.runtime_state.last_successful_refresh_date,
                },
            )
            return
        self._log_invalid_refresh_window_fallback_if_needed("startup_resume")
        if not self._is_within_refresh_window():
            self.runtime_state.awaiting_fresh_data = False
            self._record_refresh_check(
                trigger="startup_resume",
                latest_history_date=self.runtime_state.last_fresh_date,
                outcome="outside_window",
            )
            await self.async_record_runtime_state_change()
            _LOGGER.info(
                "Indexa startup resume skipped: outside refresh window",
                extra={
                    "trigger": "startup_resume",
                    "today": today,
                    "latest_history_date": self.runtime_state.last_fresh_date,
                    "refresh_start_time": self.refresh_start_time.isoformat(),
                    "refresh_end_time": self.refresh_end_time.isoformat(),
                },
            )
            return
        self.runtime_state.awaiting_fresh_data = True
        await self.async_record_runtime_state_change()
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
        _LOGGER.info(
            "Indexa refresh window started",
            extra={
                "trigger": "window_start",
                "today": today,
                "latest_history_date": self.runtime_state.last_fresh_date,
                "last_successful_refresh_date": self.runtime_state.last_successful_refresh_date,
                "refresh_start_time": self.refresh_start_time.isoformat(),
                "refresh_end_time": self.refresh_end_time.isoformat(),
                "refresh_interval_minutes": self.refresh_interval_minutes,
            },
        )
        self._log_invalid_refresh_window_fallback_if_needed("window_start")
        if self.runtime_state.last_successful_refresh_date != today:
            self.runtime_state.awaiting_fresh_data = True
            await self.async_record_runtime_state_change()
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
        self._record_refresh_check(
            trigger="window_end",
            latest_history_date=self.runtime_state.last_fresh_date,
            outcome="window_ended_without_fresher_day",
        )
        self._cancel_retry()
        await self.async_record_runtime_state_change()
        _LOGGER.info(
            "Indexa refresh window ended without fresher day",
            extra={
                "trigger": "window_end",
                "latest_history_date": self.runtime_state.last_fresh_date,
                "last_successful_refresh_date": self.runtime_state.last_successful_refresh_date,
            },
        )

    @callback
    def _handle_retry(self, now: datetime) -> None:
        """Perform a scheduled retry."""
        self.hass.async_create_task(self._async_handle_retry())

    async def _async_handle_retry(self) -> None:
        self._unsub_retry = None
        _LOGGER.info(
            "Indexa scheduled retry starting",
            extra={
                "trigger": "retry",
                "latest_history_date": self.runtime_state.last_fresh_date,
                "refresh_interval_minutes": self.refresh_interval_minutes,
                "awaiting_fresh_data": self.runtime_state.awaiting_fresh_data,
            },
        )
        await self._async_attempt_refresh("retry")
        if self.runtime_state.awaiting_fresh_data and self._is_within_refresh_window():
            self._schedule_retry()

    async def _async_attempt_refresh(self, trigger: str) -> None:
        """Fetch fresh data and stop retries once a new history date appears."""
        if not self._is_within_refresh_window():
            self.runtime_state.awaiting_fresh_data = False
            self._record_refresh_check(
                trigger=trigger,
                latest_history_date=self.runtime_state.last_fresh_date,
                outcome="outside_window",
            )
            await self.async_record_runtime_state_change()
            _LOGGER.info(
                "Indexa refresh skipped outside window",
                extra={
                    "trigger": trigger,
                    "latest_history_date": self.runtime_state.last_fresh_date,
                    "refresh_start_time": self.refresh_start_time.isoformat(),
                    "refresh_end_time": self.refresh_end_time.isoformat(),
                },
            )
            return

        _LOGGER.info(
            "Indexa refresh attempt starting",
            extra={
                "trigger": trigger,
                "previous_last_fresh_date": self.runtime_state.last_fresh_date,
                "awaiting_fresh_data": self.runtime_state.awaiting_fresh_data,
                "notify_service_configured": self.notification_configured,
            },
        )
        try:
            snapshot = await self.client.async_fetch_portfolio_snapshot()
        except IndexaAuthError as err:
            raise ConfigEntryAuthFailed from err
        except IndexaApiError as err:
            self._record_refresh_check(
                trigger=trigger,
                latest_history_date=None,
                outcome="api_error",
                error=str(err),
            )
            _LOGGER.warning(
                "Indexa refresh attempt failed",
                extra={
                    "trigger": trigger,
                    "previous_last_fresh_date": self.runtime_state.last_fresh_date,
                    "error": str(err),
                },
            )
            await self.async_record_runtime_state_change()
            return

        if snapshot is None:
            self._record_refresh_check(
                trigger=trigger,
                latest_history_date=None,
                outcome="no_snapshot",
            )
            _LOGGER.info(
                "Indexa refresh attempt returned no snapshot",
                extra={
                    "trigger": trigger,
                    "previous_last_fresh_date": self.runtime_state.last_fresh_date,
                },
            )
            await self.async_record_runtime_state_change()
            return

        if not await self._async_accept_fresher_snapshot(
            snapshot,
            trigger=trigger,
            notify=True,
            publish_update=True,
        ):
            _LOGGER.info(
                "Indexa refresh did not return a fresher history date",
                extra={
                    "trigger": trigger,
                    "latest_history_date": snapshot.latest_history_date.isoformat()
                    if snapshot.latest_history_date
                    else None,
                    "previous_last_fresh_date": self.runtime_state.last_fresh_date,
                },
            )
            return

    async def _async_maybe_send_notification(self, latest_date: date) -> None:
        """Send one success notification per day if configured."""
        today = self._local_now().date().isoformat()
        if not self.notify_service or self.runtime_state.last_notification_date == today:
            _LOGGER.info(
                "Indexa notification skipped",
                extra={
                    "latest_history_date": latest_date.isoformat(),
                    "notify_service_configured": bool(self.notify_service),
                    "last_notification_date": self.runtime_state.last_notification_date,
                    "today": today,
                },
            )
            return

        _LOGGER.info(
            "Indexa notification attempt starting",
            extra={
                "latest_history_date": latest_date.isoformat(),
                "last_notification_date": self.runtime_state.last_notification_date,
                "today": today,
            },
        )
        try:
            await self.async_send_notification(
                title="Indexa Capital",
                message=f"Daily portfolio refresh completed for {latest_date.isoformat()}.",
            )
        except ValueError:
            _LOGGER.warning(
                "Invalid notify service configured for Indexa Capital",
                extra={
                    "latest_history_date": latest_date.isoformat(),
                    "notify_service": self.notify_service,
                },
            )
            return
        except Exception as err:  # pragma: no cover - defensive HA service boundary
            _LOGGER.warning(
                "Indexa notification delivery failed",
                extra={
                    "latest_history_date": latest_date.isoformat(),
                    "error": str(err),
                },
            )
            return
        self.runtime_state.last_notification_date = today
        await self.async_record_runtime_state_change()
        _LOGGER.info("Indexa notification delivered", extra={"latest_history_date": latest_date.isoformat()})

    async def async_send_notification(self, *, title: str, message: str) -> None:
        """Send a notification through the configured notify service."""
        attempted_at = self._local_now().isoformat()
        self.runtime_state.last_notification_attempt_at = attempted_at

        if not self.notify_service or "." not in self.notify_service:
            self.runtime_state.last_notification_success_at = None
            self.runtime_state.last_notification_error = "Notify service is not configured correctly."
            await self.async_record_runtime_state_change()
            raise ValueError("Notify service is not configured correctly.")

        domain, service = self.notify_service.split(".", 1)
        try:
            await self.hass.services.async_call(
                domain,
                service,
                {
                    "title": title,
                    "message": message,
                },
                blocking=True,
            )
        except Exception as err:
            self.runtime_state.last_notification_success_at = None
            self.runtime_state.last_notification_error = str(err)
            await self.async_record_runtime_state_change()
            raise

        self.runtime_state.last_notification_success_at = attempted_at
        self.runtime_state.last_notification_error = None
        await self.async_record_runtime_state_change()

    async def _async_accept_fresher_snapshot(
        self,
        snapshot: IndexaPortfolioSnapshot,
        *,
        trigger: str,
        notify: bool,
        publish_update: bool,
    ) -> bool:
        """Accept a snapshot only when it carries a fresher Indexa history date."""
        latest_date = snapshot.latest_history_date
        previous_fresh_date = self.runtime_state.last_fresh_date
        if not latest_date or latest_date.isoformat() <= (previous_fresh_date or ""):
            self._record_refresh_check(
                trigger=trigger,
                latest_history_date=latest_date.isoformat() if latest_date else None,
                outcome="stale_snapshot",
            )
            await self.async_record_runtime_state_change()
            _LOGGER.info(
                "Indexa snapshot rejected as not fresher",
                extra={
                    "trigger": trigger,
                    "latest_history_date": latest_date.isoformat() if latest_date else None,
                    "previous_last_fresh_date": previous_fresh_date,
                },
            )
            return False

        if publish_update:
            self.async_set_updated_data(snapshot)
        else:
            self.data = snapshot

        self.runtime_state.last_fresh_date = latest_date.isoformat()
        self.runtime_state.last_successful_refresh_date = self._local_now().date().isoformat()
        self.runtime_state.awaiting_fresh_data = False
        self._record_refresh_check(
            trigger=trigger,
            latest_history_date=latest_date.isoformat(),
            outcome="accepted_fresher_snapshot",
        )
        self._cancel_retry()
        await self._async_save_state()
        _LOGGER.info(
            "Indexa accepted fresher snapshot",
            extra={
                "trigger": trigger,
                "latest_history_date": latest_date.isoformat(),
                "previous_last_fresh_date": previous_fresh_date,
                "last_successful_refresh_date": self.runtime_state.last_successful_refresh_date,
                "notify": notify,
            },
        )

        if notify:
            await self._async_maybe_send_notification(latest_date)

        return True

    def _record_refresh_check(
        self,
        *,
        trigger: str,
        latest_history_date: str | None,
        outcome: str,
        error: str | None = None,
    ) -> None:
        """Persist the latest refresh evaluation outcome for troubleshooting."""
        self.runtime_state.last_refresh_check_at = self._local_now().isoformat()
        self.runtime_state.last_refresh_check_trigger = trigger
        self.runtime_state.last_refresh_check_latest_history_date = latest_history_date
        self.runtime_state.last_refresh_check_outcome = outcome
        self.runtime_state.last_refresh_check_error = error

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

    @property
    def _configured_refresh_start_time(self) -> time:
        """Return the raw configured refresh start time."""
        return self._coerce_time(
            self.config_entry.options.get(CONF_REFRESH_START_TIME, DEFAULT_REFRESH_START_TIME)
        )

    @property
    def _configured_refresh_end_time(self) -> time:
        """Return the raw configured refresh end time."""
        return self._coerce_time(
            self.config_entry.options.get(CONF_REFRESH_END_TIME, DEFAULT_REFRESH_END_TIME)
        )

    def _has_valid_configured_refresh_window(self) -> bool:
        """Return whether the persisted refresh window is internally consistent."""
        return self._configured_refresh_start_time < self._configured_refresh_end_time

    def _log_invalid_refresh_window_fallback_if_needed(self, trigger: str) -> None:
        """Warn when falling back from an invalid persisted refresh window."""
        if self._has_valid_configured_refresh_window():
            return
        _LOGGER.warning(
            "Indexa invalid refresh window configured; using defaults instead",
            extra={
                "trigger": trigger,
                "configured_refresh_start_time": self._configured_refresh_start_time.isoformat(),
                "configured_refresh_end_time": self._configured_refresh_end_time.isoformat(),
                "effective_refresh_start_time": self.refresh_start_time.isoformat(),
                "effective_refresh_end_time": self.refresh_end_time.isoformat(),
            },
        )

    def _is_within_refresh_window(self) -> bool:
        """Return whether the current local time is inside the refresh window."""
        now = self._local_now()
        start_dt = self._combine_local(now.date(), self.refresh_start_time)
        end_dt = self._combine_local(now.date(), self.refresh_end_time)
        return start_dt <= now < end_dt
