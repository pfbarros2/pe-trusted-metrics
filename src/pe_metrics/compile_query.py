"""Compile a structured question into SQL for one stand-in plan."""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import date

from pe_metrics.calendar import portfolio_month_ends, resolve_window
from pe_metrics.sql_build import (
    aggregate_sql,
    argmax_of,
    entity_ratio_sql,
    monthly_sql,
    pair_sql,
    policy_aggregate_sql,
    ratio_sql,
    sum_of,
)


@dataclass
class Compiled:
    sql: str | None
    metric_ids: list[str]
    require_monthly_fx: bool
    fx_modes: dict
    traces: dict = field(default_factory=dict)


class QueryPlan:
    name = "plan"

    def expression(self, metric: str, entity: str) -> str:
        raise NotImplementedError

    def fx_mode(self, needed: bool, task_kind: str) -> str:
        raise NotImplementedError

    def grain(self, metric: str) -> str:
        raise NotImplementedError

    def trace(self, metric: str, entity: str) -> dict:
        return {"expression": self.expression(metric, entity)}


def compile_question(question: dict, definitions: dict, plan: QueryPlan) -> Compiled:
    if not question["answerable"]:
        return Compiled(None, [], False, {})
    task = question["task"]
    metric_ids = _metric_ids(task, definitions)
    kind = task["kind"]
    if kind == "aggregate":
        sql, fx_modes, traces, needs_fx = _scope_sum(
            definitions, plan, task["metric"], task["scope"], task["window"], task["currency"], kind
        )
    elif kind == "argmax_month":
        sql, fx_modes, traces, needs_fx = _scope_argmax(
            definitions, plan, task["metric"], task["scope"], task["window"], task["currency"], kind
        )
    elif kind == "margin":
        sql, fx_modes, traces, needs_fx = _margin(
            definitions, plan, task["scope"], task["window"], task["currency"], kind
        )
    elif kind == "compare_margin":
        sql, fx_modes, traces, needs_fx = _compare_margin(
            definitions, plan, task["window"], task["currency"], kind
        )
    elif kind == "compare_margin_growth":
        sql, fx_modes, traces, needs_fx = _compare_growth(
            definitions,
            plan,
            task["window_earlier"],
            task["window_later"],
            task["currency"],
            kind,
        )
    elif kind == "growth_ratio":
        sql, fx_modes, traces, needs_fx = _growth(
            definitions,
            plan,
            task["metric"],
            task["scope"],
            task["window_earlier"],
            task["window_later"],
            task["currency"],
            kind,
        )
    elif kind == "margin_change_pp":
        sql, fx_modes, traces, needs_fx = _margin_change(
            definitions,
            plan,
            task["scope"],
            task["window_earlier"],
            task["window_later"],
            task["currency"],
            kind,
        )
    else:
        raise ValueError(kind)
    return Compiled(sql, metric_ids, needs_fx, fx_modes, traces)


def _scope_sum(definitions, plan, metric, scope, window, currency, task_kind):
    parts = []
    fx_modes = {}
    traces = {}
    needs_fx = False
    start, end = _bounds(definitions, window)
    for entity in _entities(definitions, scope):
        sql, mode, trace = _entity_sum(
            definitions, plan, metric, entity, start, end, currency, task_kind
        )
        parts.append(sql)
        fx_modes[entity] = mode
        traces[entity] = trace
        needs_fx = needs_fx or mode != "none"
    sql = parts[0] if len(parts) == 1 else sum_of(parts)
    return sql, fx_modes, traces, needs_fx


def _scope_argmax(definitions, plan, metric, scope, window, currency, task_kind):
    parts = []
    fx_modes = {}
    traces = {}
    needs_fx = False
    start, end = _bounds(definitions, window)
    for entity in _entities(definitions, scope):
        mode = _mode(definitions, plan, entity, currency, task_kind)
        expression = plan.expression(metric, entity)
        relation, date_column, policy = _from(definitions, plan, metric, entity, expression)
        parts.append(
            monthly_sql(
                relation=relation,
                date_column=date_column,
                expression=expression,
                start=start,
                end=end,
                fx_mode=mode if mode != "spot" else "monthly",
                policy_entity=policy,
            )
        )
        fx_modes[entity] = "monthly" if mode != "none" else "none"
        traces[entity] = plan.trace(metric, entity)
        needs_fx = needs_fx or mode != "none"
    return argmax_of(parts), fx_modes, traces, needs_fx


def _margin(definitions, plan, scope, window, currency, task_kind):
    numerator, fx_a, trace_a, needs_a = _scope_sum(
        definitions, plan, "adjusted_ebitda", scope, window, currency, task_kind
    )
    denominator, fx_b, trace_b, needs_b = _scope_sum(
        definitions, plan, "net_revenue", scope, window, currency, task_kind
    )
    return (
        ratio_sql(numerator, denominator),
        {"adjusted_ebitda": fx_a, "net_revenue": fx_b},
        {"adjusted_ebitda": trace_a, "net_revenue": trace_b},
        needs_a or needs_b,
    )


