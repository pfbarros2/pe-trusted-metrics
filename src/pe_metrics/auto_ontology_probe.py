"""Run the parts of NVIDIA Auto Ontology that do not need Docker or a model.

The full product is a Docker Compose stack with a model endpoint. This probe
does not start that stack, does not set KumoRFM, and does not upload data.
It does call the upstream DuckDB connector and catalog extractor when those
modules can be loaded from a local checkout of
https://github.com/NVIDIA/auto-ontology (Apache-2.0).
"""

from __future__ import annotations

import argparse
import importlib
import json
import os
import platform
import shutil
import subprocess
import sys
import traceback
import types
from pathlib import Path

MODEL_KEY_NAMES = (
    "DEFAULT_MODELS_API_KEY",
    "NVIDIA_API_KEY",
    "OPENAI_API_KEY",
    "OPENAI_BASE_URL",
    "AZURE_OPENAI_API_KEY",
    "GROQ_API_KEY",
    "TOGETHER_API_KEY",
    "FIREWORKS_API_KEY",
)
KUMO_KEY = "KUMO_RFM_API_KEY"
IMPORT_TARGETS = (
    "auto_ontology.connectors",
    "auto_ontology.semantic.term_extractor",
    "auto_ontology.semantic.deterministic",
)
USER_SCHEMAS = {"ledger", "ops", "ref", "main"}


def model_key_report() -> dict:
    flags = {name: bool(os.environ.get(name)) for name in (*MODEL_KEY_NAMES, KUMO_KEY)}
    return {
        "any_model_key": any(flags[name] for name in MODEL_KEY_NAMES),
        "kumo_rfm_api_key_set": flags[KUMO_KEY],
        "names_checked": list(flags),
        "flags": flags,
    }


def probe(src: Path, databases: dict[str, Path]) -> dict:
    src = src.resolve()
    sha, remote = _git_identity(src)
    keys = model_key_report()
    docker_path = shutil.which("docker")
    imports = {module: _try_import(src, module) for module in IMPORT_TARGETS}
    extraction = _extract_all(src, databases)
    report = {
        "upstream_repo": "https://github.com/NVIDIA/auto-ontology",
        "upstream_license": "Apache-2.0",
        "commit": sha,
        "remote": remote,
        "source_path_used": str(src),
        "docker": {
            "available": docker_path is not None,
            "executable": docker_path,
            "compose_ran": False,
        },
        "uv_available": shutil.which("uv") is not None,
        "model_keys": keys,
        "kumo_rfm": "off",
        "uploaded_data": False,
        "services_started": False,
        "ports_not_opened": [3000, 3001, 3002, 3003, 5432],
        "package_imports": imports,
        "extraction": extraction,
        "python_version": platform.python_version(),
        "duckdb_version": _version("duckdb"),
        "pandas_version": _version("pandas"),
    }
    report["plain_language_sentences"] = _sentences(report)
    return report


def _sentences(report: dict) -> list[str]:
    sentences = [
        (
            f"Auto Ontology source commit {report['commit']} from {report['remote']} "
            "was used read-only."
        )
    ]
    if report["docker"]["available"]:
        sentences.append("Docker was on PATH, and Docker Compose was not started.")
    else:
        sentences.append("Docker was not available, so Docker Compose was not started.")
    if report["model_keys"]["any_model_key"]:
        sentences.append("A model API key was present. This probe did not call a model.")
    else:
        sentences.append(
            "No NVIDIA or OpenAI-compatible model API key was set, so the model was not called."
        )
    sentences.append("KumoRFM was off and no table was uploaded.")
    sentences.append(
        "The web UI on port 3000, the backend on port 3001, the ingestion service on "
        "port 3002, the MCP server on port 3003, and Postgres on port 5432 were not started."
    )
    failed = [name for name, item in report["package_imports"].items() if not item["ok"]]
    if failed:
        joined = ", ".join(failed)
        sentences.append(
            f"Package imports of {joined} were attempted and did not succeed, because those "
            "modules import database engines and a model client that are not installed here."
        )
    extraction = report["extraction"]
    if extraction.get("loaded"):
        sentences.append(
            "DuckDBDatabase and extract_tabular_db_data were loaded from the upstream source "
            "by importing those modules directly, skipping the package inits that pull in every "
            "engine and the embedding client."
        )
        sentences.append(
            f"Python {report['python_version']}, DuckDB {report['duckdb_version']}, and "
            f"pandas {report['pandas_version']} ran that extraction."
        )
    else:
        sentences.append(
            "DuckDBDatabase could not be loaded, so catalog extraction did not run. "
            f"The loader error was {extraction.get('load_error', 'unknown')}."
        )
    for alias, item in extraction.get("databases", {}).items():
        if item.get("extract_ok"):
            sentences.append(
                f"extract_tabular_db_data on {alias} returned {item['n_tables']} tables "
                f"and {item['n_columns']} columns. {item['n_user_tables']} of those tables "
                f"were in the portfolio schemas. get_pks returned {item['n_pks']} rows and "
                f"get_fks returned {item['n_fks']} rows."
            )
        else:
            sentences.append(
                f"extract_tabular_db_data on {alias} did not succeed "
                f"({item.get('extract_error_type', 'error')})."
            )
    return sentences


def _extract_all(src: Path, databases: dict[str, Path]) -> dict:
    try:
        database_cls, extract = _load_extractor(src)
    except Exception as exc:
        return {
            "loaded": False,
            "load_error_type": type(exc).__name__,
            "load_error": _one_line(exc),
            "databases": {},
        }
    results = {"loaded": True, "load_error": None, "databases": {}}
    for alias, path in databases.items():
        results["databases"][alias] = _extract_one(database_cls, extract, Path(path))
    return results


