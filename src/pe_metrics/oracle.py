"""Python ground truth for the fund's agreed definitions.

This module does not execute the stand-in SQL. Tests check that the grounded
SQL returns the same amounts.
"""

from __future__ import annotations

from datetime import date
from decimal import Decimal

import duckdb

from pe_metrics.calendar import portfolio_month_ends, resolve_window
from pe_metrics.db import as_date, as_decimal, fetch_dicts
from pe_metrics.money import (
    choose_entity,
    pack_growth,
    pack_money,
    pack_pp,
    pack_ratio,
    q2,
    quant_ratio,
)


class Ledger:
    def __init__(self, connection: duckdb.DuckDBPyConnection, definitions: dict):
        self.definitions = definitions
        self.months = portfolio_month_ends(definitions["reference"]["rates"])
        self.fx = {
            row["period_end"]: as_decimal(row["gbp_per_usd"])
            for row in definitions["reference"]["rates"]
        }
        self.north = {
            as_date(row["period_end"]): _decimals(row)
            for row in fetch_dicts(
                connection, "SELECT * FROM north.ledger.monthly_pack ORDER BY 1"
            )
        }
        self.west = {
            as_date(row["MonthEnding"]): _decimals(row)
            for row in fetch_dicts(
                connection, "SELECT * FROM west.ops.MonthlyPnl ORDER BY 1"
            )
        }
        self.policy = {
            (row["entity_code"], as_date(row["period_end"])): _decimals(row)
            for row in fetch_dicts(connection, "SELECT * FROM holdco.ref.policy_adjustment")
        }

    def total(self, metric: str, scope: str, window: dict, currency: str) -> Decimal:
        days = resolve_window(
            window, self.months, int(self.definitions["calendar"]["ltm_months"])
        )
        amount = Decimal("0.00")
        for entity in _entities(scope, self.definitions):
            for day in days:
                amount += self.in_currency(metric, entity, day, currency)
        return amount

    def monthly(self, metric: str, scope: str, window: dict, currency: str) -> list[tuple[date, Decimal]]:
        days = resolve_window(
            window, self.months, int(self.definitions["calendar"]["ltm_months"])
        )
        rows = []
        for day in days:
            amount = Decimal("0.00")
            for entity in _entities(scope, self.definitions):
                amount += self.in_currency(metric, entity, day, currency)
            rows.append((day, amount))
        return rows

    def margin_parts(
        self, scope: str, window: dict, currency: str
    ) -> tuple[Decimal, Decimal, Decimal]:
        numerator = self.total("adjusted_ebitda", scope, window, currency)
        denominator = self.total("net_revenue", scope, window, currency)
        return quant_ratio(numerator, denominator), numerator, denominator

    def in_currency(self, metric: str, entity: str, day: date, currency: str) -> Decimal:
        self._require_month(entity, day)
        amount = self.functional(metric, entity, day)
        functional = self.definitions["entities"][entity]["functional_currency"]
        if currency == functional:
            return amount
        if functional == "USD" and currency == "GBP":
            return q2(amount * self.fx[day])
        raise ValueError(f"cannot translate {functional} to {currency}")

    def functional(self, metric: str, entity: str, day: date) -> Decimal:
        if metric == "net_revenue":
            if entity == "madeup_north":
                row = self.north[day]
                return (
                    row["third_party_billed_gross"]
                    - row["rebate_accrual"]
                    - row["deferred_unearned"]
                    + row["prior_period_release"]
                )
            return self.west[day]["RecognizedNetSales"]
        if metric == "adjusted_ebitda":
            policy = self.policy[(entity, day)]
            if entity == "madeup_north":
                return self.functional("net_revenue", entity, day) - self.north[day][
                    "cash_opex_excl_rent"
                ] + policy["ic_approved_addback_functional"]
            row = self.west[day]
            return (
                row["RecognizedNetSales"]
                - (
                    row["CashOperatingCost"]
                    - row["IntercompanyPurchasesUsd"]
                    - row["OneOffCharges"]
                )
                + policy["ic_approved_addback_functional"]
            )
        if metric == "ifrs16_lease_addback":
            if entity == "madeup_north":
                return self.north[day]["property_rent"]
            return Decimal("0.00")
        if metric == "declined_management_addback":
            return self.policy[(entity, day)]["rejected_addback_functional"]
        if metric == "intercompany_revenue_eliminated":
            if entity == "madeup_north":
                return self.north[day]["intercompany_billed_gross"]
            return Decimal("0.00")
        raise KeyError(metric)

    def _require_month(self, entity: str, day: date) -> None:
        table = self.north if entity == "madeup_north" else self.west
        if day not in table:
            raise KeyError((entity, day))


