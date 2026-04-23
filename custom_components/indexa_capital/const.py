"""Constants for the Indexa Capital integration."""

from __future__ import annotations

from datetime import time

DOMAIN = "indexa_capital"

CONF_NOTIFY_SERVICE = "notify_service"
CONF_REFRESH_START_TIME = "refresh_start_time"
CONF_REFRESH_END_TIME = "refresh_end_time"
CONF_REFRESH_INTERVAL_MINUTES = "refresh_interval_minutes"

DEFAULT_REFRESH_START_TIME = time(hour=8, minute=0)
DEFAULT_REFRESH_END_TIME = time(hour=11, minute=0)
DEFAULT_REFRESH_INTERVAL_MINUTES = 15

DATA_CLIENT = "client"
DATA_COORDINATOR = "coordinator"

STORAGE_VERSION = 1
STORAGE_KEY = f"{DOMAIN}_state"

ATTR_ACCOUNT_NUMBER = "account_number"
ATTR_LATEST_HISTORY_DATE = "latest_history_date"
ATTR_INVESTED_AMOUNT = "invested_amount"

AGGREGATE_DEVICE_ID = "portfolio_total"

