"""Indexa Capital API client."""

from __future__ import annotations

import hashlib
import logging
from dataclasses import asdict
from datetime import date
from typing import Any

from aiohttp import ClientResponseError, ClientSession

from .models import IndexaAccountSnapshot, IndexaPortfolioSnapshot

_LOGGER = logging.getLogger(__name__)

API_BASE = "https://api.indexacapital.com"


class IndexaApiError(Exception):
    """Base API error."""


class IndexaAuthError(IndexaApiError):
    """Authentication failed."""


def fingerprint_token(token: str) -> str:
    """Return a stable token fingerprint safe for unique IDs."""
    return hashlib.sha256(token.encode("utf-8")).hexdigest()


class IndexaApiClient:
    """Async client for the Indexa API."""

    def __init__(self, session: ClientSession, token: str) -> None:
        self._session = session
        self._token = token

    @property
    def token_fingerprint(self) -> str:
        """Return a stable token fingerprint."""
        return fingerprint_token(self._token)

    async def async_validate_token(self) -> dict[str, Any]:
        """Validate the configured token."""
        return await self._request_json("GET", "/users/me")

    async def async_fetch_portfolio_snapshot(self) -> IndexaPortfolioSnapshot:
        """Fetch and normalize all account performance data."""
        profile = await self.async_validate_token()
        raw_accounts = self._find_accounts_container(profile)
        account_ids = self._extract_account_numbers(profile)
        accounts: list[IndexaAccountSnapshot] = []

        for raw_account, account_number in zip(raw_accounts, account_ids, strict=False):
            detail = await self._safe_account_detail(account_number)
            performance = await self._request_json("GET", f"/accounts/{account_number}/performance")
            accounts.append(
                self._normalize_account(
                    account_number,
                    detail,
                    performance,
                    raw_account,
                )
            )

        return IndexaPortfolioSnapshot(accounts=accounts)

    async def _safe_account_detail(self, account_number: str) -> dict[str, Any]:
        """Fetch account details when available without failing normalization."""
        try:
            return await self._request_json("GET", f"/accounts/{account_number}")
        except IndexaApiError:
            _LOGGER.debug("Could not fetch account details for %s", account_number)
            return {}

    async def _request_json(self, method: str, path: str) -> dict[str, Any]:
        """Perform an authenticated JSON request."""
        headers = {
            "X-AUTH-TOKEN": self._token,
            "Accept": "application/json",
        }
        url = f"{API_BASE}{path}"
        try:
            async with self._session.request(method, url, headers=headers) as response:
                if response.status in (401, 403):
                    raise IndexaAuthError("Invalid Indexa API token")
                response.raise_for_status()
                payload = await response.json()
        except ClientResponseError as err:
            raise IndexaApiError(str(err)) from err
        except IndexaAuthError:
            raise
        except Exception as err:  # pragma: no cover - network/runtime safety
            raise IndexaApiError(str(err)) from err

        if isinstance(payload, dict):
            return payload
        if isinstance(payload, list):
            return {"items": payload}
        raise IndexaApiError(f"Unexpected payload type at {path}")

    def _extract_account_numbers(self, profile: dict[str, Any]) -> list[str]:
        """Extract account identifiers from the user profile."""
        raw_accounts = self._find_accounts_container(profile)
        account_numbers: list[str] = []

        for item in raw_accounts:
            account_number = (
                item.get("account_number")
                or item.get("accountNumber")
                or item.get("number")
                or item.get("id")
            )
            if account_number is not None:
                account_numbers.append(str(account_number))

        if not account_numbers:
            raise IndexaApiError("No accounts found for the provided token")
        return account_numbers

    def _find_accounts_container(self, payload: dict[str, Any]) -> list[dict[str, Any]]:
        """Locate the account list in a flexible API payload."""
        candidates = [
            payload.get("accounts"),
            payload.get("portfolios"),
            payload.get("items"),
            (
                payload.get("data", {}).get("accounts")
                if isinstance(payload.get("data"), dict)
                else None
            ),
            (
                payload.get("user", {}).get("accounts")
                if isinstance(payload.get("user"), dict)
                else None
            ),
        ]
        for candidate in candidates:
            if isinstance(candidate, list):
                return [item for item in candidate if isinstance(item, dict)]
        return []

    def _normalize_account(
        self,
        account_number: str,
        detail: dict[str, Any],
        performance: dict[str, Any],
        profile_account: dict[str, Any] | None = None,
    ) -> IndexaAccountSnapshot:
        """Normalize an account payload into Home Assistant friendly data."""
        perf_return = performance.get("return", {})
        profile_account = profile_account or {}
        history_items = self._extract_history(performance)
        latest_history = history_items[-1] if history_items else {}
        latest_history_date = self._parse_date(
            latest_history.get("date") or latest_history.get("day") or latest_history.get("label")
        )
        latest_history_value = self._coerce_float(
            latest_history.get("value") or latest_history.get("amount") or latest_history.get("pl")
        )
        invested_amount = self._coerce_float(
            performance.get("invested_amount")
            or performance.get("investedAmount")
            or perf_return.get("investment")
            or perf_return.get("invested_amount")
            or perf_return.get("investedAmount")
            or detail.get("invested_amount")
            or detail.get("investedAmount")
            or detail.get("current_value")
            or detail.get("currentValue")
        )
        performance_amount = self._coerce_float(
            perf_return.get("pl")
            or perf_return.get("profit_loss")
            or performance.get("pl")
            or performance.get("profit_loss")
        )
        time_return = self._coerce_float(
            perf_return.get("time_return")
            or perf_return.get("timeReturn")
            or performance.get("time_return")
            or performance.get("timeReturn")
        )
        display_name = (
            detail.get("name")
            or detail.get("display_name")
            or detail.get("alias")
            or profile_account.get("name")
            or profile_account.get("display_name")
            or profile_account.get("alias")
            or profile_account.get("title")
            or profile_account.get("label")
            or profile_account.get("portfolio_name")
            or profile_account.get("portfolioName")
            or profile_account.get("account_name")
            or profile_account.get("accountName")
            or profile_account.get("nickname")
            or self._default_account_name(account_number, detail, profile_account)
        )
        currency = detail.get("currency") or performance.get("currency") or "EUR"

        return IndexaAccountSnapshot(
            account_number=account_number,
            display_name=str(display_name),
            currency=str(currency),
            invested_amount=invested_amount,
            performance_amount=performance_amount,
            performance_percentage=time_return * 100,
            latest_history_date=latest_history_date,
            latest_history_value=latest_history_value,
        )

    def _default_account_name(
        self,
        account_number: str,
        detail: dict[str, Any],
        profile_account: dict[str, Any],
    ) -> str:
        """Build a readable fallback account name from known account metadata."""
        account_type = (
            profile_account.get("type")
            or detail.get("type")
            or detail.get("account_type")
            or profile_account.get("account_type")
        )
        if account_type == "pension":
            return f"Indexa Pension {account_number}"
        if account_type == "mutual":
            return f"Indexa Mutual {account_number}"
        return f"Indexa Account {account_number}"

    def _extract_history(self, payload: dict[str, Any]) -> list[dict[str, Any]]:
        """Extract historical performance points."""
        candidates = [
            payload.get("history"),
            payload.get("performance_history"),
            payload.get("performanceHistory"),
            payload.get("chart"),
            self._normalize_index_history(payload.get("return", {}).get("index")),
            (
                payload.get("data", {}).get("history")
                if isinstance(payload.get("data"), dict)
                else None
            ),
        ]
        for candidate in candidates:
            if isinstance(candidate, list):
                rows = [item for item in candidate if isinstance(item, dict)]
                rows.sort(
                    key=lambda item: str(
                        item.get("date") or item.get("day") or item.get("label") or ""
                    )
                )
                return rows
        return []

    def _normalize_index_history(self, payload: Any) -> list[dict[str, Any]] | None:
        """Normalize dict-based history maps like the documented `return.index` payload."""
        if not isinstance(payload, dict):
            return None

        rows: list[dict[str, Any]] = []
        for raw_date, value in payload.items():
            normalized_date = self._normalize_compact_date(raw_date)
            if normalized_date is None:
                continue
            rows.append(
                {
                    "date": normalized_date,
                    "value": value,
                }
            )

        return rows or None

    def _normalize_compact_date(self, raw_value: Any) -> str | None:
        """Convert compact dates like `20161211` to ISO format."""
        if raw_value is None:
            return None

        text = str(raw_value)
        if len(text) == 8 and text.isdigit():
            return f"{text[0:4]}-{text[4:6]}-{text[6:8]}"
        return text

    def _parse_date(self, raw_value: Any) -> date | None:
        """Parse an ISO-like date string."""
        if not raw_value:
            return None
        try:
            return date.fromisoformat(str(raw_value)[:10])
        except ValueError:
            _LOGGER.debug("Unable to parse history date: %s", raw_value)
            return None

    def _coerce_float(self, value: Any) -> float:
        """Convert API values to float safely."""
        if value in (None, ""):
            return 0.0
        try:
            return float(value)
        except (TypeError, ValueError):
            _LOGGER.debug("Unable to coerce float from %s", value)
            return 0.0


