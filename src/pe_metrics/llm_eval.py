"""Pure helpers for the real-model rerun: prompts, answer parsing, and scoring.

Nothing in this module touches the network. The script
``scripts/run_llm_experiment.py`` makes the model calls and uses these helpers
to build prompts and to score what came back against the same deterministic
ground truth and tolerances that the stand-in comparison uses.
"""

from __future__ import annotations

import copy
import json
import re
from datetime import date, datetime
from decimal import Decimal, InvalidOperation, ROUND_HALF_UP

import duckdb
import sqlglot
import yaml
from sqlglot import exp

from pe_metrics.compile_query import compile_question
from pe_metrics.db import fetch_dicts
from pe_metrics.evaluate import is_correct
from pe_metrics.money import PP_Q, RATIO_Q, q2
from pe_metrics.plans import GroundedPlan
from pe_metrics.sql_guard import allowed_columns, validate
from pe_metrics.standin import OUTPUT_COLUMNS

CONDITIONS = ("llm_baseline", "llm_grounded")
USER_SCHEMAS = ("ledger", "ops", "ref")

SYSTEM_RULES = """You are a data agent for a private-equity fund. You answer one question by writing exactly one read-only DuckDB SQL query.

Rules:
- Three DuckDB databases are attached under the aliases north, west and holdco. Always use fully qualified names: <alias>.<schema>.<table>.
- The query must return exactly one row with one column named answer.
- Money: return the amount as a number in the currency the question asks for (no rounding needed).
- Margins and growth rates: return a decimal fraction (0.1234 means 12.34%).
- Percentage-point changes: return the number of percentage points (1.5 means 1.5 pp).
- "Which company" questions: return the company's legal name as text.
- "Which month" questions: return the month-end date as a DATE.
- If the question cannot be answered from the available data, set answerable to false and sql to null.

Reply with one JSON object and nothing else:
{"answerable": true or false, "sql": "<the query or null>", "notes": "<one short sentence>"}"""


def schema_text(connection: duckdb.DuckDBPyConnection) -> str:
    rows = connection.execute(
        """
        SELECT c.table_catalog, c.table_schema, c.table_name, c.column_name, c.data_type,
               t.table_type
        FROM information_schema.columns c
        JOIN information_schema.tables t
          ON t.table_catalog = c.table_catalog
         AND t.table_schema = c.table_schema
         AND t.table_name = c.table_name
        WHERE c.table_catalog IN ('north', 'west', 'holdco')
        ORDER BY c.table_catalog, c.table_schema, c.table_name, c.ordinal_position
        """
    ).fetchall()
    tables: dict[tuple, list[str]] = {}
    kinds: dict[tuple, str] = {}
    for catalog, schema, table, column, dtype, ttype in rows:
        key = (catalog, schema, table)
        tables.setdefault(key, []).append(f"  {column} {dtype}")
        kinds[key] = "VIEW" if "VIEW" in ttype.upper() else "TABLE"
    blocks = []
    for key, columns in tables.items():
        blocks.append(f"{kinds[key]} {'.'.join(key)} (\n" + ",\n".join(columns) + "\n)")
    return "\n\n".join(blocks)


def definitions_text(raw_yaml: str) -> str:
    """The fund definitions file as the model sees it in condition B.

    The only key removed is fx.baseline_stand_in_method, which describes how the
    offline baseline stand-in translates currency. It is a note about the old
    harness, not part of the fund's agreed definitions.
    """
    data = yaml.safe_load(raw_yaml)
    data = copy.deepcopy(data)
    data.get("fx", {}).pop("baseline_stand_in_method", None)
    return yaml.safe_dump(data, sort_keys=False, allow_unicode=True, width=100)


def build_messages(condition: str, question_text: str, schema: str, definitions: str | None) -> list[dict]:
    if condition not in CONDITIONS:
        raise ValueError(condition)
    parts = ["DATABASE SCHEMA\n\n" + schema]
    if condition == "llm_grounded":
        if not definitions:
            raise ValueError("grounded condition needs the definitions text")
        parts.append(
            "AGREED FUND METRIC DEFINITIONS (YAML). These are the fund's agreed "
            "meanings. Follow them exactly when they apply.\n\n" + definitions
        )
    parts.append("QUESTION\n\n" + question_text)
    return [
        {"role": "system", "content": SYSTEM_RULES},
        {"role": "user", "content": "\n\n".join(parts)},
    ]