def _extract_one(database_cls, extract, path: Path) -> dict:
    record = {"path_name": path.name}
    try:
        database = database_cls(str(path))
    except Exception as exc:
        record.update(
            {
                "extract_ok": False,
                "extract_error_type": type(exc).__name__,
                "extract_error": _one_line(exc),
            }
        )
        return record
    methods = {}
    frames = {}
    try:
        for name in ("get_tables", "get_columns", "get_views", "get_pks", "get_fks", "get_queries"):
            try:
                frame = getattr(database, name)()
                frames[name] = frame
                methods[name] = {
                    "ok": True,
                    "rows": int(frame.shape[0]),
                    "columns": [str(column) for column in frame.columns],
                }
            except Exception as exc:
                methods[name] = {
                    "ok": False,
                    "error_type": type(exc).__name__,
                    "error": _one_line(exc),
                }
        record["methods"] = methods
        try:
            data = extract(database)
            record["extract_ok"] = True
            record.update(_summarize(data, frames))
        except Exception as exc:
            record["extract_ok"] = False
            record["extract_error_type"] = type(exc).__name__
            record["extract_error"] = _one_line(exc)
            record["n_tables"] = methods.get("get_tables", {}).get("rows")
            record["n_columns"] = methods.get("get_columns", {}).get("rows")
            record["n_user_tables"] = 0
            record["n_pks"] = methods.get("get_pks", {}).get("rows", 0)
            record["n_fks"] = methods.get("get_fks", {}).get("rows", 0)
    finally:
        database.close()
    return record


def _summarize(data: dict, frames: dict) -> dict:
    tables = data["tables"]
    columns = data["columns"]
    user_tables = []
    if tables is not None and not tables.empty and "table_schema" in tables.columns:
        mask = tables["table_schema"].astype(str).isin(USER_SCHEMAS)
        user_tables = tables.loc[mask, ["table_schema", "table_name"]].astype(str).to_dict("records")
    user_columns = []
    if columns is not None and not columns.empty and "table_schema" in columns.columns:
        mask = columns["table_schema"].astype(str).isin(USER_SCHEMAS)
        keep = [name for name in ("table_schema", "table_name", "column_name", "data_type") if name in columns.columns]
        user_columns = columns.loc[mask, keep].astype(str).to_dict("records")
    pks = data.get("pks")
    fks = data.get("fks")
    return {
        "database_name": str(data.get("database_name")),
        "n_tables": int(len(tables)) if tables is not None else 0,
        "n_columns": int(len(columns)) if columns is not None else 0,
        "n_user_tables": len(user_tables),
        "n_pks": int(getattr(pks, "shape", (0,))[0]),
        "n_fks": int(getattr(fks, "shape", (0,))[0]),
        "user_tables": user_tables,
        "user_columns": user_columns,
        "get_pks_rows": frames.get("get_pks").shape[0] if frames.get("get_pks") is not None else None,
        "get_fks_rows": frames.get("get_fks").shape[0] if frames.get("get_fks") is not None else None,
    }


def _load_extractor(src: Path):
    sys.path.insert(0, str(src))
    import auto_ontology  # noqa: F401

    for name, folder in (
        ("auto_ontology.connectors", src / "auto_ontology" / "connectors"),
        ("auto_ontology.utils", src / "auto_ontology" / "utils"),
    ):
        if name not in sys.modules:
            module = types.ModuleType(name)
            module.__path__ = [str(folder)]
            module.__package__ = name
            sys.modules[name] = module
    from auto_ontology.connectors.duckdb import DuckDBDatabase
    from auto_ontology.catalog.extract import extract_tabular_db_data

    return DuckDBDatabase, extract_tabular_db_data


def _try_import(src: Path, module: str) -> dict:
    completed = subprocess.run(
        [
            sys.executable,
            "-c",
            (
                "import sys, importlib; "
                "sys.path.insert(0, sys.argv[1]); "
                "importlib.import_module(sys.argv[2])"
            ),
            str(src),
            module,
        ],
        capture_output=True,
        text=True,
        timeout=120,
        check=False,
    )
    error = ""
    if completed.returncode != 0:
        lines = [line for line in (completed.stderr or completed.stdout).splitlines() if line.strip()]
        error = lines[-1][:400] if lines else "import failed"
    return {"ok": completed.returncode == 0, "returncode": completed.returncode, "error": error}


def _git_identity(src: Path) -> tuple[str, str]:
    sha = subprocess.check_output(
        ["git", "-C", str(src), "rev-parse", "HEAD"], text=True
    ).strip()
    remote = subprocess.check_output(
        ["git", "-C", str(src), "remote", "get-url", "origin"], text=True
    ).strip()
    return sha, remote


def _version(distribution: str) -> str:
    return importlib.import_module(distribution).__version__


def _one_line(exc: BaseException) -> str:
    text = "".join(traceback.format_exception_only(type(exc), exc)).strip()
    return text.splitlines()[-1][:500]


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--src", type=Path, required=True)
    parser.add_argument("--out", type=Path, required=True)
    parser.add_argument("--north", type=Path, required=True)
    parser.add_argument("--west", type=Path, required=True)
    parser.add_argument("--holdco", type=Path, required=True)
    args = parser.parse_args()
    report = probe(
        args.src,
        {"north": args.north, "west": args.west, "holdco": args.holdco},
    )
    args.out.parent.mkdir(parents=True, exist_ok=True)
    args.out.write_text(json.dumps(report, indent=2) + "\n", encoding="utf-8")
    print(f"wrote {args.out}")


if __name__ == "__main__":
    main()
