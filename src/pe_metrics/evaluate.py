"""Score the oracle, the baseline stand-in, and the grounded stand-in."""

from __future__ import annotations

from decimal import Decimal, ROUND_HALF_UP

import duckdb

from pe_metrics.calendar import resolve_window
from pe_metrics.generate import NORTH_APPROVAL_RATE, WEST_APPROVAL_RATE
from pe_metrics.money import (
    MONEY_TOLERANCE,
    PP_TOLERANCE,
    RATIO_TOLERANCE,
    choose_entity,
    pack_ratio,
    quant_ratio,
)
from pe_metrics.oracle import Ledger, answer_question
from pe_metrics.plans import BaselinePlan, GroundedPlan
from pe_metrics.standin import run_standin


def evaluate(connection: duckdb.DuckDBPyConnection, definitions: dict, questions: list[dict]) -> dict:
    ledger = Ledger(connection, definitions)
    truth = [answer_question(question, ledger, definitions) for question in questions]
    baseline_plan = BaselinePlan(connection, definitions)
    grounded_plan = GroundedPlan(definitions)
    baseline = [
        run_standin(question, definitions, baseline_plan, connection) for question in questions
    ]
    grounded = [
        run_standin(question, definitions, grounded_plan, connection) for question in questions
    ]
    per_question = []
    for truth_row, baseline_row, grounded_row in zip(truth, baseline, grounded):
        per_question.append(
            {
                "id": truth_row["question_id"],
                "question": truth_row["question"],
                "ground_truth": truth_row["display"],
                "baseline": baseline_row["display"],
                "grounded": grounded_row["display"],
                "baseline_within_tolerance": is_correct(truth_row, baseline_row),
                "grounded_within_tolerance": is_correct(truth_row, grounded_row),
                "baseline_guard_passed": baseline_row["provenance"]["definition_guard"].get(
                    "passed"
                ),
                "grounded_guard_passed": grounded_row["provenance"]["definition_guard"].get(
                    "passed"
                ),
            }
        )
    n_questions = len(questions)
    baseline_correct = sum(row["baseline_within_tolerance"] for row in per_question)
    grounded_correct = sum(row["grounded_within_tolerance"] for row in per_question)
    scoreboard = {
        "harness": "deterministic_stand_in",
        "language_model": False,
        "auto_ontology_answered_questions": False,
        "n_questions": n_questions,
        "baseline_correct": baseline_correct,
        "grounded_correct": grounded_correct,
        "baseline_accuracy_display": f"{baseline_correct}/{n_questions}",
        "grounded_accuracy_display": f"{grounded_correct}/{n_questions}",
        "baseline_accuracy_percent_display": _percent(baseline_correct, n_questions),
        "grounded_accuracy_percent_display": _percent(grounded_correct, n_questions),
        "tolerance": {
            "money": format(MONEY_TOLERANCE, "f"),
            "ratio": format(RATIO_TOLERANCE, "f"),
            "percentage_points": format(PP_TOLERANCE, "f"),
            "entity_and_month": "exact",
        },
        "per_question": per_question,
    }
    example = _example(ledger, definitions)
    sentences = _sentences(scoreboard, example)
    return {
        "ground_truth": truth,
        "baseline": baseline,
        "grounded": grounded,
        "scoreboard": scoreboard,
        "example": example,
        "sentences": sentences,
        "sample_markdown": render_table(per_question),
        "column_choices": {
            entity: {
                metric: baseline_plan.trace(metric, entity)
                for metric in (
                    "net_revenue",
                    "adjusted_ebitda",
                    "ifrs16_lease_addback",
                    "declined_management_addback",
                    "intercompany_revenue_eliminated",
                )
            }
            for entity in definitions["entities"]
        },
    }