def _compare_margin(definitions, plan, window, currency, task_kind):
    parts = []
    fx_modes = {}
    traces = {}
    needs_fx = False
    for entity in definitions["entities"]:
        numerator, fx_a, trace_a, needs_a = _scope_sum(
            definitions, plan, "adjusted_ebitda", entity, window, currency, task_kind
        )
        denominator, fx_b, trace_b, needs_b = _scope_sum(
            definitions, plan, "net_revenue", entity, window, currency, task_kind
        )
        parts.append((entity, numerator, denominator))
        fx_modes[entity] = {"adjusted_ebitda": fx_a, "net_revenue": fx_b}
        traces[entity] = {"adjusted_ebitda": trace_a, "net_revenue": trace_b}
        needs_fx = needs_fx or needs_a or needs_b
    return entity_ratio_sql(parts), fx_modes, traces, needs_fx


def _compare_growth(definitions, plan, earlier, later, currency, task_kind):
    early_sql, fx_a, trace_a, needs_a = _compare_margin(
        definitions, plan, earlier, currency, task_kind
    )
    late_sql, fx_b, trace_b, needs_b = _compare_margin(
        definitions, plan, later, currency, task_kind
    )
    sql = (
        "SELECT earlier.entity_code AS entity_code,\n"
        "       earlier.numerator AS earlier_numerator,\n"
        "       earlier.denominator AS earlier_denominator,\n"
        "       later.numerator AS later_numerator,\n"
        "       later.denominator AS later_denominator\n"
        "FROM (\n"
        f"{early_sql}\n"
        ") AS earlier\n"
        "INNER JOIN (\n"
        f"{late_sql}\n"
        ") AS later ON earlier.entity_code = later.entity_code"
    )
    return sql, {"earlier": fx_a, "later": fx_b}, {"earlier": trace_a, "later": trace_b}, needs_a or needs_b


def _growth(definitions, plan, metric, scope, earlier, later, currency, task_kind):
    early_sql, fx_a, trace_a, needs_a = _scope_sum(
        definitions, plan, metric, scope, earlier, currency, task_kind
    )
    late_sql, fx_b, trace_b, needs_b = _scope_sum(
        definitions, plan, metric, scope, later, currency, task_kind
    )
    return (
        pair_sql(early_sql, late_sql, "earlier", "later"),
        {"earlier": fx_a, "later": fx_b},
        {"earlier": trace_a, "later": trace_b},
        needs_a or needs_b,
    )


def _margin_change(definitions, plan, scope, earlier, later, currency, task_kind):
    early_sql, fx_a, trace_a, needs_a = _margin(
        definitions, plan, scope, earlier, currency, task_kind
    )
    late_sql, fx_b, trace_b, needs_b = _margin(
        definitions, plan, scope, later, currency, task_kind
    )
    sql = (
        "SELECT early.numerator AS earlier_numerator,\n"
        "       early.denominator AS earlier_denominator,\n"
        "       late.numerator AS later_numerator,\n"
        "       late.denominator AS later_denominator\n"
        "FROM (\n"
        f"{early_sql}\n"
        ") AS early\n"
        "CROSS JOIN (\n"
        f"{late_sql}\n"
        ") AS late"
    )
    return sql, {"earlier": fx_a, "later": fx_b}, {"earlier": trace_a, "later": trace_b}, needs_a or needs_b


def _entity_sum(definitions, plan, metric, entity, start, end, currency, task_kind):
    expression = plan.expression(metric, entity)
    mode = _mode(definitions, plan, entity, currency, task_kind)
    relation, date_column, policy = _from(definitions, plan, metric, entity, expression)
    if plan.grain(metric) == "policy":
        sql = policy_aggregate_sql(
            expression=expression,
            entity=entity,
            start=start,
            end=end,
            fx_mode=mode,
        )
    else:
        sql = aggregate_sql(
            relation=relation,
            date_column=date_column,
            expression=expression,
            start=start,
            end=end,
            fx_mode=mode,
            policy_entity=policy,
        )
    return sql, mode, plan.trace(metric, entity)


def _from(definitions, plan, metric, entity, expression):
    spec = definitions["entities"][entity]
    policy = entity if "adj." in expression else None
    if plan.grain(metric) == "policy":
        return "holdco.ref.policy_adjustment", "period_end", None
    return spec["relation"], spec["date_column"], policy


def _mode(definitions, plan, entity, currency, task_kind) -> str:
    functional = definitions["entities"][entity]["functional_currency"]
    return plan.fx_mode(functional != currency, task_kind)


def _bounds(definitions, window) -> tuple[date, date]:
    days = resolve_window(
        window,
        portfolio_month_ends(definitions["reference"]["rates"]),
        int(definitions["calendar"]["ltm_months"]),
    )
    return days[0], days[-1]


def _entities(definitions, scope) -> list[str]:
    if scope == "consolidated":
        return list(definitions["entities"])
    return [scope]


def _metric_ids(task: dict, definitions: dict) -> list[str]:
    kind = task["kind"]
    if kind in {"margin", "compare_margin", "compare_margin_growth", "margin_change_pp"}:
        return ["adjusted_ebitda", "net_revenue"]
    return [task["metric"]]