def repair_message(error: str) -> dict:
    return {
        "role": "user",
        "content": (
            "Running that SQL in DuckDB failed with this error:\n"
            + error[:1500]
            + "\nReply with a corrected JSON object in the same format."
        ),
    }


THINK_RE = re.compile(r"<think>.*?</think>", re.DOTALL)


def parse_reply(content: str | None) -> dict:
    """Pull the JSON object out of a model reply."""
    if not content:
        return {"ok": False, "error": "empty reply"}
    text = THINK_RE.sub("", content).strip()
    fence = re.search(r"```(?:json)?\s*(\{.*?\})\s*```", text, re.DOTALL)
    candidates = [fence.group(1)] if fence else []
    start, end = text.find("{"), text.rfind("}")
    if start != -1 and end > start:
        candidates.append(text[start : end + 1])
    for candidate in candidates:
        try:
            payload = json.loads(candidate)
        except json.JSONDecodeError:
            continue
        if isinstance(payload, dict) and "answerable" in payload:
            sql = payload.get("sql")
            return {
                "ok": True,
                "answerable": bool(payload.get("answerable")),
                "sql": sql.strip() if isinstance(sql, str) and sql.strip() else None,
                "notes": payload.get("notes"),
            }
    return {"ok": False, "error": "no JSON object with an answerable key"}


def run_sql(connection: duckdb.DuckDBPyConnection, sql: str) -> dict:
    try:
        rows = fetch_dicts(connection, sql)
    except Exception as exc:  # DuckDB raises several error classes
        return {"ok": False, "error": f"{type(exc).__name__}: {exc}"}
    if not rows:
        return {"ok": False, "error": "query returned no rows"}
    row = rows[0]
    column = "answer" if "answer" in row else next(iter(row))
    return {
        "ok": True,
        "raw": _jsonable(row[column]),
        "column_used": column,
        "row_count": len(rows),
    }


def normalise(raw, value_kind: str, names: dict[str, str]) -> tuple[str | None, str | None]:
    """Shape a raw SQL result like the oracle's value string. Returns (value, error)."""
    if raw is None:
        return None, "answer was NULL"
    if value_kind == "entity":
        text = str(raw).strip()
        lowered = {name.casefold(): name for name in names.values()}
        if text.casefold() in lowered:
            return lowered[text.casefold()], None
        if text in names:
            return names[text], None
        return text, None
    if value_kind == "month":
        try:
            if isinstance(raw, (date, datetime)):
                value = raw if isinstance(raw, date) and not isinstance(raw, datetime) else raw.date()
            else:
                value = date.fromisoformat(str(raw)[:10])
        except ValueError:
            return str(raw), "answer is not a date"
        return value.isoformat(), None
    try:
        number = Decimal(str(raw))
    except (InvalidOperation, ValueError):
        return str(raw), "answer is not a number"
    if value_kind == "money":
        return f"{q2(number):.2f}", None
    if value_kind == "ratio":
        return f"{number.quantize(RATIO_Q, rounding=ROUND_HALF_UP):.6f}", None
    if value_kind == "percentage_points":
        return f"{number.quantize(PP_Q, rounding=ROUND_HALF_UP):.4f}", None
    raise ValueError(value_kind)


def score(truth: dict, attempt: dict) -> bool:
    return is_correct(truth, attempt)


def abs_error(truth: dict, attempt: dict) -> str | None:
    if truth["value_kind"] not in {"money", "ratio", "percentage_points"}:
        return None
    if attempt.get("status") != "answered" or attempt.get("value") is None:
        return None
    try:
        return format(abs(Decimal(attempt["value"]) - Decimal(truth["value"])), "f")
    except InvalidOperation:
        return None


