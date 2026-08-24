"""Time the queries that matter, so an optimisation can be proved rather than claimed.

    cd backend
    python -m scripts.benchmark                    # todo
    python -m scripts.benchmark --label antes      # etiqueta la tanda
    python -m scripts.benchmark --json out.json    # guarda para comparar después
    python -m scripts.benchmark --compare antes.json

Every case runs a warm-up first and then reports the **median** of N runs. The
median, not the mean: one background WAL checkpoint is enough to double a single
timing, and a mean would carry that noise into the conclusion.
"""

from __future__ import annotations

import argparse
import json
import statistics
import sys
import time
from collections.abc import Callable
from pathlib import Path
from typing import Any

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from app.api.deps import get_database, get_repository  # noqa: E402
from app.models.filters import ListingQuery, MapQuery, StatsQuery  # noqa: E402
from app.storage.cache import stats_cache  # noqa: E402
from app.storage.repository import ListingRepository  # noqa: E402

#: Bounding boxes of real map views, from the tightest to the whole country.
VIEWS = {
    "manzana": dict(lat_min=40.415, lat_max=40.425, lon_min=-3.705, lon_max=-3.695),
    "barrio": dict(lat_min=40.40, lat_max=40.45, lon_min=-3.72, lon_max=-3.68),
    "ciudad": dict(lat_min=40.30, lat_max=40.55, lon_min=-3.85, lon_max=-3.55),
    "pais": dict(lat_min=35.0, lat_max=44.0, lon_min=-10.0, lon_max=5.0),
}


def timed(fn: Callable[[], Any], runs: int, *, cold: bool) -> tuple[float, Any]:
    """Median milliseconds over `runs`, plus whatever the last call returned.

    ``cold=True`` invalidates the aggregate cache before every run, so what is
    measured is the SQL. ``cold=False`` leaves it alone and measures what a
    repeated request actually costs the second time.

    Both numbers matter and neither on its own is honest: quoting only the warm
    figure would credit the cache for work the database still has to do the
    first time, and quoting only the cold one would hide the whole point of
    having a cache.
    """
    if cold:
        stats_cache.bump()
    fn()  # warm-up: the first call pays for page cache and query planning
    samples = []
    result = None
    for _ in range(runs):
        if cold:
            stats_cache.bump()
        started = time.perf_counter()
        result = fn()
        samples.append((time.perf_counter() - started) * 1000)
    return statistics.median(samples), result


def cases(repository: ListingRepository) -> dict[str, Callable[[], Any]]:
    """Name -> callable. Each one is a query the app actually makes."""
    work: dict[str, Callable[[], Any]] = {}

    for name, box in VIEWS.items():
        work[f"listings bbox {name}"] = lambda b=box: len(
            repository.list(ListingQuery(**b), limit=24)
        )
        work[f"count bbox {name}"] = lambda b=box: repository.count(ListingQuery(**b))
        work[f"map bbox {name}"] = lambda b=box: repository.map_data(
            MapQuery(**b, zoom=12), zoom=12
        )["total"]

    work["stats sin filtros"] = lambda: repository.overall_stats(StatsQuery())["count"]
    work["stats por zona"] = lambda: len(repository.zone_stats(StatsQuery(), total=149_923))
    work["stats bbox ciudad"] = lambda: repository.overall_stats(
        StatsQuery(**VIEWS["ciudad"])
    )["count"]
    work["stats zona=Madrid"] = lambda: repository.overall_stats(
        StatsQuery(zone="Madrid")
    )["count"]

    work["filtros combinados"] = lambda: repository.count(
        ListingQuery(
            price_min=150_000, price_max=400_000, rooms_min=2,
            operation="venta", zone="Madrid",
        )
    )
    work["filtros + bbox"] = lambda: repository.count(
        ListingQuery(price_min=150_000, price_max=400_000, rooms_min=2, **VIEWS["ciudad"])
    )
    work["chollos ordenados"] = lambda: len(
        repository.list(ListingQuery(bargains_only=True), limit=24, order="desviacion")
    )
    work["facetas"] = lambda: len(repository.facets()["zones"])

    return work


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--runs", type=int, default=5)
    parser.add_argument("--label", default="ahora")
    parser.add_argument("--json", default=None, help="Guardar los resultados")
    parser.add_argument("--compare", default=None, help="Comparar contra una tanda previa")
    parser.add_argument("--only", default=None, help="Filtrar casos por subcadena")
    args = parser.parse_args(argv)

    get_database().init_schema()
    repository = get_repository()

    work = cases(repository)
    if args.only:
        work = {k: v for k, v in work.items() if args.only in k}

    print(f"{args.label}  ·  mediana de {args.runs} ejecuciones\n")
    print(f"{'caso':<26} {'frío ms':>9} {'caché ms':>9}   resultado")
    print("-" * 72)

    results: dict[str, float] = {}
    warm: dict[str, float] = {}
    for name, fn in work.items():
        elapsed, value = timed(fn, args.runs, cold=True)
        cached, _ = timed(fn, args.runs, cold=False)
        results[name] = round(elapsed, 2)
        warm[name] = round(cached, 3)
        print(f"{name:<26} {elapsed:>9.1f} {cached:>9.3f}   {value}")

    print(f"\ncaché: {stats_cache.stats()}")

    if args.json:
        Path(args.json).write_text(
            json.dumps({"label": args.label, "results": results, "warm": warm}, indent=2),
            encoding="utf-8",
        )
        print(f"\nguardado en {args.json}")

    if args.compare:
        before = json.loads(Path(args.compare).read_text(encoding="utf-8"))
        print(f"\n\ncomparación contra «{before['label']}»\n")
        print(f"{'caso':<26} {'antes':>9} {'después':>9} {'cambio':>10}")
        print("-" * 58)
        for name, after in results.items():
            previous = before["results"].get(name)
            if previous is None:
                continue
            if after < previous:
                change = f"{previous / max(after, 0.01):.1f}x más rápido"
            else:
                change = f"{after / max(previous, 0.01):.1f}x más lento"
            print(f"{name:<26} {previous:>9.1f} {after:>9.1f} {change:>10}")

    return 0


if __name__ == "__main__":
    sys.exit(main())
