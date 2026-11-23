#!/usr/bin/env python3
"""Turn the raw real-model run files into the committed LLM results.

Reads results/llm_runs/*.json (written by run_llm_experiment.py), runs
NVIDIA Auto Ontology's catalog SQL parser over every executed model query
(in a separate Python environment where the upstream package is installed),
and writes:

- results/llm_baseline_answers.json
- results/llm_grounded_answers.json
- results/llm_auto_ontology_lineage.json
- results/llm_scoreboard.json
- results/llm_sample_results.md
- results/llm_doc_sentences.json

No network access is needed.
"""

from __future__ import annotations

import argparse
import json
import os
import subprocess
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

from pe_metrics.compile_query import compile_question  # noqa: E402
from pe_metrics.definitions import load_definitions, load_questions  # noqa: E402
from pe_metrics.generate import generate_portfolio  # noqa: E402
from pe_metrics.llm_eval import summarise  # noqa: E402
from pe_metrics.plans import GroundedPlan  # noqa: E402
from pe_metrics.sql_guard import allowed_columns  # noqa: E402

RESULTS = ROOT / "results"
SHORT = {
    "nvidia/nemotron-3-ultra-550b-a55b": "Nemotron 3 Ultra",
    "mistralai/mistral-large-4-1t-a52b": "Mistral Large 4",
}
EXTRA_OK = {"period_end", "MonthEnding", "gbp_per_usd", "entity_code", "legal_name", "LegalName"}


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--ao-python", default=os.environ.get("AUTO_ONTOLOGY_PYTHON"))
    parser.add_argument("--ao-src", default=os.environ.get("AUTO_ONTOLOGY_SRC"))
    parser.add_argument("--primary", default="nvidia/nemotron-3-ultra-550b-a55b")
    parser.add_argument("--data-dir", type=Path, default=ROOT / "data" / "duckdb")
    args = parser.parse_args()

    definitions = load_definitions()
    questions = {q["id"]: q for q in load_questions(definitions=definitions)}
    runs = [json.loads(p.read_text()) for p in sorted((RESULTS / "llm_runs").glob("*.json"))]
    runs.sort(key=lambda run: run["model"] != args.primary)
    rows = [row for run in runs for row in run["rows"]]

    lineage = _lineage(args, rows, definitions)
    for row in rows:
        info = lineage["lineage"].get(_key(row))
        row["auto_ontology_lineage"] = info
        row["lineage_check"] = _lineage_check(row, info, questions, definitions)

    answers = {condition: [] for condition in ("llm_baseline", "llm_grounded")}
    for row in rows:
        slim = {k: v for k, v in row.items() if k != "turns"}
        slim["model_calls"] = [
            {k: turn.get(k) for k in ("seconds", "finish_reason", "model_returned", "usage", "error")}
            for turn in row["turns"]
        ]
        answers[row["condition"]].append(slim)
    for condition, items in answers.items():
        items.sort(key=lambda r: (r["model"] != args.primary, r["model"], r["repeat"], r["id"]))
        _write(RESULTS / f"{condition}_answers.json", items)

    board = {
        "harness": "real_model",
        "language_model": True,
        "fiction": True,
        "endpoint": runs[0]["endpoint"] if runs else None,
        "temperature": 0,
        "model_sees": {
            "llm_baseline": "question text + DuckDB schema (table and column names and types)",
            "llm_grounded": "question text + fund definitions YAML + the same DuckDB schema",
            "structured_task_block_sent": False,
        },
        "scoring": "same deterministic ground truth (results/ground_truth.json) and tolerances as the stand-in",
        "repair_policy": "at most one follow-up turn per question, for a SQL execution error or an unparseable reply, in both conditions",
        "guard": "repo SQL guard (pe_metrics.sql_guard) applied after the fact; correct_if_guard_blocks counts a guard rejection as not answered. Not Auto Ontology.",
        "auto_ontology": {k: v for k, v in lineage.items() if k != "lineage"},
        "kumo_rfm": "off",
        "models_planned_not_run": [
            {
                "model": "mistralai/mistral-large-4-1t-a52b",
                "reason": "skipped for time: the account's throughput was too low to finish a second model on 2026-11-13; two started attempts were stopped before any answer was recorded",
            }
        ],
        "models": [],
    }
    for run in runs:
        model_rows = [row for row in rows if row["model"] == run["model"]]
        entry = {
            "model": run["model"],
            "model_returned": sorted({t.get("model_returned") for r in model_rows for t in r["turns"] if t.get("model_returned")}),
            "run_date": run.get("run_date"),
            "resume_note": run.get("resume_note"),
            "run_started_utc": run["started_utc"],
            "run_finished_utc": run["finished_utc"],
            "max_tokens": run["max_tokens"],
            "repeats": run["repeats"],
            "completion_tokens": sum((t.get("usage") or {}).get("completion_tokens") or 0 for r in model_rows for t in r["turns"]),
            "prompt_tokens": sum((t.get("usage") or {}).get("prompt_tokens") or 0 for r in model_rows for t in r["turns"]),
            "model_calls": sum(len(r["turns"]) for r in model_rows),
            "conditions": {},
        }
        for condition in ("llm_baseline", "llm_grounded"):
            subset = [row for row in model_rows if row["condition"] == condition]
            summary = summarise(subset, len(questions))
            summary["lineage_check_passed"] = sum(1 for r in subset if r["lineage_check"].get("passed") is True)
            summary["auto_ontology_parsed"] = sum(1 for r in subset if (r["auto_ontology_lineage"] or {}).get("parsed"))
            summary["sql_executed"] = sum(1 for r in subset if r["status"] == "answered")
            entry["conditions"][condition] = summary
        board["models"].append(entry)
    _write(RESULTS / "llm_scoreboard.json", board)
    md = _table(board, rows, questions, args.primary)
    (RESULTS / "llm_sample_results.md").write_text(md, encoding="utf-8")
    _write(RESULTS / "llm_doc_sentences.json", {"sentences": _sentences(board, rows)})
    print("wrote LLM results")


