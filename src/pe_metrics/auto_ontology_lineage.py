"""Parse model SQL with NVIDIA Auto Ontology's catalog parser and record lineage.

Run this module with a Python environment where the upstream package is
installed (for example ``uv sync`` inside a checkout of
https://github.com/NVIDIA/auto-ontology). It does not start Docker, Postgres,
the MCP server or any model client, and it uploads nothing.

What genuinely runs from upstream:
- ``auto_ontology.connectors.duckdb.DuckDBDatabase`` opens each fictional file
  read-only.
- ``auto_ontology.catalog.extract.extract_tabular_db_data`` reads tables and
  columns.
- ``auto_ontology.catalog.parsers.schemas_parser.parse_df`` builds the
  in-memory catalog schemas.
- ``auto_ontology.catalog.sql_parse.parse_query_single`` parses each SQL
  string against that catalog and returns the tables, source columns, and join
  keys it resolved.

One substitution: ``parse_df`` asks the Postgres catalog store for existing
table and column ids. No store is running, so those two lookups are replaced
with functions that return the frames unchanged, which is what the upstream
functions return when the store has no rows for the database.
"""

from __future__ import annotations

import argparse
import json
import logging
import subprocess
from pathlib import Path


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--src", type=Path, required=True)
    parser.add_argument("--in", dest="inp", type=Path, required=True)
    parser.add_argument("--out", type=Path, required=True)
    parser.add_argument("--north", type=Path, required=True)
    parser.add_argument("--west", type=Path, required=True)
    parser.add_argument("--holdco", type=Path, required=True)
    args = parser.parse_args()
    logging.disable(logging.WARNING)

    from auto_ontology.connectors.duckdb import DuckDBDatabase
    from auto_ontology.catalog.extract import extract_tabular_db_data
    import auto_ontology.catalog.parsers.schemas_parser as schemas_parser
    from auto_ontology.catalog.sql_parse import parse_query_single

    schemas_parser.get_table_ids = lambda frame, _name: frame
    schemas_parser.get_column_ids = lambda frame, _name: frame

    catalog = {}
    for path in (args.north, args.west, args.holdco):
        data = extract_tabular_db_data(DuckDBDatabase(str(path)))
        schemas, _ = schemas_parser.parse_df(
            data["tables"], data["columns"], database_name=data["database_name"]
        )
        catalog.update(schemas)
    names = {}
    for schema in catalog.values():
        names.update(schema.id_to_node)

    items = json.loads(args.inp.read_text(encoding="utf-8"))
    out = {}
    for key, sql in items.items():
        try:
            query = parse_query_single(sql=sql, schemas=catalog, dialects=["duckdb"])
        except Exception as exc:  # upstream raises several types
            out[key] = {"parsed": False, "error": f"{type(exc).__name__}: {exc}"[:300]}
            continue
        if query is None:
            out[key] = {"parsed": False, "error": "no catalog table resolved"}
            continue
        tables, columns, joins = set(), set(), set()
        for edge in query.edges:
            head, tail = edge[0], edge[1]
            label = str(tail.label)
            full = names.get(str(tail.id), getattr(tail, "name", None))
            if "Table" in label and "Sql" in str(head.label):
                tables.add(full)
            elif "Column" in label and "Sql" in str(head.label):
                columns.add(full)
            elif "Column" in label and "Column" in str(head.label):
                props = edge[2] if len(edge) > 2 and isinstance(edge[2], dict) else {}
                for ref in props.get("refs", []):
                    joins.add(ref.split("|", 1)[-1])
        out[key] = {
            "parsed": True,
            "tables": sorted(t for t in tables if t),
            "source_columns": sorted(c for c in columns if c),
            "join_conditions": sorted(joins),
            "join_count": query.get_join_count(),
        }

    sha = subprocess.run(
        ["git", "-C", str(args.src), "rev-parse", "HEAD"], capture_output=True, text=True
    ).stdout.strip()
    args.out.write_text(
        json.dumps(
            {
                "auto_ontology_commit": sha,
                "functions_run": [
                    "auto_ontology.connectors.duckdb.DuckDBDatabase",
                    "auto_ontology.catalog.extract.extract_tabular_db_data",
                    "auto_ontology.catalog.parsers.schemas_parser.parse_df",
                    "auto_ontology.catalog.sql_parse.parse_query_single",
                ],
                "substitution": (
                    "schemas_parser.get_table_ids and get_column_ids were replaced with "
                    "pass-through functions because no Postgres catalog store was running; "
                    "upstream returns the same frames when the store is empty."
                ),
                "lineage": out,
            },
            indent=2,
        )
        + "\n",
        encoding="utf-8",
    )


if __name__ == "__main__":
    main()
