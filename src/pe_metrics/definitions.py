"""Load the fund definitions and the question file."""

from __future__ import annotations

from datetime import date
from decimal import Decimal
from pathlib import Path
import re

import yaml

from pe_metrics.calendar import month_end

ROOT = Path(__file__).resolve().parents[2]
DEFINITIONS_PATH = ROOT / "definitions" / "fund_definitions.yml"
QUESTIONS_PATH = ROOT / "questions" / "questions.yml"

REQUIRED_METRICS = (
    "net_revenue",
    "adjusted_ebitda",
    "ebitda_margin",
    "ifrs16_lease_addback",
    "declined_management_addback",
    "intercompany_revenue_eliminated",
)
TASK_KINDS = {
    "aggregate",
    "margin",
    "compare_margin",
    "compare_margin_growth",
    "argmax_month",
    "growth_ratio",
    "margin_change_pp",
}
EXPR_RE = re.compile(r"^[A-Za-z0-9_+\-*/().,\s]+$")
H1_MONTHS = [1, 2, 3, 4, 5, 6]
Q4_MONTHS = [10, 11, 12]


def load_definitions(path: Path | None = None) -> dict:
    raw = yaml.safe_load((path or DEFINITIONS_PATH).read_text(encoding="utf-8"))
    calendar = raw["calendar"]
    if int(calendar["ltm_months"]) != 12:
        raise ValueError("this demo implements a 12-month LTM")
    if list(calendar["h1_months"]) != H1_MONTHS:
        raise ValueError("H1 months do not match the harness")
    if list(calendar["q4_months"]) != Q4_MONTHS:
        raise ValueError("Q4 months do not match the harness")
    for row in raw["reference"]["rates"]:
        row["period_end"] = _as_date(row["period_end"])
        row["gbp_per_usd"] = Decimal(str(row["gbp_per_usd"]))
        if row["period_end"] != month_end(row["period_end"].year, row["period_end"].month):
            raise ValueError(f"{row['period_end']} is not a month-end")
    if len(raw["reference"]["rates"]) != 24:
        raise ValueError("expected 24 FX rows")
    for name in REQUIRED_METRICS:
        if name not in raw["metrics"]:
            raise ValueError(f"missing metric {name}")
    for code, metric in raw["metrics"].items():
        if metric.get("derived"):
            continue
        for entity, spec in metric["entities"].items():
            expression = " ".join(spec["expression"].split())
            spec["expression"] = expression
            if not EXPR_RE.match(expression):
                raise ValueError(f"unsafe expression for {code}.{entity}")
            for column in spec["lineage"]:
                if column not in expression:
                    raise ValueError(f"{column} missing from {code}.{entity}")
    return raw


def load_questions(path: Path | None = None, definitions: dict | None = None) -> list[dict]:
    raw = yaml.safe_load((path or QUESTIONS_PATH).read_text(encoding="utf-8"))
    questions = raw["questions"]
    if not 15 <= len(questions) <= 25:
        raise ValueError(f"expected 15 to 25 questions, found {len(questions)}")
    definitions = definitions or load_definitions()
    seen: set[str] = set()
    for question in questions:
        qid = question["id"]
        if qid in seen:
            raise ValueError(f"duplicate {qid}")
        seen.add(qid)
        if question["answerable"]:
            task = question["task"]
            if task["kind"] not in TASK_KINDS:
                raise ValueError(f"{qid} has unknown task {task['kind']}")
            for key in ("metric",):
                if key in task and task[key] not in definitions["metrics"]:
                    raise ValueError(f"{qid} references unknown metric {task[key]}")
        else:
            if question["missing_concept"] in definitions["metrics"]:
                raise ValueError(f"{qid} missing concept collides with a metric")
    return questions


def entity_names(definitions: dict) -> dict[str, str]:
    return {
        code: spec["legal_name"] for code, spec in definitions["entities"].items()
    }


def _as_date(value: object) -> date:
    if isinstance(value, date):
        return value
    return date.fromisoformat(str(value))
