"""Data models for Indexa Capital."""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import date


@dataclass(slots=True)
class IndexaAccountSnapshot:
    """Normalized snapshot for a single account."""

    account_number: str
    display_name: str
    currency: str
    invested_amount: float
    performance_amount: float
    performance_percentage: float
    latest_history_date: date | None
    latest_history_value: float | None


@dataclass(slots=True)
class IndexaPortfolioSnapshot:
    """Combined snapshot for all accounts."""

    accounts: list[IndexaAccountSnapshot] = field(default_factory=list)

    @property
    def latest_history_date(self) -> date | None:
        """Return the most recent history date across all accounts."""
        dates = [
            account.latest_history_date
            for account in self.accounts
            if account.latest_history_date
        ]
        return max(dates) if dates else None

    @property
    def total_performance_amount(self) -> float:
        """Return total profit/loss in account currency."""
        return sum(account.performance_amount for account in self.accounts)

    @property
    def total_invested_amount(self) -> float:
        """Return total invested amount."""
        return sum(account.invested_amount for account in self.accounts)

    @property
    def total_contributions_amount(self) -> float:
        """Return total contributions amount."""
        return self.total_invested_amount

    @property
    def total_performance_percentage(self) -> float:
        """Return weighted total performance percentage."""
        total_invested = self.total_invested_amount
        if total_invested == 0:
            return 0.0
        return (self.total_performance_amount / total_invested) * 100

    @property
    def currency(self) -> str:
        """Return the most common account currency."""
        return self.accounts[0].currency if self.accounts else "EUR"


@dataclass(slots=True)
class IndexaRuntimeState:
    """Persisted scheduler state."""

    last_fresh_date: str | None = None
    last_notification_date: str | None = None
    last_successful_refresh_date: str | None = None
    awaiting_fresh_data: bool = False
