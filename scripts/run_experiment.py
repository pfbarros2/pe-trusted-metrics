#!/usr/bin/env python3
"""Generate the ledgers, score both stand-ins, and probe Auto Ontology."""

from __future__ import annotations

import argparse
import json
import os
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

from pe_metrics.db import connect  # noqa: E402
from pe_metrics.definitions import load_definitions, load_questions  # noqa: E402
from pe_metrics.evaluate import evaluate  # noqa: E402
from pe_metrics.generate import (  # noqa: E402
    NORTH_APPROVAL_RATE,
    WEST_APPROVAL_RATE,
    generate_portfolio,
)


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--data-dir", type=Path, default=ROOT / "data" / "duckdb")
    parser.add_argument("--results", type=Path, default=ROOT / "results")
    parser.add_argument(
        "--auto-ontology-src",
        type=Path,
        default=Path(os.environ.get("AUTO_ONTOLOGY_SRC", "/tmp/auto-ontology")),
    )
    parser.add_argument("--skip-probe", action="store_true")
    args = parser.parse_args()

    definitions = load_definitions()
    questions = load_questions(definitions=definitions)
    paths = generate_portfolio(args.data_dir, definitions)
    connection = connect(paths)
    try:
        outcome = evaluate(connection, definitions, questions)
    finally:
        connection.close()

    probe_path = args.results / "auto_ontology_probe.json"
    if args.skip_probe:
        probe = json.loads(probe_path.read_text(encoding="utf-8"))
    else:
        _run_probe(args.auto_ontology_src, paths, probe_path)
        probe = json.loads(probe_path.read_text(encoding="utf-8"))

    sentences = list(outcome["sentences"]) + list(probe["plain_language_sentences"])
    args.results.mkdir(parents=True, exist_ok=True)
    _write(args.results / "ground_truth.json", outcome["ground_truth"])
    _write(args.results / "baseline_answers.json", outcome["baseline"])
    _write(args.results / "grounded_answers.json", outcome["grounded"])
    _write(args.results / "scoreboard.json", outcome["scoreboard"])
    _write(args.results / "worked_example.json", outcome["example"])
    _write(args.results / "baseline_column_choices.json", outcome["column_choices"])
    _write(
        args.results / "scenario.json",
        {
            "fiction": True,
            "companies": 2,
            "month_rows_per_company": 24,
            "first_month": "2024-01-31",
            "last_month": "2025-12-31",
            "north_approval_rate": f"{NORTH_APPROVAL_RATE:.2f}",
            "west_approval_rate": f"{WEST_APPROVAL_RATE:.2f}",
            "reporting_currency": "GBP",
            "harness": "deterministic_stand_in",
            "language_model": False,
        },
    )
    _write(args.results / "doc_sentences.json", {"sentences": sentences})
    (args.results / "sample_results.md").write_text(outcome["sample_markdown"], encoding="utf-8")
    _write(
        args.results / "run_manifest.json",
        {
            "project": "pe-trusted-metrics-ontology",
            "fiction": True,
            "what_ran": {
                "python_ground_truth": True,
                "baseline_stand_in": True,
                "grounded_stand_in": True,
                "auto_ontology_docker_compose": False,
                "auto_ontology_model": False,
                "kumo_rfm": "off",
                "catalog_extraction": bool(probe["extraction"].get("loaded")),
            },
            "auto_ontology_commit": probe.get("commit"),
            "result_files": sorted(
                {*(path.name for path in args.results.iterdir() if path.is_file()), "run_manifest.json"}
            ),
        },
    )
    board = outcome["scoreboard"]
    print(
        f"baseline {board['baseline_accuracy_display']} "
        f"grounded {board['grounded_accuracy_display']}"
    )
    print(f"wrote {args.results}")


def _run_probe(src: Path, paths: dict[str, Path], out: Path) -> None:
    if not (src / "auto_ontology").is_dir():
        destination = ROOT / ".cache" / "auto-ontology"
        if not (destination / "auto_ontology").is_dir():
            destination.parent.mkdir(parents=True, exist_ok=True)
            subprocess.check_call(
                [
                    "git",
                    "clone",
                    "--depth",
                    "1",
                    "https://github.com/NVIDIA/auto-ontology.git",
                    str(destination),
                ]
            )
        src = destination
    out.parent.mkdir(parents=True, exist_ok=True)
    subprocess.check_call(
        [
            sys.executable,
            "-m",
            "pe_metrics.auto_ontology_probe",
            "--src",
            str(src),
            "--out",
            str(out),
            "--north",
            str(paths["north"]),
            "--west",
            str(paths["west"]),
            "--holdco",
            str(paths["holdco"]),
        ],
        cwd=str(ROOT),
        env={**os.environ, "PYTHONPATH": str(ROOT / "src")},
    )


def _write(path: Path, payload) -> None:
    path.write_text(json.dumps(payload, indent=2) + "\n", encoding="utf-8")


if __name__ == "__main__":
    main()
