"""Say which neighbourhood each stored listing is in, by testing its coordinates.

    cd backend
    python -m scripts.assign_neighbourhoods            # rellena lo que falte
    python -m scripts.assign_neighbourhoods --force    # recalcula todo
    python -m scripts.assign_neighbourhoods --dry-run  # calcula y enseña, sin escribir

**Why this exists at all.** The idealista18 `_Sale` tables have no `LOCATIONID`
-- checked column by column in all three `.rda` -- while `<City>_Polygons` has
one per neighbourhood. There is no key to join on, so the only way to connect a
flat to its neighbourhood is geometry: is this point inside that ring?

**Why offline and not per query.** The "draw your area" filter already runs
point-in-polygon inside SQL, and that is affordable because a drawn area is one
polygon over a few thousand candidate rows. Doing it here would be 277 polygons
over 149.923 rows on every request that mentions a neighbourhood. Instead the
answer is computed once and written into two indexed columns, after which
filtering by neighbourhood is an `IN` over an index -- the most selective clause
the API has.

**A NULL is an answer, not a failure.** The dataset covers the metropolitan
area; the polygons stop at the municipal boundary. A flat in Pozuelo is in no
neighbourhood of Madrid, and saying so is correct.

Re-run it after re-exporting the geography, and after ingesting new listings.
Like the price estimates, these columns are derived: an upsert leaves them
alone, so a listing whose coordinates changed keeps a stale neighbourhood until
this runs again.
"""

from __future__ import annotations

import argparse
import logging
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from app import geodata  # noqa: E402
from app.api.deps import get_database, get_repository  # noqa: E402
from app.storage.repository import ListingRepository  # noqa: E402

logger = logging.getLogger("assign_neighbourhoods")

#: Rows per UPDATE batch. Same size as the scoring script, for the same reason:
#: big enough that the per-statement overhead disappears, small enough that an
#: interrupted run leaves finished batches on disk.
BATCH_SIZE = 5_000


def thousands(value: int) -> str:
    return f"{value:,}".replace(",", ".")


def assign(
    repository: ListingRepository,
    *,
    force: bool = False,
    dry_run: bool = False,
    batch_size: int = BATCH_SIZE,
) -> dict[str, int]:
    """Locate every listing and write the result. Returns a small report."""
    index = geodata.neighbourhood_index()

    # Only the columns the test needs. Reading full Listing objects for 150k
    # rows would build 150k Pydantic models to look at two floats each.
    where = "" if force else " WHERE neighbourhood_id IS NULL"
    query = (
        "SELECT global_id, latitude, longitude FROM listings" + where + " ORDER BY global_id"
    )

    considered = 0
    located = 0
    pending: list[tuple[str, str | None, str | None]] = []
    written = 0
    by_city: dict[str, int] = {}

    with repository.database.session() as connection:
        rows = connection.execute(query).fetchall()

    logger.info("Candidatos: %s anuncios", thousands(len(rows)))

    for row in rows:
        considered += 1
        found = index.locate(row["latitude"], row["longitude"])
        if found is None:
            pending.append((row["global_id"], None, None))
        else:
            located += 1
            by_city[found.city] = by_city.get(found.city, 0) + 1
            pending.append((row["global_id"], found.location_id, found.name))

        if len(pending) >= batch_size:
            if not dry_run:
                written += repository.update_neighbourhoods(pending)
            pending.clear()
            logger.debug("%s procesados", thousands(considered))

    if pending and not dry_run:
        written += repository.update_neighbourhoods(pending)

    return {
        "considerados": considered,
        "localizados": located,
        "fuera": considered - located,
        "escritos": written,
        "por_ciudad": by_city,
    }


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument(
        "--force", action="store_true", help="Recalcular también los que ya tienen barrio"
    )
    parser.add_argument("--dry-run", action="store_true", help="Calcular sin escribir")
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
        index = geodata.neighbourhood_index()
    except geodata.GeoDataUnavailable as error:
        logger.error("%s", error)
        return 2

    logger.info("Polígonos cargados: %s barrios", thousands(len(index.all)))

    started = time.perf_counter()
    report = assign(
        repository, force=args.force, dry_run=args.dry_run, batch_size=args.batch_size
    )
    elapsed = time.perf_counter() - started

    logger.info(
        "Hecho en %.1fs · %s localizados de %s · %s fuera de todo polígono",
        elapsed,
        thousands(report["localizados"]),
        thousands(report["considerados"]),
        thousands(report["fuera"]),
    )
    for city, count in sorted(report["por_ciudad"].items(), key=lambda item: -item[1]):
        logger.info("    %-12s %s", city, thousands(count))

    if args.dry_run:
        logger.info("--dry-run: no se ha escrito nada")
        return 0

    coverage = repository.neighbourhood_coverage()
    logger.info(
        "Cobertura total: %s de %s anuncios con barrio (%.1f %%), en %s barrios distintos",
        thousands(coverage["located"]),
        thousands(coverage["total"]),
        100 * coverage["located"] / max(coverage["total"], 1),
        coverage["neighbourhoods"],
    )
    return 0


if __name__ == "__main__":  # pragma: no cover
    raise SystemExit(main())