def is_correct(truth: dict, attempt: dict) -> bool:
    if truth["value_kind"] == "unanswerable":
        return attempt["status"] == "unanswerable"
    if attempt["status"] != "answered" or truth["status"] != "answered":
        return False
    if truth["value_kind"] in {"entity", "month"}:
        return attempt["value"] == truth["value"]
    tolerance = {
        "money": MONEY_TOLERANCE,
        "ratio": RATIO_TOLERANCE,
        "percentage_points": PP_TOLERANCE,
    }[truth["value_kind"]]
    return abs(Decimal(attempt["value"]) - Decimal(truth["value"])) <= tolerance


def render_table(rows: list[dict]) -> str:
    header = (
        "| ID | Question | Ground truth | Baseline stand-in | Grounded stand-in "
        "| Baseline within tolerance | Grounded within tolerance |"
    )
    rule = "| --- | --- | --- | --- | --- | --- | --- |"
    body = []
    for row in rows:
        body.append(
            "| {id} | {question} | {ground_truth} | {baseline} | {grounded} | {baseline_ok} | {grounded_ok} |".format(
                id=row["id"],
                question=row["question"].replace("|", "/"),
                ground_truth=row["ground_truth"],
                baseline=row["baseline"],
                grounded=row["grounded"],
                baseline_ok="yes" if row["baseline_within_tolerance"] else "no",
                grounded_ok="yes" if row["grounded_within_tolerance"] else "no",
            )
        )
    return "\n".join([header, rule, *body]) + "\n"


def _example(ledger: Ledger, definitions: dict) -> dict:
    january = ledger.months[0]
    names = {code: spec["legal_name"] for code, spec in definitions["entities"].items()}
    window = {"kind": "ltm", "end": "2025-12-31"}
    days = resolve_window(window, ledger.months, 12)
    fund_scores = []
    local_scores = []
    fund_detail = {}
    local_detail = {}
    for code, table, num_key, den_key in (
        ("madeup_north", ledger.north, "reported_ebitda", "billed_turnover_gross"),
        ("madeup_west", ledger.west, "EbitdaAfterAddbacks", "GrossBillings"),
    ):
        ratio, numerator, denominator = ledger.margin_parts(code, window, "GBP")
        fund_scores.append((code, ratio))
        _, display, _ = pack_ratio(numerator, denominator)
        fund_detail[code] = display
        local_num = sum(table[day][num_key] for day in days)
        local_den = sum(table[day][den_key] for day in days)
        local_ratio = quant_ratio(local_num, local_den)
        local_scores.append((code, local_ratio))
        _, local_display, _ = pack_ratio(local_num, local_den)
        local_detail[code] = local_display
    north = ledger.north[january]
    west = ledger.west[january]
    return {
        "january": january.isoformat(),
        "north_january_fund_net_revenue": _money(
            ledger.functional("net_revenue", "madeup_north", january), "GBP"
        ),
        "north_january_fund_adjusted_ebitda": _money(
            ledger.functional("adjusted_ebitda", "madeup_north", january), "GBP"
        ),
        "north_january_reported_ebitda": _money(north["reported_ebitda"], "GBP"),
        "west_january_fund_adjusted_ebitda": _money(
            ledger.functional("adjusted_ebitda", "madeup_west", january), "USD"
        ),
        "west_january_local_ebitda": _money(west["EbitdaAfterAddbacks"], "USD"),
        "ltm_dec_2025_fund_margin": fund_detail,
        "ltm_dec_2025_local_margin": local_detail,
        "ltm_dec_2025_fund_margin_leader": choose_entity(fund_scores, names),
        "ltm_dec_2025_local_margin_leader": choose_entity(local_scores, names),
    }


