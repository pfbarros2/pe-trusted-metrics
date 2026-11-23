"""The two stand-in plans. Neither plan calls a language model."""

from __future__ import annotations

import re

import duckdb

from pe_metrics.compile_query import QueryPlan
from pe_metrics.db import fetch_dicts

TOKENS = {
    "net_revenue": ("revenue", "sales", "turnover", "billing", "billings", "billed"),
    "adjusted_ebitda": ("ebitda",),
    "ifrs16_lease_addback": ("lease", "rent", "ifrs"),
    "declined_management_addback": ("addback",),
    "intercompany_revenue_eliminated": ("intercompany",),
}
HEADLINE_BONUS = ("gross", "reported", "after", "headline")


class GroundedPlan(QueryPlan):
    """SQL expressions are copied from the agreed definitions."""

    name = "stand_in_grounded"

    def __init__(self, definitions: dict):
        self.definitions = definitions

    def expression(self, metric: str, entity: str) -> str:
        return self.definitions["metrics"][metric]["entities"][entity]["expression"]

    def fx_mode(self, needed: bool, task_kind: str) -> str:
        if not needed:
            return "none"
        return "monthly"

    def grain(self, metric: str) -> str:
        return self.definitions["metrics"][metric].get("grain", "fact")

    def trace(self, metric: str, entity: str) -> dict:
        spec = self.definitions["metrics"][metric]["entities"][entity]
        return {
            "definition_id": metric,
            "expression": spec["expression"],
            "lineage": list(spec["lineage"]),
            "grain": self.grain(metric),
        }


class BaselinePlan(QueryPlan):
    """Pick a fact-table column by token overlap. Do not open the policy table."""

    name = "stand_in_baseline"

    def __init__(self, connection: duckdb.DuckDBPyConnection, definitions: dict):
        self.definitions = definitions
        self.columns: dict[str, list[str]] = {}
        for entity, spec in definitions["entities"].items():
            self.columns[entity] = numeric_columns(connection, spec["relation"])

    def expression(self, metric: str, entity: str) -> str:
        ranking = rank_columns(self.columns[entity], metric)
        if not ranking:
            raise KeyError(f"no fact column matched {metric} for {entity}")
        return ranking[0]["column"]

    def fx_mode(self, needed: bool, task_kind: str) -> str:
        if not needed:
            return "none"
        if task_kind == "argmax_month":
            return "monthly"
        return "spot"

    def grain(self, metric: str) -> str:
        return "fact"

    def trace(self, metric: str, entity: str) -> dict:
        ranking = rank_columns(self.columns[entity], metric)
        return {
            "heuristic": (
                "Score fact-table columns by token overlap. One extra point if "
                "the name contains gross, reported, after, or headline. Highest "
                "score wins, then the shorter name, then alphabetical order. "
                "The fund policy table is not searched."
            ),
            "chosen_column": ranking[0]["column"] if ranking else None,
            "ranking": ranking,
            "searched_relation": self.definitions["entities"][entity]["relation"],
        }


def numeric_columns(connection: duckdb.DuckDBPyConnection, relation: str) -> list[str]:
    names = []
    for row in fetch_dicts(connection, f"DESCRIBE {relation}"):
        kind = str(row["column_type"]).upper()
        if any(token in kind for token in ("DECIMAL", "DOUBLE", "FLOAT", "INT", "NUMERIC")):
            names.append(row["column_name"])
    return names


def column_words(name: str) -> list[str]:
    spaced = re.sub(r"([a-z0-9])([A-Z])", r"\1 \2", name).replace("_", " ")
    return [part.lower() for part in spaced.split() if part]


def score_column(name: str, tokens: tuple[str, ...]) -> int:
    words = column_words(name)
    hits = sum(1 for token in tokens if token in words)
    if hits == 0:
        return 0
    bonus = 1 if any(token in words for token in HEADLINE_BONUS) else 0
    return hits + bonus


def rank_columns(columns: list[str], metric: str) -> list[dict]:
    ranked = []
    for name in columns:
        score = score_column(name, TOKENS[metric])
        if score:
            ranked.append({"column": name, "score": score, "length": len(name)})
    ranked.sort(key=lambda item: (-item["score"], item["length"], item["column"].lower()))
    return ranked
