"""Make sure the database exists and has something to show. Safe to run on boot.

Unlike ``load_initial_data``, this is designed to run unattended every time a
container starts:

* it never reloads a database that already has rows;
* it never fails the boot -- if no dataset is reachable it logs and returns 0,
  so the API still comes up and the web still renders, just empty.

Order of preference:

1. The database already has listings -> do nothing.
2. The exported ``idealista18`` dataset is in ``DATA_DIR`` -> load it (149.923
   anuncios, about a minute).
3. Neither -> load the 8 demo listings that ship with the repo, so a fresh
   clone shows a working map instead of an empty one.

    python -m scripts.ensure_data
    python -m scripts.ensure_data --demo-only   # skip the big dataset
"""

from __future__ import annotations

import argparse
import logging
import sys
import time
from pathlib import Path

# Allow both `python -m scripts.ensure_data` and `python scripts/ensure_data.py`.
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from app.api.deps import get_database, get_repository  # noqa: E402
from app.ingestion.base import ListingSourceError  # noqa: E402
from app.ingestion.pipeline import run_ingestion  # noqa: E402
from app.ingestion.sources.sample_csv import SampleCsvSource  # noqa: E402
from app.ingestion.sources.static_dataset import StaticDatasetSource  # noqa: E402

logger = logging.getLogger("ensure_data")


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument(
        "--demo-only",
        action="store_true",
        help="Load the demo CSV even if the full dataset is available",
    )
    args = parser.parse_args(argv)

    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s %(levelname)-7s %(message)s",
        datefmt="%H:%M:%S",
    )

    database = get_database()
    database.init_schema()
    repository = get_repository()

    stored = repository.count()
    if stored:
        logger.info("Base de datos lista: %d anuncios en %s", stored, database.path)
        return 0

    logger.info("Base de datos vacía (%s). Buscando algo que cargar…", database.path)

    source = None
    if not args.demo_only:
        candidate = StaticDatasetSource()
        # health_check() is what knows where the dataset lives and whether the
        # export step has been run; asking it beats guessing at filenames here.
        if candidate.health_check():
            logger.info("Encontrado el dataset idealista18. Esto tarda un minuto…")
            source = candidate
        else:
            logger.info(
                "No está el dataset idealista18. Para cargarlo más tarde:\n"
                "    Rscript scripts/export_idealista18.R\n"
                "    python -m scripts.load_initial_data"
            )

    if source is None:
        demo = SampleCsvSource()
        if not demo.health_check():
            logger.warning(
                "Tampoco está el CSV de demostración en DATA_DIR. La API arranca vacía."
            )
            return 0
        logger.info("Cargando los anuncios de demostración.")
        source = demo

    started = time.perf_counter()
    try:
        result = run_ingestion(source, repository)
    except (ListingSourceError, FileNotFoundError) as exc:
        # A missing dataset must not stop the API from booting: an empty web is
        # diagnosable, a container that will not start is not.
        logger.warning("No se pudo cargar ningún dato (%s). La API arranca vacía.", exc)
        return 0

    logger.info("%s (%.1fs)", result.summary(), time.perf_counter() - started)
    # Scoring is a separate step on purpose: it needs the price model and the
    # source dataset, and the API has to come up whether or not either is there.
    logger.info(
        "Para marcar los chollos, estima los precios:\n"
        "    python -m scripts.score_listings"
    )
    return 0


if __name__ == "__main__":
    sys.exit(main())
