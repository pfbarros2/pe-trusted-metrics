"""Docs may only repeat figures that the experiment wrote to results/."""

from __future__ import annotations

import json
import re
from pathlib import Path

import pytest

from pe_metrics.auto_ontology_probe import MODEL_KEY_NAMES, model_key_report
from pe_metrics.evaluate import evaluate

ROOT = Path(__file__).resolve().parents[1]
NUMBER = re.compile(r"\d[\d,]*(?:\.\d+)?%?")
SKIP_DIRS = {".git", ".venv", ".cache", "__pycache__", ".pytest_cache", "data"}


def _tokens(text: str) -> set[str]:
    return set(NUMBER.findall(text))


def _allowed_tokens() -> set[str]:
    allowed: set[str] = set()
    for path in (ROOT / "results").rglob("*"):
        if path.is_file():
            allowed |= _tokens(path.read_text(encoding="utf-8"))
    return allowed


def test_recorded_sentences_and_table_are_copied_verbatim():
    sentences = json.loads((ROOT / "results" / "doc_sentences.json").read_text())["sentences"]
    table = (ROOT / "results" / "sample_results.md").read_text().strip()
    for name in ("README.md", "reports/findings.md"):
        text = (ROOT / name).read_text(encoding="utf-8")
        missing = [sentence for sentence in sentences if sentence not in text]
        assert missing == []
        assert table in text


def test_doc_numbers_are_subset_of_result_files():
    allowed = _allowed_tokens()
    for name in ("README.md", "reports/findings.md"):
        extra = sorted(_tokens((ROOT / name).read_text(encoding="utf-8")) - allowed)
        assert extra == []


def test_committed_answers_match_a_fresh_evaluation(connection, definitions, questions):
    outcome = evaluate(connection, definitions, questions)
    scoreboard = json.loads((ROOT / "results" / "scoreboard.json").read_text())
    assert outcome["scoreboard"]["n_questions"] == scoreboard["n_questions"]
    assert outcome["scoreboard"]["baseline_correct"] == scoreboard["baseline_correct"]
    assert outcome["scoreboard"]["grounded_correct"] == scoreboard["grounded_correct"]
    fresh = [
        (
            row["id"],
            row["ground_truth"],
            row["baseline"],
            row["grounded"],
            row["baseline_within_tolerance"],
            row["grounded_within_tolerance"],
        )
        for row in outcome["scoreboard"]["per_question"]
    ]
    saved = [
        (
            row["id"],
            row["ground_truth"],
            row["baseline"],
            row["grounded"],
            row["baseline_within_tolerance"],
            row["grounded_within_tolerance"],
        )
        for row in scoreboard["per_question"]
    ]
    assert fresh == saved
    assert outcome["sample_markdown"] == (ROOT / "results" / "sample_results.md").read_text()
    recorded = json.loads((ROOT / "results" / "doc_sentences.json").read_text())["sentences"]
    for sentence in outcome["sentences"]:
        assert sentence in recorded


def test_probe_record_matches_what_this_environment_could_run():
    probe = json.loads((ROOT / "results" / "auto_ontology_probe.json").read_text())
    assert probe["docker"]["compose_ran"] is False
    assert probe["uploaded_data"] is False
    assert probe["kumo_rfm"] == "off"
    assert probe["services_started"] is False
    assert probe["model_keys"]["any_model_key"] is False
    assert probe["extraction"]["loaded"] is True
    manifest = json.loads((ROOT / "results" / "run_manifest.json").read_text())
    assert manifest["what_ran"]["auto_ontology_model"] is False
    assert manifest["what_ran"]["catalog_extraction"] is True
    assert manifest["what_ran"]["auto_ontology_docker_compose"] is False


def test_no_unapproved_social_drafts_are_committed():
    assert not (ROOT / "notes" / "linkedin-draft.md").exists()


def test_repository_does_not_name_a_real_advisory_firm():
    banned = ("delo" + "itte").lower()
    hits = []
    for path in ROOT.rglob("*"):
        if any(part in SKIP_DIRS for part in path.parts):
            continue
        if not path.is_file() or path.suffix in {".pyc", ".duckdb"}:
            continue
        try:
            text = path.read_text(encoding="utf-8")
        except UnicodeDecodeError:
            continue
        if banned in text.lower():
            hits.append(str(path.relative_to(ROOT)))
    assert hits == []


def test_license_credits_upstream_without_relicensing_it():
    text = (ROOT / "LICENSE").read_text(encoding="utf-8")
    assert "MIT License" in text
    assert "Pedro Fernandes Barros" in text
    assert "Apache-2.0" in text
    assert "not included" in text


def test_model_key_report_reads_the_environment_only(monkeypatch: pytest.MonkeyPatch):
    for name in (*MODEL_KEY_NAMES, "KUMO_RFM_API_KEY"):
        monkeypatch.delenv(name, raising=False)
    empty = model_key_report()
    assert empty["any_model_key"] is False
    assert empty["kumo_rfm_api_key_set"] is False
    monkeypatch.setenv("OPENAI_API_KEY", "present")
    seen = model_key_report()
    assert seen["any_model_key"] is True
    assert seen["kumo_rfm_api_key_set"] is False
    monkeypatch.delenv("OPENAI_API_KEY")
    monkeypatch.setenv("KUMO_RFM_API_KEY", "present")
    kumo = model_key_report()
    assert kumo["any_model_key"] is False
    assert kumo["kumo_rfm_api_key_set"] is True
