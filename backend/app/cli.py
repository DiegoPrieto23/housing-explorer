"""Command line entrypoint for ingestion and database maintenance.

Usage:
    python -m app.cli init-db
    python -m app.cli sources
    python -m app.cli ingest --source sample_csv

For the large static dataset prefer the dedicated loader, which is
idempotent and reports discards:

    python -m scripts.load_initial_data
"""

from __future__ import annotations

import argparse
import logging
import sys

from app.api.deps import get_database, get_repository
from app.ingestion import available_sources
from app.ingestion.pipeline import run_ingestion

logging.basicConfig(level=logging.INFO, format="%(levelname)s: %(message)s")


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="housing-explorer")
    subparsers = parser.add_subparsers(dest="command", required=True)

    subparsers.add_parser("init-db", help="Create the SQLite schema")
    subparsers.add_parser("sources", help="List registered ingestion sources")

    ingest = subparsers.add_parser("ingest", help="Fetch and store listings")
    ingest.add_argument("--source", required=True, choices=available_sources())

    args = parser.parse_args(argv)

    if args.command == "init-db":
        get_database().init_schema()
        print("Schema ready at", get_database().path)
        return 0

    if args.command == "sources":
        for name in available_sources():
            print(name)
        return 0

    if args.command == "ingest":
        get_database().init_schema()
        result = run_ingestion(args.source, get_repository())
        print(result.summary())
        return 0

    return 1


if __name__ == "__main__":
    sys.exit(main())