def snapshot_to_dict(snapshot: IndexaPortfolioSnapshot | None) -> dict[str, Any] | None:
    """Convert a snapshot into a serializable dictionary for diagnostics."""
    if snapshot is None:
        return None
    return {
        "accounts": [
            {
                **asdict(account),
                "latest_history_date": (
                    account.latest_history_date.isoformat()
                    if account.latest_history_date
                    else None
                ),
            }
            for account in snapshot.accounts
        ]
    }


def dict_to_snapshot(payload: dict[str, Any] | None) -> IndexaPortfolioSnapshot | None:
    """Convert stored snapshot data back into portfolio models."""
    if payload is None:
        return None

    accounts: list[IndexaAccountSnapshot] = []
    for raw_account in payload.get("accounts", []):
        latest_history_date = raw_account.get("latest_history_date")
        accounts.append(
            IndexaAccountSnapshot(
                account_number=str(raw_account["account_number"]),
                display_name=str(raw_account["display_name"]),
                currency=str(raw_account["currency"]),
                invested_amount=float(raw_account["invested_amount"]),
                performance_amount=float(raw_account["performance_amount"]),
                performance_percentage=float(raw_account["performance_percentage"]),
                latest_history_date=(
                    date.fromisoformat(latest_history_date)
                    if isinstance(latest_history_date, str)
                    else latest_history_date
                ),
                latest_history_value=(
                    float(raw_account["latest_history_value"])
                    if raw_account.get("latest_history_value") is not None
                    else None
                ),
            )
        )

    return IndexaPortfolioSnapshot(accounts=accounts)
