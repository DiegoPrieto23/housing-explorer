"""The aggregate cache has to be fast *and* never serve a stale answer."""

from __future__ import annotations

import time

import pytest
from fastapi.testclient import TestClient

from app.models.listing import Listing, Operation, PropertyType
from app.storage.cache import VersionedCache, stats_cache
from app.storage.repository import ListingRepository


def listing(listing_id: str, price: float) -> Listing:
    return Listing(
        id=listing_id, source="test", title=f"Anuncio {listing_id}",
        operation=Operation.SALE, property_type=PropertyType.FLAT,
        price=price, size_m2=80.0, rooms=2, latitude=40.4, longitude=-3.7, zone="Madrid",
    )


# -- the cache on its own --------------------------------------------------


def test_a_repeated_question_is_only_computed_once() -> None:
    cache = VersionedCache()
    calls = []

    def compute() -> str:
        calls.append(1)
        return "valor"

    assert cache.get_or_compute("k", compute) == "valor"
    assert cache.get_or_compute("k", compute) == "valor"
    assert len(calls) == 1
    assert cache.stats()["hits"] == 1


def test_bumping_the_version_makes_every_entry_unreachable() -> None:
    cache = VersionedCache()
    cache.get_or_compute("k", lambda: "viejo")
    cache.bump()
    assert cache.get_or_compute("k", lambda: "nuevo") == "nuevo"


def test_different_keys_do_not_collide() -> None:
    cache = VersionedCache()
    assert cache.get_or_compute("a", lambda: 1) == 1
    assert cache.get_or_compute("b", lambda: 2) == 2


def test_the_lru_evicts_the_least_recently_used() -> None:
    cache = VersionedCache(maxsize=2)
    cache.get_or_compute("a", lambda: 1)
    cache.get_or_compute("b", lambda: 2)
    cache.get_or_compute("a", lambda: 99)  # touching 'a' makes 'b' the oldest
    cache.get_or_compute("c", lambda: 3)

    assert cache.stats()["entries"] == 2
    # 'a' survived and still holds its original value; 'b' was evicted.
    assert cache.get_or_compute("a", lambda: 99) == 1
    assert cache.get_or_compute("b", lambda: 99) == 99


def test_a_result_computed_across_a_bump_is_discarded() -> None:
    """Otherwise a slow query could write an answer about data that has changed."""
    cache = VersionedCache()

    def compute_and_change() -> str:
        cache.bump()
        return "calculado sobre datos viejos"

    cache.get_or_compute("k", compute_and_change)
    assert cache.stats()["entries"] == 0


# -- the cache inside the repository ---------------------------------------


@pytest.fixture
def stocked(repository: ListingRepository) -> ListingRepository:
    repository.upsert_many([listing("a", 100_000), listing("b", 200_000)])
    return repository


def test_writing_invalidates_the_aggregates(stocked: ListingRepository) -> None:
    assert stocked.count() == 2
    assert stocked.overall_stats()["avg_price"] == 150_000

    stocked.upsert_many([listing("c", 600_000)])

    assert stocked.count() == 3
    assert stocked.overall_stats()["avg_price"] == 300_000


def test_scoring_invalidates_the_aggregates(stocked: ListingRepository) -> None:
    assert stocked.count() == 2
    stocked.update_scores([("test:a", 200_000.0, -50.0)])
    # The bargain filter reads a column the previous count knew nothing about.
    from app.models.filters import ListingQuery

    assert stocked.count(ListingQuery(bargains_only=True)) == 1
    stocked.clear_scores()
    assert stocked.count(ListingQuery(bargains_only=True)) == 0


def test_the_cache_actually_gets_used(stocked: ListingRepository) -> None:
    before = stats_cache.stats()["hits"]
    for _ in range(5):
        stocked.overall_stats()
    assert stats_cache.stats()["hits"] >= before + 4


def test_different_filters_get_different_answers(stocked: ListingRepository) -> None:
    from app.models.filters import ListingQuery

    assert stocked.count(ListingQuery(price_min=150_000)) == 1
    assert stocked.count(ListingQuery(price_max=150_000)) == 1
    assert stocked.count() == 2


def test_the_api_reports_the_same_totals_twice(client: TestClient,
                                               repository: ListingRepository) -> None:
    repository.upsert_many([listing("a", 100_000), listing("b", 200_000)])
    first = client.get("/api/stats").json()
    second = client.get("/api/stats").json()
    assert first == second


# -- single-flight ---------------------------------------------------------


def test_concurrent_misses_compute_once() -> None:
    """Veinte hilos preguntando lo mismo tienen que calcularlo una sola vez.

    Es lo que impidió que el servidor se quedara sin CPU: una pestaña en bucle
    de recarga pedía `/stats` sin parar, y sin esto cada petición recalculaba el
    mismo agregado sobre 150k filas a la vez.
    """
    import threading

    cache = VersionedCache()
    calls = []
    empezar = threading.Barrier(20)

    def compute() -> str:
        calls.append(1)
        time.sleep(0.2)  # lo bastante para que los demás lleguen mientras
        return "valor"

    def worker(salida: list[str]) -> None:
        empezar.wait()
        salida.append(cache.get_or_compute("k", compute))

    resultados: list[str] = []
    hilos = [threading.Thread(target=worker, args=(resultados,)) for _ in range(20)]
    for hilo in hilos:
        hilo.start()
    for hilo in hilos:
        hilo.join(timeout=10)

    assert len(resultados) == 20
    assert set(resultados) == {"valor"}
    assert len(calls) == 1
    assert cache.stats()["in_flight"] == 0


def test_a_failed_computation_does_not_wedge_the_key() -> None:
    """Si el primero revienta, el siguiente tiene que poder intentarlo."""
    cache = VersionedCache()

    with pytest.raises(RuntimeError):
        cache.get_or_compute("k", lambda: (_ for _ in ()).throw(RuntimeError("boom")))

    assert cache.stats()["in_flight"] == 0
    assert cache.get_or_compute("k", lambda: "bien") == "bien"


def test_waiters_are_released_when_the_data_changes() -> None:
    """Un bump durante el cálculo no debe dejar a nadie esperando el tiempo entero."""
    import threading

    cache = VersionedCache(wait_timeout=30.0)
    empezado = threading.Event()

    def lento() -> str:
        empezado.set()
        time.sleep(0.4)
        return "viejo"

    primero = threading.Thread(target=lambda: cache.get_or_compute("k", lento))
    primero.start()
    empezado.wait(timeout=5)

    cache.bump()  # los datos cambian mientras el primero calcula

    empezado_segundo = time.perf_counter()
    assert cache.get_or_compute("k", lambda: "nuevo") == "nuevo"
    # Sin la liberación en bump() esto habría esperado hasta el timeout.
    assert time.perf_counter() - empezado_segundo < 5
    primero.join(timeout=5)
