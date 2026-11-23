"""Run one stand-in plan and shape the answer the same way as the oracle."""

from __future__ import annotations

from decimal import Decimal

import duckdb

from pe_metrics.compile_query import QueryPlan, compile_question
from pe_metrics.db import as_date, as_decimal, fetch_dicts
from pe_metrics.money import (
    choose_entity,
    pack_growth,
    pack_money,
    pack_pp,
    pack_ratio,
    quant_ratio,
)
from pe_metrics.sql_guard import allowed_columns, validate

# Names this compiler gives to its own subqueries. They are not source columns.
OUTPUT_COLUMNS = {
    "value",
    "numerator",
    "denominator",
    "earlier",
    "later",
    "earlier_numerator",
    "earlier_denominator",
    "later_numerator",
    "later_denominator",
}


def run_standin(
    question: dict,
    definitions: dict,
    plan: QueryPlan,
    connection: duckdb.DuckDBPyConnection,
) -> dict:
    base = {
        "question_id": question["id"],
        "question": question["text"],
        "sql": None,
    }
    blocks = plan.name == "stand_in_grounded"
    if not question["answerable"]:
        return {
            **base,
            "status": "unanswerable",
            "value": None,
            "display": "unanswerable",
            "unit": None,
            "value_kind": "unanswerable",
            "provenance": _provenance(
                plan,
                {
                    "passed": None,
                    "reason": "no agreed definition and no mapped fact column",
                },
                blocks,
                {},
                {},
                question.get("missing_concept"),
            ),
        }
    compiled = compile_question(question, definitions, plan)
    guard = validate(
        compiled.sql or "",
        allowed=allowed_columns(definitions, compiled.metric_ids) | OUTPUT_COLUMNS,
        require_monthly_fx=compiled.require_monthly_fx,
    )
    provenance = _provenance(
        plan, guard, blocks, compiled.fx_modes, compiled.traces, None
    )
    envelope = {**base, "sql": compiled.sql, "provenance": provenance}
    if blocks and not guard["passed"]:
        return {
            **envelope,
            "status": "rejected_by_guard",
            "value": None,
            "display": "rejected_by_guard",
            "unit": None,
            "value_kind": _value_kind(question),
        }
    rows = fetch_dicts(connection, compiled.sql or "")
    value, display, unit, kind, extra = interpret(question, rows, definitions)
    if extra:
        provenance["components"] = extra
    return {
        **envelope,
        "status": "answered",
        "value": value,
        "display": display,
        "unit": unit,
        "value_kind": kind,
        "provenance": provenance,
    }


def interpret(question: dict, rows: list[dict], definitions: dict):
    task = question["task"]
    kind = task["kind"]
    names = {code: spec["legal_name"] for code, spec in definitions["entities"].items()}
    if kind == "aggregate":
        amount = as_decimal(rows[0]["value"])
        value, display = pack_money(amount, task["currency"])
        return value, display, task["currency"], "money", {}
    if kind == "margin":
        numerator = as_decimal(rows[0]["numerator"])
        denominator = as_decimal(rows[0]["denominator"])
        value, display, _ = pack_ratio(numerator, denominator)
        return value, display, "ratio", "ratio", _parts(numerator, denominator)
    if kind == "compare_margin":
        scored = []
        detail = {}
        for row in rows:
            numerator = as_decimal(row["numerator"])
            denominator = as_decimal(row["denominator"])
            ratio = quant_ratio(numerator, denominator)
            scored.append((row["entity_code"], ratio))
            detail[row["entity_code"]] = {
                "ratio": f"{ratio:.6f}",
                **_parts(numerator, denominator),
            }
        winner = choose_entity(scored, names)
        return winner, winner, "entity", "entity", {"companies": detail}
    if kind == "compare_margin_growth":
        scored = []
        detail = {}
        for row in rows:
            earlier = quant_ratio(
                as_decimal(row["earlier_numerator"]), as_decimal(row["earlier_denominator"])
            )
            later = quant_ratio(
                as_decimal(row["later_numerator"]), as_decimal(row["later_denominator"])
            )
            change = later - earlier
            scored.append((row["entity_code"], change))
            detail[row["entity_code"]] = {
                "earlier": f"{earlier:.6f}",
                "later": f"{later:.6f}",
                "change": f"{change:.6f}",
            }
        winner = choose_entity(scored, names)
        return winner, winner, "entity", "entity", {"companies": detail}
    if kind == "argmax_month":
        winner = as_date(rows[0]["period_end"])
        amount = as_decimal(rows[0]["value"])
        return (
            winner.isoformat(),
            winner.isoformat(),
            "month",
            "month",
            {"amount": pack_money(amount, task["currency"])[1]},
        )
    if kind == "growth_ratio":
        earlier = as_decimal(rows[0]["earlier"])
        later = as_decimal(rows[0]["later"])
        value, display, _ = pack_growth(earlier, later)
        return (
            value,
            display,
            "ratio",
            "ratio",
            {"earlier": f"{earlier:.2f}", "later": f"{later:.2f}"},
        )
    if kind == "margin_change_pp":
        earlier = quant_ratio(
            as_decimal(rows[0]["earlier_numerator"]),
            as_decimal(rows[0]["earlier_denominator"]),
        )
        later = quant_ratio(
            as_decimal(rows[0]["later_numerator"]),
            as_decimal(rows[0]["later_denominator"]),
        )
        value, display = pack_pp(earlier, later)
        return (
            value,
            display,
            "percentage_points",
            "percentage_points",
            {"earlier_ratio": f"{earlier:.6f}", "later_ratio": f"{later:.6f}"},
        )
    raise ValueError(kind)


def _parts(numerator: Decimal, denominator: Decimal) -> dict:
    return {"numerator": f"{numerator:.2f}", "denominator": f"{denominator:.2f}"}


def _value_kind(question: dict) -> str:
    kind = question["task"]["kind"]
    return {
        "aggregate": "money",
        "margin": "ratio",
        "compare_margin": "entity",
        "compare_margin_growth": "entity",
        "argmax_month": "month",
        "growth_ratio": "ratio",
        "margin_change_pp": "percentage_points",
    }[kind]


def _provenance(plan, guard, blocks, fx_modes, traces, missing) -> dict:
    record = {
        "condition": plan.name,
        "stand_in": True,
        "language_model": False,
        "not_auto_ontology_runtime": True,
        "fx_modes": fx_modes,
        "traces": traces,
        "definition_guard": guard,
        "guard_blocks_execution": blocks,
    }
    if missing:
        record["missing_concept"] = missing
        record["reason"] = (
            "The stand-in has no agreed definition for this concept and does not invent a column."
        )
    return record