def _sentences(scoreboard: dict, example: dict) -> list[str]:
    baseline_ids = [
        row["id"] for row in scoreboard["per_question"] if row["baseline_within_tolerance"]
    ]
    grounded_ids = [
        row["id"] for row in scoreboard["per_question"] if row["grounded_within_tolerance"]
    ]
    by_id = {row["id"]: row for row in scoreboard["per_question"]}
    sentences = [
        (
            f"The stand-in baseline was within tolerance on {scoreboard['baseline_correct']} "
            f"of {scoreboard['n_questions']} questions "
            f"({scoreboard['baseline_accuracy_percent_display']})."
        ),
        (
            f"The stand-in grounded condition was within tolerance on "
            f"{scoreboard['grounded_correct']} of {scoreboard['n_questions']} questions "
            f"({scoreboard['grounded_accuracy_percent_display']})."
        ),
        (
            "Money answers match within "
            f"{scoreboard['tolerance']['money']}, ratios within "
            f"{scoreboard['tolerance']['ratio']}, and percentage points within "
            f"{scoreboard['tolerance']['percentage_points']}. "
            "Entity names and months must match exactly."
        ),
        "Baseline question ids within tolerance: " + ", ".join(baseline_ids) + ".",
        "Grounded question ids within tolerance: " + ", ".join(grounded_ids) + ".",
        (
            "Each company fact table has 24 month-end rows, from 2024-01-31 through 2025-12-31."
        ),
        "The fund consolidates 2 portfolio companies.",
        (
            "The fictional schedule approves "
            f"{NORTH_APPROVAL_RATE:.2f} of Madeup North's proposed add-back and "
            f"{WEST_APPROVAL_RATE:.2f} of Madeup West's booked add-back, "
            "rounded half away from zero to the cent."
        ),
        (
            f"In {example['january']}, Madeup North fund Net Revenue was "
            f"{example['north_january_fund_net_revenue']} and fund Adjusted EBITDA was "
            f"{example['north_january_fund_adjusted_ebitda']}. "
            f"The company's own reported EBITDA that month was "
            f"{example['north_january_reported_ebitda']}."
        ),
        (
            f"In {example['january']}, Madeup West fund Adjusted EBITDA was "
            f"{example['west_january_fund_adjusted_ebitda']}. "
            f"The company's own EbitdaAfterAddbacks that month was "
            f"{example['west_january_local_ebitda']}."
        ),
        (
            "LTM Adjusted EBITDA margin at Dec 2025 on the fund definitions was "
            f"{example['ltm_dec_2025_fund_margin']['madeup_north']} for Madeup North and "
            f"{example['ltm_dec_2025_fund_margin']['madeup_west']} for Madeup West. "
            f"The higher fund margin was {example['ltm_dec_2025_fund_margin_leader']}."
        ),
        (
            "The local headline margin over the same months was "
            f"{example['ltm_dec_2025_local_margin']['madeup_north']} for Madeup North and "
            f"{example['ltm_dec_2025_local_margin']['madeup_west']} for Madeup West. "
            f"The higher local headline margin was {example['ltm_dec_2025_local_margin_leader']}."
        ),
    ]
    for qid in ("Q01", "Q04", "Q08", "Q11", "Q12", "Q15", "Q16", "Q20", "Q21"):
        row = by_id[qid]
        sentences.append(
            f"{qid} ground truth is {row['ground_truth']}. "
            f"The baseline stand-in answered {row['baseline']}. "
            f"The grounded stand-in answered {row['grounded']}."
        )
    q16 = by_id["Q16"]
    if q16["baseline_within_tolerance"] and q16["baseline_guard_passed"] is False:
        sentences.append(
            "Q16 matched the ground-truth month "
            f"{q16['ground_truth']} under the baseline stand-in, and the definition "
            "guard still rejected that baseline SQL."
        )
    return sentences


def _money(amount, currency: str) -> str:
    from pe_metrics.money import pack_money

    return pack_money(amount, currency)[1]


def _percent(correct: int, n_questions: int) -> str:
    if n_questions == 0:
        return "0.0%"
    percent = (Decimal(correct) / Decimal(n_questions) * Decimal(100)).quantize(
        Decimal("0.1"), rounding=ROUND_HALF_UP
    )
    return f"{percent:.1f}%"
