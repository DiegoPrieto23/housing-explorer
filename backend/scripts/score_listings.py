"""Estimate what each stored listing should cost, and how far off the asking price is.

    cd backend
    python -m scripts.score_listings                 # solo lo que aún no tiene estimación
    python -m scripts.score_listings --force         # recalcula todo
    python -m scripts.score_listings --dry-run       # calcula y enseña, sin escribir

**Why this reads the source dataset instead of the database.** The price model
was trained on 35 variables; the ``listings`` table keeps 5 of them (surface,
rooms, coordinates, city) because the rest are specific to idealista18 and the
normalised schema deliberately does not carry provider-specific columns.

Feeding the model only what the table stores costs a lot of accuracy -- measured
on the held-out set:

===========================  ========  =========  ==============
inputs                       MAE       MAPE       median error
===========================  ========  =========  ==============
all 35 variables             47.943 €  14,1 %     10,3 %
only what the table stores   78.947 €  23,6 %     18,2 %
===========================  ========  =========  ==============

That difference decides the feature. A "bargain" is a listing far enough below
its estimate to stand out from the model's own error; with a median error of
18 % almost nothing is far enough, and the flag would be noise. So this script
joins the stored rows back onto the dataset they came from and scores them with
everything the source knew.

A source that cannot be joined back to a rich feature set simply goes unscored:
NULL is a first-class answer here, and the API filters those rows out of any
bargain search rather than guessing.
"""

from __future__ import annotations

import argparse
import logging
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from app.api.deps import get_database, get_repository  # noqa: E402
from app.config import get_settings  # noqa: E402
from app.models.listing import BARGAIN_THRESHOLD  # noqa: E402
from app.pricing import PriceModel, PriceModelUnavailable  # noqa: E402
from app.storage.repository import ListingRepository  # noqa: E402

logger = logging.getLogger("score_listings")

#: The source whose raw dataset this script knows how to read.
SOURCE = "idealista18"

#: Tried in order, like the ingestion source does.
DATASET_FILENAMES = ("idealista18_sale.csv", "idealista18_sale.csv.gz")

#: Rows per UPDATE batch.
BATCH_SIZE = 5_000


def find_dataset(data_dir: Path) -> Path | None:
    for name in DATASET_FILENAMES:
        candidate = data_dir / name
        if candidate.is_file():
            return candidate
    return None


def score(
    repository: ListingRepository,
    model: PriceModel,
    dataset: Path,
    *,
    force: bool,
    dry_run: bool,
    batch_size: int = BATCH_SIZE,
) -> dict[str, int]:
    import numpy as np
    import pandas as pd

    logger.info("Leyendo %s", dataset.name)
    frame = pd.read_csv(dataset)

    # Same collapse the loader applies, so the row scored is the row stored.
    frame = frame.sort_values("PERIOD").drop_duplicates("ASSETID", keep="last")
    frame["global_id"] = SOURCE + ":" + frame.ASSETID.astype(str)

    stored = repository.global_ids(SOURCE, only_unscored=not force)
    logger.info(
        "%s anuncios %s en la base de datos",
        f"{len(stored):,}".replace(",", "."),
        "por estimar" if not force else "a reestimar",
    )

    frame = frame[frame.global_id.isin(stored)]
    if frame.empty:
        return {"candidatos": len(stored), "estimados": 0, "escritos": 0, "chollos": 0}

    # A price model cannot say anything useful about a listing with no surface
    # area, and CONSTRUCTEDAREA is the second most important variable there is.
    sin_superficie = frame.CONSTRUCTEDAREA.isna() | (frame.CONSTRUCTEDAREA <= 0)
    if sin_superficie.any():
        logger.info("%d anuncios sin superficie: se quedan sin estimar", int(sin_superficie.sum()))
        frame = frame[~sin_superficie]

    logger.info("Estimando %s precios…", f"{len(frame):,}".replace(",", "."))
    started = time.perf_counter()
    expected = model.estimate_raw(frame)
    logger.info("Estimados en %.1fs", time.perf_counter() - started)

    real = frame.PRICE.to_numpy(dtype=float)
    deviation = 100.0 * (real - expected) / expected

    # A prediction that is not a finite number is a bug, not a result; dropping
    # it loudly beats writing a NaN that later compares false against everything.
    usable = np.isfinite(expected) & np.isfinite(deviation) & (expected > 0)
    if not usable.all():
        logger.warning("%d estimaciones no finitas, descartadas", int((~usable).sum()))

    rows = list(
        zip(
            frame.global_id.to_numpy()[usable],
            expected[usable].astype(float),
            deviation[usable].astype(float),
            strict=True,
        )
    )
    bargains = int((deviation[usable] <= BARGAIN_THRESHOLD).sum())

    if dry_run:
        logger.info("--dry-run: no se escribe nada")
        preview = sorted(rows, key=lambda r: r[2])[:10]
        for key, esperado, desviacion in preview:
            logger.info("  %-34s estimado %10.0f €  %+6.1f %%", key, esperado, desviacion)
        return {
            "candidatos": len(stored),
            "estimados": len(rows),
            "escritos": 0,
            "chollos": bargains,
        }

    written = 0
    for start in range(0, len(rows), batch_size):
        written += repository.update_scores(rows[start : start + batch_size])
        logger.debug("escritos %d/%d", written, len(rows))

    return {
        "candidatos": len(stored),
        "estimados": len(rows),
        "escritos": written,
        "chollos": bargains,
    }


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--force", action="store_true", help="Reestimar también lo ya estimado")
    parser.add_argument("--dry-run", action="store_true", help="Calcular sin escribir")
    parser.add_argument("--path", default=None, help="Ruta alternativa del dataset")
    parser.add_argument("--model", default=None, help="Ruta alternativa del modelo")
    parser.add_argument("--batch-size", type=int, default=BATCH_SIZE)
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

    try:
        model = PriceModel.load(args.model)
    except PriceModelUnavailable as exc:
        logger.error("%s", exc)
        return 2

    dataset = Path(args.path) if args.path else find_dataset(get_settings().data_dir)
    if dataset is None or not dataset.is_file():
        logger.error(
            "No encuentro el dataset de %s. Expórtalo primero:\n"
            "    Rscript scripts/export_idealista18.R",
            SOURCE,
        )
        return 2

    if args.force and not args.dry_run:
        cleared = repository.clear_scores(SOURCE)
        logger.info("Borradas %s estimaciones previas", f"{cleared:,}".replace(",", "."))

    started = time.perf_counter()
    result = score(
        repository, model, dataset,
        force=args.force, dry_run=args.dry_run, batch_size=args.batch_size,
    )

    coverage = repository.scoring_coverage()
    logger.info(
        "Hecho en %.1fs · %s estimados de %s candidatos · %s chollos (<= %.0f %%)",
        time.perf_counter() - started,
        f"{result['estimados']:,}".replace(",", "."),
        f"{result['candidatos']:,}".replace(",", "."),
        f"{result['chollos']:,}".replace(",", "."),
        BARGAIN_THRESHOLD,
    )
    logger.info(
        "Cobertura total: %s de %s anuncios con estimación (%.0f %%)",
        f"{coverage['scored']:,}".replace(",", "."),
        f"{coverage['total']:,}".replace(",", "."),
        100 * coverage["scored"] / max(coverage["total"], 1),
    )
    return 0


if __name__ == "__main__":
    sys.exit(main())
