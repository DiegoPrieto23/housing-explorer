"""Populate SQLite from an exported static dataset. Run once, not on boot.

    cd backend
    python -m scripts.load_initial_data                  # idealista18, skip if loaded
    python -m scripts.load_initial_data --force          # reload from scratch
    python -m scripts.load_initial_data --path ../data/idealista18_madrid.csv

The application itself never ingests: ``app.main`` only creates the schema, so
starting the API is fast and does not depend on any dataset being present.
"""

from __future__ import annotations

import argparse
import logging
import sys
import time
from pathlib import Path

# Allow both `python -m scripts.load_initial_data` and `python scripts/...py`.
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from app.api.deps import get_database, get_repository  # noqa: E402
from app.ingestion import available_sources, get_source_class  # noqa: E402
from app.ingestion.base import ListingSourceError  # noqa: E402
from app.ingestion.pipeline import DEFAULT_BATCH_SIZE, run_ingestion  # noqa: E402
from app.ingestion.sources.static_dataset import StaticDatasetSource  # noqa: E402

logger = logging.getLogger("load_initial_data")


def build_source(name: str, path: str | None, keep_all_periods: bool):
    """Instantiate the source, passing the options only it understands."""
    source_class = get_source_class(name)
    if issubclass(source_class, StaticDatasetSource):
        return source_class(path=path, keep_all_periods=keep_all_periods)
    if path:
        return source_class(path=path)
    return source_class()


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--source", default="idealista18", choices=available_sources())
    parser.add_argument("--path", default=None, help="Override the dataset file location")
    parser.add_argument(
        "--force",
        action="store_true",
        help="Load even if this source already has rows in the database",
    )
    parser.add_argument(
        "--keep-all-periods",
        action="store_true",
        help="Store one row per quarter instead of collapsing to the latest",
    )
    parser.add_argument("--batch-size", type=int, default=DEFAULT_BATCH_SIZE)
    parser.add_argument("--verbose", "-v", action="store_true")
    args = parser.parse_args(argv)

    logging.basicConfig(
        level=logging.DEBUG if args.verbose else logging.INFO,
        format="%(asctime)s %(levelname)-7s %(message)s",
        datefmt="%H:%M:%S",
    )

    database = get_database()
    database.init_schema()
    repository = get_repository()
    logger.info("Database: %s", database.path)

    already = repository.counts_by_source().get(args.source, 0)
    if already and not args.force:
        logger.info(
            "%s already has %d rows in the database; nothing to do. Use --force to reload.",
            args.source,
            already,
        )
        return 0

    try:
        source = build_source(args.source, args.path, args.keep_all_periods)
    except KeyError as exc:
        logger.error("%s", exc)
        return 2

    if not source.health_check():
        logger.error(
            "Source %r is not ready. For idealista18, export the dataset first:\n"
            "    Rscript scripts/export_idealista18.R",
            args.source,
        )
        return 2

    started = time.perf_counter()
    try:
        result = run_ingestion(source, repository, batch_size=args.batch_size)
    except ListingSourceError as exc:
        logger.error("Ingestion failed: %s", exc)
        return 1
    except KeyboardInterrupt:  # pragma: no cover - interactive
        logger.warning("Interrupted; partially loaded batches are already committed.")
        return 130

    elapsed = time.perf_counter() - started
    logger.info("Done in %.1fs", elapsed)

    collapsed = result.written - result.stored
    if collapsed > 0:
        logger.info(
            "%d rows collapsed onto existing ids (the same dwelling listed in "
            "several quarters). Use --keep-all-periods to keep them apart.",
            collapsed,
        )
    return 0


if __name__ == "__main__":
    sys.exit(main())