def _key(row: dict) -> str:
    return f"{row['model']}|{row['condition']}|{row['repeat']}|{row['id']}"


def _lineage(args, rows, definitions) -> dict:
    sqls = {_key(r): r["sql"] for r in rows if r["status"] == "answered" and r["sql"]}
    if not args.ao_python or not args.ao_src:
        cached = RESULTS / "llm_auto_ontology_lineage.json"
        if cached.exists():
            return json.loads(cached.read_text())
        raise SystemExit("set --ao-python and --ao-src to run the Auto Ontology parser")
    paths = generate_portfolio(args.data_dir, definitions)
    with tempfile.TemporaryDirectory() as tmp:
        inp = Path(tmp) / "sql.json"
        out = Path(tmp) / "lineage.json"
        inp.write_text(json.dumps(sqls))
        env = {k: v for k, v in os.environ.items() if "API_KEY" not in k}
        env["PYTHONPATH"] = str(ROOT / "src")
        subprocess.check_call(
            [args.ao_python, "-m", "pe_metrics.auto_ontology_lineage", "--src", args.ao_src,
             "--in", str(inp), "--out", str(out), "--north", str(paths["north"]),
             "--west", str(paths["west"]), "--holdco", str(paths["holdco"])],
            cwd=args.ao_src, env=env, stdout=subprocess.DEVNULL,
        )
        data = json.loads(out.read_text())
    data["queries_sent_to_parser"] = len(sqls)
    data["queries_parsed"] = sum(1 for v in data["lineage"].values() if v.get("parsed"))
    _write(RESULTS / "llm_auto_ontology_lineage.json", data)
    return data


def _lineage_check(row, info, questions, definitions) -> dict:
    """Compare the source columns Auto Ontology resolved with the agreed lineage.

    The comparison logic is this repo's. The column list comes from Auto
    Ontology's parser.
    """
    question = questions[row["id"]]
    if not info or not info.get("parsed") or not question["answerable"]:
        return {"passed": None}
    compiled = compile_question(question, definitions, GroundedPlan(definitions))
    allowed = allowed_columns(definitions, compiled.metric_ids) | EXTRA_OK
    used = {c.split(".")[-1] for c in info["source_columns"]}
    outside = sorted(used - allowed)
    return {"passed": not outside, "columns_outside_agreed_lineage": outside}


