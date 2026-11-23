"""Assemble DuckDB SQL. Expressions come from the caller, not from question text."""

from __future__ import annotations

from datetime import date
import re

_CODE = re.compile(r"^[a-z0-9_]+$")


def aggregate_sql(
    *,
    relation: str,
    date_column: str,
    expression: str,
    start: date,
    end: date,
    fx_mode: str,
    policy_entity: str | None,
) -> str:
    joins = _joins(date_column, fx_mode, policy_entity)
    value = _aggregate_value(expression, fx_mode, end)
    return (
        f"SELECT {value} AS value\n"
        f"FROM {relation} AS t\n"
        f"{joins}"
        f"WHERE {_between('t', date_column, start, end)}"
    )


def monthly_sql(
    *,
    relation: str,
    date_column: str,
    expression: str,
    start: date,
    end: date,
    fx_mode: str,
    policy_entity: str | None,
) -> str:
    if fx_mode == "spot":
        raise ValueError("monthly rows are translated at each month's own rate")
    joins = _joins(date_column, fx_mode, policy_entity)
    value = _row_value(expression, fx_mode)
    return (
        f"SELECT t.{date_column} AS period_end, {value} AS value\n"
        f"FROM {relation} AS t\n"
        f"{joins}"
        f"WHERE {_between('t', date_column, start, end)}"
    )


def policy_aggregate_sql(
    *,
    expression: str,
    entity: str,
    start: date,
    end: date,
    fx_mode: str,
) -> str:
    _check_code(entity)
    joins = ""
    if fx_mode == "monthly":
        joins = (
            "INNER JOIN holdco.ref.fx_monthly AS fx\n"
            "  ON fx.period_end = t.period_end\n"
        )
    elif fx_mode == "spot":
        pass
    elif fx_mode != "none":
        raise ValueError(fx_mode)
    value = _aggregate_value(expression, fx_mode, end)
    return (
        f"SELECT {value} AS value\n"
        "FROM holdco.ref.policy_adjustment AS t\n"
        f"{joins}"
        f"WHERE t.entity_code = '{entity}'\n"
        f"  AND {_between('t', 'period_end', start, end)}"
    )


def sum_of(parts: list[str]) -> str:
    branches = "\nUNION ALL\n".join(
        f"SELECT value FROM (\n{sql}\n) AS part_{index}" for index, sql in enumerate(parts)
    )
    return f"SELECT SUM(value) AS value FROM (\n{branches}\n) AS combined"


def argmax_of(parts: list[str]) -> str:
    branches = "\nUNION ALL\n".join(parts)
    return (
        "SELECT period_end, SUM(value) AS value\n"
        "FROM (\n"
        f"{branches}\n"
        ") AS monthly\n"
        "GROUP BY period_end\n"
        "ORDER BY value DESC, period_end ASC\n"
        "LIMIT 1"
    )


def ratio_sql(numerator_sql: str, denominator_sql: str) -> str:
    return (
        "SELECT\n"
        f"  (SELECT value FROM (\n{numerator_sql}\n  ) AS numerator_q) AS numerator,\n"
        f"  (SELECT value FROM (\n{denominator_sql}\n  ) AS denominator_q) AS denominator"
    )


def entity_ratio_sql(parts: list[tuple[str, str, str]]) -> str:
    branches = []
    for entity, numerator_sql, denominator_sql in parts:
        _check_code(entity)
        branches.append(
            "SELECT\n"
            f"  '{entity}' AS entity_code,\n"
            f"  (SELECT value FROM (\n{numerator_sql}\n  ) AS numerator_q) AS numerator,\n"
            f"  (SELECT value FROM (\n{denominator_sql}\n  ) AS denominator_q) AS denominator"
        )
    return "\nUNION ALL\n".join(branches)


def pair_sql(earlier_sql: str, later_sql: str, earlier_name: str, later_name: str) -> str:
    return (
        "SELECT\n"
        f"  (SELECT value FROM (\n{earlier_sql}\n  ) AS earlier_q) AS {earlier_name},\n"
        f"  (SELECT value FROM (\n{later_sql}\n  ) AS later_q) AS {later_name}"
    )


def _aggregate_value(expression: str, fx_mode: str, end: date) -> str:
    if fx_mode == "none":
        return f"SUM(({expression}))"
    if fx_mode == "monthly":
        return f"SUM(CAST(({expression}) * fx.gbp_per_usd AS DECIMAL(18,2)))"
    if fx_mode == "spot":
        return (
            f"CAST(SUM(({expression})) * ("
            "SELECT gbp_per_usd FROM holdco.ref.fx_monthly "
            f"WHERE period_end = DATE '{end.isoformat()}') AS DECIMAL(18,2))"
        )
    raise ValueError(fx_mode)


def _row_value(expression: str, fx_mode: str) -> str:
    if fx_mode == "none":
        return f"({expression})"
    if fx_mode == "monthly":
        return f"CAST(({expression}) * fx.gbp_per_usd AS DECIMAL(18,2))"
    raise ValueError(fx_mode)


def _joins(date_column: str, fx_mode: str, policy_entity: str | None) -> str:
    lines = []
    if policy_entity is not None:
        _check_code(policy_entity)
        lines.append(
            "INNER JOIN holdco.ref.policy_adjustment AS adj\n"
            f"  ON adj.entity_code = '{policy_entity}'\n"
            f"  AND adj.period_end = t.{date_column}\n"
        )
    if fx_mode == "monthly":
        lines.append(
            "INNER JOIN holdco.ref.fx_monthly AS fx\n"
            f"  ON fx.period_end = t.{date_column}\n"
        )
    return "".join(lines)


def _between(alias: str, column: str, start: date, end: date) -> str:
    return (
        f"{alias}.{column} >= DATE '{start.isoformat()}' "
        f"AND {alias}.{column} <= DATE '{end.isoformat()}'"
    )


def _check_code(code: str) -> None:
    if not _CODE.match(code):
        raise ValueError(f"unexpected entity code {code}")
