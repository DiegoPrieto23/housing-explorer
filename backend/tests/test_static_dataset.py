"""Ingestion tests for StaticDatasetSource (the idealista18 export).

The fixture mixes valid rows with every kind of row the loader must throw away,
so both halves of the contract are checked: good rows arrive complete, bad rows
are counted rather than silently stored.
"""

from __future__ import annotations

import os
from pathlib import Path

import pytest

from app.ingestion.base import ListingSourceError
from app.ingestion.pipeline import run_ingestion
from app.ingestion.sources.static_dataset import StaticDatasetSource
from app.models.filters import ListingFilters
from app.models.listing import Operation, PropertyType
from app.storage.repository import ListingRepository

HEADER = "ASSETID,PERIOD,PRICE,CONSTRUCTEDAREA,ROOMNUMBER,ISSTUDIO,ISDUPLEX,LONGITUDE,LATITUDE,CITY"

# 4 usable rows, 6 that must be discarded, one reason each.
ROWS = [
    "A001,201803,250000,80,3,0,0,-3.7038,40.4168,Madrid",
    "A002,201806,180000,45,1,1,0,-0.3763,39.4699,Valencia",
    "A003,201809,420000,120,4,0,1,2.1734,41.3851,Barcelona",
    "A004,201812,99000,NA,NA,0,0,-3.7100,40.4200,Madrid",  # nullable fields empty
    "A005,201803,,80,3,0,0,-3.7038,40.4168,Madrid",  # no price
    "A006,201803,0,80,3,0,0,-3.7038,40.4168,Madrid",  # non-positive price
    "A007,201803,250000,80,3,0,0,-3.7038,,Madrid",  # no latitude
    "A008,201803,250000,80,3,0,0,,40.4168,Madrid",  # no longitude
    "A009,201803,250000,80,3,0,0,-0.1276,51.5072,Madrid",  # London, outside Spain
    ",201803,250000,80,3,0,0,-3.7038,40.4168,Madrid",  # no ASSETID
]

EXPECTED_VALID = 4
EXPECTED_DISCARDED = 6


@pytest.fixture
def dataset(tmp_path: Path) -> Path:
    path = tmp_path / "idealista18_sale.csv"
    path.write_text("\n".join([HEADER, *ROWS]) + "\n", encoding="utf-8")
    return path


@pytest.fixture
def source(dataset: Path) -> StaticDatasetSource:
    return StaticDatasetSource(path=dataset)


def test_ingestion_loads_records(source: StaticDatasetSource) -> None:
    listings = source.fetch_listings()
    assert len(listings) == EXPECTED_VALID


def test_mandatory_fields_are_never_empty(source: StaticDatasetSource) -> None:
    """lat, lon and price must be present and usable on every loaded record."""
    listings = source.fetch_listings()
    assert listings, "no listings were loaded"

    for listing in listings:
        assert listing.latitude is not None, f"{listing.id} has no latitude"
        assert listing.longitude is not None, f"{listing.id} has no longitude"
        assert listing.price is not None, f"{listing.id} has no price"
        assert listing.price > 0, f"{listing.id} has a non-positive price"
        assert listing.has_coordinates
        assert listing.id, "empty id"
        assert listing.source == StaticDatasetSource.name


def test_invalid_rows_are_discarded_and_counted(source: StaticDatasetSource) -> None:
    source.fetch_listings()
    stats = source.stats

    assert stats.read == len(ROWS)
    assert stats.emitted == EXPECTED_VALID
    assert stats.discarded == EXPECTED_DISCARDED
    assert stats.read == stats.emitted + stats.discarded

    assert stats.reasons["missing or non-positive price"] == 2
    assert stats.reasons["missing coordinates"] == 2
    assert stats.reasons["coordinates outside Spain"] == 1
    assert stats.reasons["missing ASSETID"] == 1


def test_normalisation_maps_the_dataset_conventions(source: StaticDatasetSource) -> None:
    by_id = {listing.id: listing for listing in source.fetch_listings()}

    # idealista18 is a sale-only dataset.
    assert all(listing.operation is Operation.SALE for listing in by_id.values())

    assert by_id["A001"].property_type is PropertyType.FLAT
    assert by_id["A002"].property_type is PropertyType.STUDIO  # ISSTUDIO=1
    assert by_id["A003"].property_type is PropertyType.DUPLEX  # ISDUPLEX=1

    assert by_id["A001"].size_m2 == 80
    assert by_id["A001"].rooms == 3
    assert by_id["A001"].zone == "Madrid"
    assert by_id["A001"].url is None  # the dataset carries no advert links
    assert "Madrid" in by_id["A001"].title

    # R writes NA for missing values; they must become nulls, not zeros.
    assert by_id["A004"].size_m2 is None
    assert by_id["A004"].rooms is None


def test_keep_all_periods_changes_the_id(dataset: Path) -> None:
    collapsed = {listing.id for listing in StaticDatasetSource(path=dataset).fetch_listings()}
    per_period = {
        listing.id
        for listing in StaticDatasetSource(path=dataset, keep_all_periods=True).fetch_listings()
    }

    assert "A001" in collapsed
    assert "A001-201803" in per_period


def test_ingestion_stores_records_in_sqlite(
    source: StaticDatasetSource, repository: ListingRepository
) -> None:
    result = run_ingestion(source, repository, batch_size=2)

    assert result.source == StaticDatasetSource.name
    assert result.read == len(ROWS)
    assert result.emitted == EXPECTED_VALID
    assert result.discarded == EXPECTED_DISCARDED
    assert result.written == EXPECTED_VALID
    assert result.stored == EXPECTED_VALID
    assert result.total_in_db == EXPECTED_VALID
    assert repository.count() == EXPECTED_VALID

    stored = repository.list(ListingFilters(source=StaticDatasetSource.name))
    assert len(stored) == EXPECTED_VALID
    for listing in stored:
        assert listing.latitude is not None
        assert listing.longitude is not None
        assert listing.price > 0


def test_loading_twice_is_idempotent(dataset: Path, repository: ListingRepository) -> None:
    """Re-running the loader must not duplicate rows: (source, id) is the key."""
    run_ingestion(StaticDatasetSource(path=dataset), repository)
    run_ingestion(StaticDatasetSource(path=dataset), repository)

    assert repository.count() == EXPECTED_VALID


def test_missing_file_raises_a_clear_error(tmp_path: Path) -> None:
    source = StaticDatasetSource(path=tmp_path / "nope.csv")

    assert source.health_check() is False
    with pytest.raises(ListingSourceError, match="not found"):
        source.fetch_listings()


def test_missing_required_column_raises(tmp_path: Path) -> None:
    path = tmp_path / "broken.csv"
    path.write_text("ASSETID,PRICE\nA001,250000\n", encoding="utf-8")

    with pytest.raises(ListingSourceError, match="missing required column"):
        StaticDatasetSource(path=path).fetch_listings()


@pytest.mark.skipif(
    not os.getenv("IDEALISTA18_CSV"),
    reason="set IDEALISTA18_CSV to the exported file to run against real data",
)
def test_real_dataset_sample() -> None:
    """Opt-in check against the actual export; see scripts/export_idealista18.R."""
    import itertools

    source = StaticDatasetSource(path=os.environ["IDEALISTA18_CSV"])
    listings = list(itertools.islice(source.iter_listings(), 500))

    assert len(listings) == 500
    for listing in listings:
        assert listing.latitude is not None
        assert listing.longitude is not None
        assert listing.price > 0
        assert 27.4 <= listing.latitude <= 44.1
        assert -18.6 <= listing.longitude <= 4.6
