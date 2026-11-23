#!/usr/bin/env python3
"""Write the fictional DuckDB files under data/duckdb."""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

from pe_metrics.generate import generate_portfolio  # noqa: E402


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--out",
        type=Path,
        default=ROOT / "data" / "duckdb",
        help="directory for the three DuckDB files",
    )
    args = parser.parse_args()
    for alias, path in generate_portfolio(args.out).items():
        print(f"{alias}: {path}")


if __name__ == "__main__":
    main()