def answer_question(question: dict, ledger: Ledger, definitions: dict) -> dict:
    names = {
        code: spec["legal_name"] for code, spec in definitions["entities"].items()
    }
    base = {
        "question_id": question["id"],
        "question": question["text"],
        "sql": None,
        "provenance": {
            "method": "python_decimal_from_component_columns",
            "language_model": False,
            "definition_source": "definitions/fund_definitions.yml",
        },
    }
    if not question["answerable"]:
        return {
            **base,
            "status": "unanswerable",
            "value": None,
            "display": "unanswerable",
            "unit": None,
            "value_kind": "unanswerable",
            "provenance": {
                **base["provenance"],
                "missing_concept": question["missing_concept"],
                "reason": "The agreed definitions do not cover this concept.",
            },
        }
    task = question["task"]
    kind = task["kind"]
    if kind == "aggregate":
        amount = ledger.total(task["metric"], task["scope"], task["window"], task["currency"])
        value, display = pack_money(amount, task["currency"])
        return _answered(base, value, display, task["currency"], "money", task["metric"])
    if kind == "margin":
        ratio, numerator, denominator = ledger.margin_parts(
            task["scope"], task["window"], task["currency"]
        )
        value, display, _ = pack_ratio(numerator, denominator)
        return _answered(
            base,
            value,
            display,
            "ratio",
            "ratio",
            "ebitda_margin",
            extra={"numerator": f"{numerator:.2f}", "denominator": f"{denominator:.2f}"},
        )
    if kind == "compare_margin":
        scored = []
        detail = {}
        for code in definitions["entities"]:
            ratio, numerator, denominator = ledger.margin_parts(
                code, task["window"], task["currency"]
            )
            scored.append((code, ratio))
            detail[code] = {
                "ratio": f"{ratio:.6f}",
                "numerator": f"{numerator:.2f}",
                "denominator": f"{denominator:.2f}",
            }
        winner = choose_entity(scored, names)
        return _answered(
            base, winner, winner, "entity", "entity", "ebitda_margin", extra={"companies": detail}
        )
    if kind == "compare_margin_growth":
        scored = []
        detail = {}
        for code in definitions["entities"]:
            earlier, _, _ = ledger.margin_parts(
                code, task["window_earlier"], task["currency"]
            )
            later, _, _ = ledger.margin_parts(code, task["window_later"], task["currency"])
            change = later - earlier
            scored.append((code, change))
            detail[code] = {"earlier": f"{earlier:.6f}", "later": f"{later:.6f}", "change": f"{change:.6f}"}
        winner = choose_entity(scored, names)
        return _answered(
            base, winner, winner, "entity", "entity", "ebitda_margin", extra={"companies": detail}
        )
    if kind == "argmax_month":
        rows = ledger.monthly(
            task["metric"], task["scope"], task["window"], task["currency"]
        )
        winner, amount = sorted(rows, key=lambda item: (-item[1], item[0]))[0]
        return _answered(
            base,
            winner.isoformat(),
            winner.isoformat(),
            "month",
            "month",
            task["metric"],
            extra={"amount": pack_money(amount, task["currency"])[1]},
        )
    if kind == "growth_ratio":
        earlier = ledger.total(
            task["metric"], task["scope"], task["window_earlier"], task["currency"]
        )
        later = ledger.total(
            task["metric"], task["scope"], task["window_later"], task["currency"]
        )
        value, display, _ = pack_growth(earlier, later)
        return _answered(
            base,
            value,
            display,
            "ratio",
            "ratio",
            task["metric"],
            extra={"earlier": f"{earlier:.2f}", "later": f"{later:.2f}"},
        )
    if kind == "margin_change_pp":
        earlier, _, _ = ledger.margin_parts(
            task["scope"], task["window_earlier"], task["currency"]
        )
        later, _, _ = ledger.margin_parts(
            task["scope"], task["window_later"], task["currency"]
        )
        value, display = pack_pp(earlier, later)
        return _answered(
            base,
            value,
            display,
            "percentage_points",
            "percentage_points",
            "ebitda_margin",
            extra={"earlier_ratio": f"{earlier:.6f}", "later_ratio": f"{later:.6f}"},
        )
    raise ValueError(kind)


def _answered(base, value, display, unit, value_kind, metric, extra=None) -> dict:
    provenance = {**base["provenance"], "definition_ids": [metric]}
    if extra:
        provenance["components"] = extra
    return {
        **base,
        "status": "answered",
        "value": value,
        "display": display,
        "unit": unit,
        "value_kind": value_kind,
        "provenance": provenance,
    }


def _entities(scope: str, definitions: dict) -> list[str]:
    if scope == "consolidated":
        return list(definitions["entities"])
    if scope not in definitions["entities"]:
        raise KeyError(scope)
    return [scope]


def _decimals(row: dict) -> dict:
    converted = {}
    for key, value in row.items():
        if isinstance(value, Decimal):
            converted[key] = value
        else:
            converted[key] = value
    return converted
