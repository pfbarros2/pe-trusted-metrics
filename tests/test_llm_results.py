"""Offline checks on the committed real-model results. No network is used."""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from pe_metrics import llm_eval

ROOT = Path(__file__).resolve().parents[1]
RESULTS = ROOT / "results"
HAVE_LLM = (RESULTS / "llm_scoreboard.json").exists()
needs_llm = pytest.mark.skipif(not HAVE_LLM, reason="no real-model results recorded")


def _rows():
    rows = []
    for name in ("llm_baseline_answers.json", "llm_grounded_answers.json"):
        rows += json.loads((RESULTS / name).read_text())
    return rows


def test_prompts_never_include_the_structured_task(questions, connection):
    schema = llm_eval.schema_text(connection)
    defs = llm_eval.definitions_text((ROOT / "definitions" / "fund_definitions.yml").read_text())
    assert "baseline_stand_in_method" not in defs
    for question in questions:
        for condition in llm_eval.CONDITIONS:
            text = json.dumps(llm_eval.build_messages(condition, question["text"], schema, defs))
            assert "answerable" in text  # the reply format mentions it
            for leak in ("kind: aggregate", "compare_margin", "missing_concept", "window_earlier"):
                assert leak not in text
        baseline = json.dumps(llm_eval.build_messages("llm_baseline", question["text"], schema, defs))
        assert "Adjusted EBITDA divided by Net Revenue" not in baseline


def test_parse_reply_handles_fences_and_think_blocks():
    reply = '<think>hmm {"x":1}</think>\n```json\n{"answerable": true, "sql": "SELECT 1 AS answer", "notes": "n"}\n```'
    parsed = llm_eval.parse_reply(reply)
    assert parsed["ok"] and parsed["sql"] == "SELECT 1 AS answer"
    assert llm_eval.parse_reply('{"answerable": false, "sql": null}')["answerable"] is False
    assert not llm_eval.parse_reply("no json here")["ok"]


def test_normalise_shapes_like_the_oracle():
    names = {"madeup_north": "Madeup North Logistics Ltd"}
    assert llm_eval.normalise("12.345", "money", names) == ("12.35", None)
    assert llm_eval.normalise(0.17135049, "ratio", names) == ("0.171350", None)
    assert llm_eval.normalise("madeup north logistics ltd", "entity", names)[0] == "Madeup North Logistics Ltd"
    assert llm_eval.normalise("2025-06-30 00:00:00", "month", names)[0] == "2025-06-30"


@needs_llm
def test_scoreboard_matches_answer_rows():
    board = json.loads((RESULTS / "llm_scoreboard.json").read_text())
    rows = _rows()
    for model in board["models"]:
        for condition, summary in model["conditions"].items():
            subset = [r for r in rows if r["model"] == model["model"] and r["condition"] == condition]
            assert summary["pooled_correct"] == sum(r["correct"] for r in subset)
            assert summary["pooled_attempts"] == len(subset)
            for item in summary["per_repeat"]:
                rep = [r for r in subset if r["repeat"] == item["repeat"]]
                assert item["correct"] == sum(r["correct"] for r in rep)


@needs_llm
def test_recorded_sql_reproduces_recorded_scores(connection, definitions):
    truth = {r["question_id"]: r for r in json.loads((RESULTS / "ground_truth.json").read_text())}
    names = {code: spec["legal_name"] for code, spec in definitions["entities"].items()}
    for row in _rows():
        t = truth[row["id"]]
        assert row["ground_truth"] == t["value"]
        if row["status"] != "answered":
            expected = llm_eval.score(t, {"status": row["status"], "value": None}) if row["status"] == "unanswerable" else False
            assert row["correct"] == expected
            continue
        executed = llm_eval.run_sql(connection, row["sql"])
        assert executed["ok"], row["id"]
        if t["value_kind"] == "unanswerable":
            value = str(executed["raw"])
        else:
            value, _ = llm_eval.normalise(executed["raw"], t["value_kind"], names)
        assert value == row["value"], (row["model"], row["condition"], row["repeat"], row["id"])
        assert llm_eval.score(t, {"status": "answered", "value": value}) == row["correct"]


@needs_llm
def test_llm_sentences_and_table_are_in_docs():
    sentences = json.loads((RESULTS / "llm_doc_sentences.json").read_text())["sentences"]
    table = (RESULTS / "llm_sample_results.md").read_text().strip()
    for name in ("README.md", "reports/findings.md"):
        text = (ROOT / name).read_text(encoding="utf-8")
        assert [s for s in sentences if s not in text] == []
    assert table in (ROOT / "reports" / "findings.md").read_text(encoding="utf-8")


def test_no_api_key_material_in_repo_files():
    marker = "nvapi" + "-"
    skip = {".git", ".venv", ".cache", "__pycache__", ".pytest_cache", "data"}
    for path in ROOT.rglob("*"):
        if path.is_file() and not (set(path.relative_to(ROOT).parts) & skip):
            assert marker not in path.read_text(encoding="utf-8", errors="ignore"), path
