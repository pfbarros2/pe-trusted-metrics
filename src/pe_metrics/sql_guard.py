"""Check a stand-in query against the agreed column lineage.

The grounded stand-in refuses to run a query that fails this check. The
baseline stand-in still runs, and the result records that the check would
have rejected it. This is a deterministic stand-in for Auto Ontology's SQL
validation, not a call to that product.
"""

from __future__ import annotations

import sqlglot
from sqlglot import exp


class GuardRejection(Exception):
    def __init__(self, result: dict):
        super().__init__(result.get("reason", "guard rejected SQL"))
        self.result = result


def validate(sql: str, *, allowed: set[str], require_monthly_fx: bool) -> dict:
    try:
        statements = sqlglot.parse(sql, read="duckdb")
    except sqlglot.errors.ParseError as exc:
        return _fail(sql, f"SQL did not parse: {exc}")
    if len(statements) != 1 or statements[0] is None:
        return _fail(sql, "expected one SQL statement")
    tree = statements[0]
    blocked = (exp.Insert, exp.Update, exp.Delete, exp.Drop, exp.Create, exp.Alter)
    if any(tree.find(kind) is not None for kind in blocked):
        return _fail(sql, "only a read query is allowed")
    if tree.find(exp.Star) is not None:
        return _fail(sql, "SELECT * is not allowed")
    used = sorted({column.name for column in tree.find_all(exp.Column)})
    unknown = sorted(set(used) - allowed)
    multiplied = _sum_is_multiplied(tree)
    reasons = []
    if unknown:
        reasons.append("columns outside the agreed lineage: " + ", ".join(unknown))
    if require_monthly_fx and "gbp_per_usd" not in used:
        reasons.append("a USD amount is translated without the monthly rate column")
    if require_monthly_fx and multiplied:
        reasons.append(
            "a summed amount is multiplied by one rate; the agreed rule rounds each month"
        )
    if not require_monthly_fx and multiplied:
        reasons.append("the query scales a sum by a rate the definition does not use")
    result = {
        "passed": not reasons,
        "columns": used,
        "rejected_columns": unknown,
        "sum_multiplied_by_rate": multiplied,
        "require_monthly_fx": require_monthly_fx,
        "reason": "; ".join(reasons),
    }
    return result


def allowed_columns(definitions: dict, metric_ids: list[str]) -> set[str]:
    allowed = set(definitions["structural_columns"])
    for metric_id in metric_ids:
        metric = definitions["metrics"][metric_id]
        if metric.get("derived"):
            allowed |= allowed_columns(
                definitions, [metric["numerator"], metric["denominator"]]
            )
            continue
        for columns in metric["entities"].values():
            allowed.update(columns["lineage"])
    return allowed


def _sum_is_multiplied(tree: exp.Expression) -> bool:
    for node in tree.find_all(exp.Mul):
        if node.find(exp.Sum) is not None:
            return True
    return False


def _fail(sql: str, reason: str) -> dict:
    return {
        "passed": False,
        "columns": [],
        "rejected_columns": [],
        "sum_multiplied_by_rate": False,
        "require_monthly_fx": False,
        "reason": reason,
        "sql": sql,
    }
