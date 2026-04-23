"""Data models for Indexa Capital."""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import date
from itertools import pairwise
from math import isfinite


@dataclass(slots=True)
class IndexaAccountSnapshot:
    """Normalized snapshot for a single account."""

    account_number: str
    display_name: str
    currency: str
    invested_amount: float
    performance_amount: float
    time_weighted_performance_percentage: float
    money_weighted_performance_percentage: float
    latest_history_date: date | None
    latest_history_value: float | None
    time_return_index: dict[str, float] = field(default_factory=dict)
    portfolio_value_history: dict[str, float] = field(default_factory=dict)
    external_cash_flow_history: dict[str, float] = field(default_factory=dict)

    @property
    def performance_percentage(self) -> float:
        """Backward-compatible alias for the time-weighted performance percentage."""
        return self.time_weighted_performance_percentage


@dataclass(slots=True)
class IndexaPortfolioSnapshot:
    """Combined snapshot for all accounts."""

    accounts: list[IndexaAccountSnapshot] = field(default_factory=list)
    computed_total_time_weighted_performance_percentage: float | None = None
    computed_total_money_weighted_performance_percentage: float | None = None

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
        """Return total time-weighted performance percentage."""
        if self.computed_total_time_weighted_performance_percentage is not None:
            return self.computed_total_time_weighted_performance_percentage
        return self._weighted_average_current_value(
            "time_weighted_performance_percentage"
        )

    @property
    def total_money_weighted_performance_percentage(self) -> float:
        """Return total money-weighted performance percentage."""
        if self.computed_total_money_weighted_performance_percentage is not None:
            return self.computed_total_money_weighted_performance_percentage
        return self._compute_portfolio_money_weighted_return()

    @property
    def currency(self) -> str:
        """Return the most common account currency."""
        return self.accounts[0].currency if self.accounts else "EUR"

    def __post_init__(self) -> None:
        """Compute aggregate portfolio metrics from daily account histories."""
        if self.computed_total_time_weighted_performance_percentage is None:
            self.computed_total_time_weighted_performance_percentage = (
                self._compose_weighted_daily_returns(weight_on_current_day=False)
            )
        if self.computed_total_money_weighted_performance_percentage is None:
            self.computed_total_money_weighted_performance_percentage = (
                self._compute_portfolio_money_weighted_return()
            )

    def _compose_weighted_daily_returns(self, *, weight_on_current_day: bool) -> float:
        """Compose daily aggregate returns using account-level return indices and values."""
        all_dates = sorted(
            {
                *(
                    date_key
                    for account in self.accounts
                    for date_key in account.time_return_index.keys()
                ),
                *(
                    date_key
                    for account in self.accounts
                    for date_key in account.portfolio_value_history.keys()
                ),
            }
        )
        if len(all_dates) < 2:
            field_name = (
                "money_weighted_performance_percentage"
                if weight_on_current_day
                else "time_weighted_performance_percentage"
            )
            return self._weighted_average_current_value(field_name)

        compounded_return = 1.0
        has_weighted_period = False

        for previous_date, current_date in pairwise(all_dates):
            weighted_return = 0.0
            total_weight = 0.0

            for account in self.accounts:
                previous_index = account.time_return_index.get(previous_date)
                current_index = account.time_return_index.get(current_date)
                if previous_index in (None, 0) or current_index is None:
                    continue

                weight_date = current_date if weight_on_current_day else previous_date
                weight = account.portfolio_value_history.get(weight_date)
                if weight in (None, 0):
                    continue

                account_daily_return = (current_index / previous_index) - 1
                weighted_return += weight * account_daily_return
                total_weight += weight

            if total_weight == 0:
                continue

            has_weighted_period = True
            compounded_return *= 1 + (weighted_return / total_weight)

        if has_weighted_period:
            return (compounded_return - 1) * 100

        field_name = (
            "money_weighted_performance_percentage"
            if weight_on_current_day
            else "time_weighted_performance_percentage"
        )
        return self._weighted_average_current_value(field_name)

    def _weighted_average_current_value(self, field_name: str) -> float:
        """Return a current-value-weighted fallback average for the requested field."""
        weighted_value = 0.0
        total_value = 0.0

        for account in self.accounts:
            current_value = (
                account.portfolio_value_history[max(account.portfolio_value_history)]
                if account.portfolio_value_history
                else account.invested_amount + account.performance_amount
            )
            if current_value == 0:
                continue
            weighted_value += current_value * getattr(account, field_name)
            total_value += current_value

        if total_value == 0:
            return 0.0
        return weighted_value / total_value

    def _compute_portfolio_money_weighted_return(self) -> float:
        """Compute the cumulative portfolio money-weighted return from cash flows."""
        cash_flows = self._aggregate_cash_flows()
        terminal_date, terminal_value = self._latest_terminal_value()

        if terminal_date is None or terminal_value == 0:
            return self._weighted_average_current_value(
                "money_weighted_performance_percentage"
            )

        flow_items = sorted(cash_flows.items())
        flow_items.append((terminal_date, terminal_value))

        cumulative_return = self._solve_cumulative_money_return(flow_items)
        if cumulative_return is None:
            return self._weighted_average_current_value(
                "money_weighted_performance_percentage"
            )
        return cumulative_return * 100

    def _aggregate_cash_flows(self) -> dict[date, float]:
        """Aggregate external account cash flows into a single portfolio series."""
        aggregated: dict[date, float] = {}

        for account in self.accounts:
            for raw_date, amount in account.external_cash_flow_history.items():
                flow_date = date.fromisoformat(raw_date)
                aggregated[flow_date] = aggregated.get(flow_date, 0.0) + amount

        return aggregated

    def _latest_terminal_value(self) -> tuple[date | None, float]:
        """Return the latest available terminal portfolio date and aggregated value."""
        dated_values: dict[date, float] = {}
        latest_account_values: list[tuple[date, float]] = []

        for account in self.accounts:
            if account.portfolio_value_history:
                latest_date_str = max(account.portfolio_value_history)
                account_date = date.fromisoformat(latest_date_str)
                account_value = account.portfolio_value_history[latest_date_str]
                latest_account_values.append((account_date, account_value))
                dated_values[account_date] = dated_values.get(account_date, 0.0) + account_value
                continue

            if account.latest_history_date is None:
                continue
            latest_account_values.append(
                (account.latest_history_date, account.invested_amount + account.performance_amount)
            )
            dated_values[account.latest_history_date] = (
                dated_values.get(account.latest_history_date, 0.0)
                + account.invested_amount
                + account.performance_amount
            )

        if not dated_values:
            return None, 0.0

        latest_date = max(dated_values)
        latest_value = sum(value for _, value in latest_account_values)
        return latest_date, latest_value

    def _solve_cumulative_money_return(self, cash_flows: list[tuple[date, float]]) -> float | None:
        """Solve cumulative money-weighted return for dated cash flows using bisection."""
        if not cash_flows:
            return None
        if not any(amount < 0 for _, amount in cash_flows):
            return None
        if not any(amount > 0 for _, amount in cash_flows):
            return None

        start_date = cash_flows[0][0]
        end_date = cash_flows[-1][0]
        total_days = max((end_date - start_date).days, 0)
        if total_days == 0:
            invested = sum(-amount for _, amount in cash_flows[:-1] if amount < 0)
            if invested == 0:
                return None
            return (cash_flows[-1][1] / invested) - 1

        def npv(cumulative_return: float) -> float:
            total = 0.0
            for flow_date, amount in cash_flows:
                fraction = (flow_date - start_date).days / total_days
                total += amount / ((1 + cumulative_return) ** fraction)
            return total

        lower = -0.9999
        upper = 10.0
        npv_lower = npv(lower)
        npv_upper = npv(upper)

        while npv_lower * npv_upper > 0 and upper < 1_000_000:
            upper *= 2
            npv_upper = npv(upper)

        if not isfinite(npv_lower) or not isfinite(npv_upper):
            return None
        if npv_lower * npv_upper > 0:
            return None

        for _ in range(100):
            midpoint = (lower + upper) / 2
            npv_midpoint = npv(midpoint)
            if abs(npv_midpoint) < 1e-10:
                return midpoint
            if npv_lower * npv_midpoint <= 0:
                upper = midpoint
                npv_upper = npv_midpoint
            else:
                lower = midpoint
                npv_lower = npv_midpoint

        return (lower + upper) / 2


@dataclass(slots=True)
class IndexaRuntimeState:
    """Persisted scheduler state."""

    last_fresh_date: str | None = None
    last_notification_date: str | None = None
    last_successful_refresh_date: str | None = None
    awaiting_fresh_data: bool = False