def _table(board, rows, questions, primary) -> str:
    model = next((m for m in board["models"] if m["model"] == primary), board["models"][0])
    base = model["conditions"]["llm_baseline"]["per_question"]
    grnd = model["conditions"]["llm_grounded"]["per_question"]
    truth = {r["id"]: r["ground_truth_display"] for r in rows}
    sample = {}
    for r in rows:
        if r["model"] == model["model"] and r["repeat"] == 1:
            sample[(r["condition"], r["id"])] = r["value"] if r["status"] == "answered" else r["status"]
    lines = [
        f"| ID | Ground truth | Baseline answer, repeat 1 | Baseline correct | Grounded answer, repeat 1 | Grounded correct |",
        "| --- | --- | --- | --- | --- | --- |",
    ]
    for qid in sorted(questions):
        lines.append(
            f"| {qid} | {truth[qid]} | {sample.get(('llm_baseline', qid))} | "
            f"{base[qid]['correct_in_repeats']} of {base[qid]['repeats']} | "
            f"{sample.get(('llm_grounded', qid))} | {grnd[qid]['correct_in_repeats']} of {grnd[qid]['repeats']} |"
        )
    return "\n".join(lines) + "\n"


def _sentences(board, rows) -> list[str]:
    out = []
    for model in board["models"]:
        name = SHORT.get(model["model"], model["model"])
        for condition, label in (("llm_baseline", "schema-only baseline"), ("llm_grounded", "definitions-grounded")):
            s = model["conditions"][condition]
            counts = ", ".join(str(c) for c in s["correct_counts"])
            out.append(
                f"{name} ({model['model']}), {label}: {s['pooled_correct']} of {s['pooled_attempts']} "
                f"answers correct across {s['repeats']} repeats (per repeat: {counts} of {s['n_questions']}; "
                f"mean {s['mean_correct']})."
            )
        g = model["conditions"]["llm_grounded"]
        b = model["conditions"]["llm_baseline"]
        out.append(
            f"{name}: if the repo SQL guard had blocked every query it rejected, the baseline would have "
            f"{sum(p['correct_if_guard_blocks'] for p in b['per_repeat'])} of {b['pooled_attempts']} correct and the grounded "
            f"condition {sum(p['correct_if_guard_blocks'] for p in g['per_repeat'])} of {g['pooled_attempts']}."
        )
        model_rows = [r for r in rows if r["model"] == model["model"]]
        for condition, label in (("llm_baseline", "baseline"), ("llm_grounded", "grounded")):
            sub = [r for r in model_rows if r["condition"] == condition]
            ran = [r for r in sub if r["status"] == "answered"]
            wrong_passed = sum(1 for r in ran if not r["correct"] and r["guard"].get("passed") is True)
            wrong_blocked = sum(1 for r in ran if not r["correct"] and r["guard"].get("passed") is False)
            right_blocked = sum(1 for r in ran if r["correct"] and r["guard"].get("passed") is False)
            ao_ok = sum(1 for r in ran if (r.get("auto_ontology_lineage") or {}).get("parsed"))
            lin_out = sum(1 for r in ran if r["lineage_check"].get("passed") is False)
            repairs = sum(1 for r in sub if len(r["turns"]) > 1)
            out.append(
                f"{name}, {label}: {len(ran)} queries ran. The repo SQL guard rejected {wrong_blocked} wrong and "
                f"{right_blocked} correct answers, and passed {wrong_passed} wrong answers."
            )
            out.append(
                f"{name}, {label}: Auto Ontology's catalog parser resolved tables and source columns for {ao_ok} of "
                f"{len(ran)} executed queries; {lin_out} of them read a column outside the fund's agreed lineage."
            )
            out.append(f"{name}, {label}: {repairs} of {len(sub)} answers needed the one allowed follow-up turn.")
        out.append(
            f"{name}: {model['model_calls']} model calls used {model['prompt_tokens']} prompt tokens and "
            f"{model['completion_tokens']} completion tokens."
        )
    return out


def _write(path: Path, payload) -> None:
    path.write_text(json.dumps(payload, indent=2) + "\n", encoding="utf-8")


if __name__ == "__main__":
    main()
