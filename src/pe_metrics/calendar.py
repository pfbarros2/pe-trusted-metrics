"""Month-end windows from definitions/fund_definitions.yml."""

from __future__ import annotations

from datetime import date, timedelta


def month_end(year: int, month: int) -> date:
    if month == 12:
        return date(year, 12, 31)
    return date(year, month + 1, 1) - timedelta(days=1)


def shift_month_end(day: date, delta_months: int) -> date:
    index = day.year * 12 + (day.month - 1) + delta_months
    year, month_index = divmod(index, 12)
    return month_end(year, month_index + 1)


def portfolio_month_ends(rates: list[dict]) -> list[date]:
    return [row["period_end"] for row in rates]


def resolve_window(window: dict, months: list[date], ltm_months: int) -> list[date]:
    kind = window["kind"]
    if kind == "fy":
        year = int(window["year"])
        chosen = [day for day in months if day.year == year]
    elif kind == "h1":
        year = int(window["year"])
        chosen = [day for day in months if day.year == year and day.month <= 6]
    elif kind == "q4":
        year = int(window["year"])
        chosen = [day for day in months if day.year == year and day.month >= 10]
    elif kind == "ltm":
        end = _as_date(window["end"])
        start = shift_month_end(end, -(ltm_months - 1))
        chosen = [day for day in months if start <= day <= end]
        if len(chosen) != ltm_months:
            raise ValueError(
                f"LTM ending {end.isoformat()} has {len(chosen)} months in the ledger, "
                f"expected {ltm_months}."
            )
    else:
        raise ValueError(f"unknown window kind {kind}")
    if not chosen:
        raise ValueError(f"window {window} matches no months")
    return chosen


def _as_date(value: object) -> date:
    if isinstance(value, date):
        return value
    return date.fromisoformat(str(value))
