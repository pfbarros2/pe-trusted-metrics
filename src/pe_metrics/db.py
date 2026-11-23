"""Open the three fictional DuckDB files as one read-only catalog."""

from __future__ import annotations

from datetime import date, datetime
from decimal import Decimal
from pathlib import Path

import duckdb


def connect(paths: dict[str, Path]) -> duckdb.DuckDBPyConnection:
    connection = duckdb.connect()
    for alias in ("north", "west", "holdco"):
        path = Path(paths[alias]).resolve()
        escaped = str(path).replace("'", "''")
        connection.execute(f"ATTACH '{escaped}' AS {alias} (READ_ONLY)")
    return connection


def fetch_dicts(connection: duckdb.DuckDBPyConnection, sql: str) -> list[dict]:
    cursor = connection.execute(sql)
    columns = [item[0] for item in cursor.description]
    rows = []
    for record in cursor.fetchall():
        row = {}
        for name, value in zip(columns, record):
            if isinstance(value, datetime):
                value = value.date()
            row[name] = value
        rows.append(row)
    return rows


def as_date(value: object) -> date:
    if isinstance(value, datetime):
        return value.date()
    if isinstance(value, date):
        return value
    return date.fromisoformat(str(value))


def as_decimal(value: object) -> Decimal:
    if isinstance(value, Decimal):
        return value
    return Decimal(str(value))