def query_aliases(sql: str) -> set[str]:
    """Names the query defines itself (column aliases, CTE and table aliases)."""
    try:
        tree = sqlglot.parse_one(sql, read="duckdb")
    except Exception:
        return set()
    names = set()
    for node in tree.find_all(exp.Alias):
        names.add(node.alias)
    for node in tree.find_all(exp.CTE):
        names.add(node.alias)
    for node in tree.find_all(exp.TableAlias):
        names.add(node.name)
        for column in node.columns:
            names.add(column.name)
    return {name for name in names if name}


def guard_check(question: dict, definitions: dict, sql: str | None) -> dict:
    """Run the repo's own SQL guard (not Auto Ontology) on a model query.

    The allowed set is the agreed lineage of the metrics the question needs,
    taken from the structured task block the model never sees, plus the guard's
    structural columns, the stand-in's output names, ``legal_name`` (to return a
    company name), and the aliases the query itself defines. The monthly FX
    rule applies when the question needs a USD amount in GBP.
    """
    if not question["answerable"] or not sql:
        return {"passed": None, "reason": "no SQL to check"}
    compiled = compile_question(question, definitions, GroundedPlan(definitions))
    allowed = (
        allowed_columns(definitions, compiled.metric_ids)
        | OUTPUT_COLUMNS
        | {"answer", "legal_name"}
        | query_aliases(sql)
    )
    result = validate(sql, allowed=allowed, require_monthly_fx=compiled.require_monthly_fx)
    result.pop("sql", None)
    result["implementation"] = "repo sql_guard (pe_metrics.sql_guard), not Auto Ontology"
    return result


def summarise(rows: list[dict], n_questions: int) -> dict:
    """Accuracy per repeat, plus mean, min, max and per-question stability."""
    repeats = sorted({row["repeat"] for row in rows})
    per_repeat = []
    for rep in repeats:
        subset = [row for row in rows if row["repeat"] == rep]
        correct = sum(row["correct"] for row in subset)
        gated = sum(row["correct_if_guard_blocks"] for row in subset)
        per_repeat.append(
            {
                "repeat": rep,
                "correct": correct,
                "n": len(subset),
                "display": f"{correct}/{len(subset)}",
                "correct_if_guard_blocks": gated,
                "guard_passed": sum(1 for row in subset if row["guard"].get("passed") is True),
                "sql_errors_after_repair": sum(1 for row in subset if row["status"] == "sql_error"),
                "format_failures": sum(1 for row in subset if row["status"] == "format_error"),
            }
        )
    counts = [item["correct"] for item in per_repeat]
    ids = sorted({row["id"] for row in rows})
    by_question = {}
    for qid in ids:
        marks = [row["correct"] for row in sorted(rows, key=lambda r: r["repeat"]) if row["id"] == qid]
        by_question[qid] = {
            "correct_in_repeats": sum(marks),
            "repeats": len(marks),
            "marks": ["correct" if mark else "incorrect" for mark in marks],
        }
    mean = Decimal(sum(counts)) / Decimal(len(counts)) if counts else Decimal(0)
    return {
        "n_questions": n_questions,
        "repeats": len(repeats),
        "per_repeat": per_repeat,
        "correct_counts": counts,
        "mean_correct": f"{mean.quantize(Decimal('0.01'), rounding=ROUND_HALF_UP):.2f}",
        "min_correct": min(counts) if counts else 0,
        "max_correct": max(counts) if counts else 0,
        "pooled_correct": sum(counts),
        "pooled_attempts": sum(item["n"] for item in per_repeat),
        "questions_always_correct": [q for q, v in by_question.items() if v["correct_in_repeats"] == v["repeats"]],
        "questions_never_correct": [q for q, v in by_question.items() if v["correct_in_repeats"] == 0],
        "questions_unstable": [
            q for q, v in by_question.items() if 0 < v["correct_in_repeats"] < v["repeats"]
        ],
        "per_question": by_question,
    }


def _jsonable(value):
    if isinstance(value, Decimal):
        return format(value, "f")
    if isinstance(value, (date, datetime)):
        return value.isoformat()
    if isinstance(value, (int, float, str, bool)) or value is None:
        return value
    return str(value)
