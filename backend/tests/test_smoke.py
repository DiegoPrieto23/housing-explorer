"""Skeleton-level checks: the app boots, the contract holds, storage round-trips."""

from __future__ import annotations

from app.ingestion import available_sources, get_source_class
from app.ingestion.base import ListingSource
from app.models.listing import Listing, Operation, PropertyType
from app.storage.repository import ListingRepository


def _listing(listing_id: str = "1") -> Listing:
    return Listing(
        id=listing_id,
        source="sample_csv",
        title="Piso en el centro",
        operation=Operation.SALE,
        property_type=PropertyType.FLAT,
        price=250000,
        size_m2=80,
        rooms=3,
        latitude=40.4168,
        longitude=-3.7038,
    )


def test_health(client) -> None:
    response = client.get("/api/health")
    assert response.status_code == 200
    assert response.json()["status"] == "ok"


def test_listings_endpoint_is_empty_by_default(client) -> None:
    response = client.get("/api/listings")
    assert response.status_code == 200
    assert response.json() == {"items": [], "total": 0, "limit": 100, "offset": 0}


def test_repository_round_trip(repository: ListingRepository) -> None:
    repository.upsert_many([_listing()])
    assert repository.count() == 1

    stored = repository.get("sample_csv", "1")
    assert stored is not None
    assert stored.title == "Piso en el centro"
    assert stored.operation is Operation.SALE


def test_upsert_is_idempotent(repository: ListingRepository) -> None:
    repository.upsert_many([_listing(), _listing()])
    repository.upsert_many([_listing()])
    assert repository.count() == 1


def test_every_registered_source_implements_the_interface() -> None:
    assert available_sources(), "no sources registered"
    for name in available_sources():
        source_class = get_source_class(name)
        assert issubclass(source_class, ListingSource)
        assert source_class.name == name
