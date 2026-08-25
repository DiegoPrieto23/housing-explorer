"""Locating listings inside neighbourhood polygons, and filtering by them.

Runs against the real polygons in ``backend/geo/``. The coordinates below are
real places, checked against the actual neighbourhood they fall in, because the
whole point of the feature is that geometry decides -- a fixture polygon I drew
myself would only prove that my ray casting agrees with my rectangle.
"""

from __future__ import annotations

import pytest
from fastapi.testclient import TestClient

from app import geodata
from app.models.listing import Listing, Operation, PropertyType
from app.storage.repository import ListingRepository
from scripts.assign_neighbourhoods import assign

#: (id, city, lat, lon, the neighbourhood it should land in).
#: `None` means "in no polygon", which is a real answer: the dataset covers the
#: metropolitan area and the polygons stop at the municipal boundary.
_PLACES = [
    ("sol", "Madrid", 40.41659, -3.70379, "Sol"),
    ("retiro", "Madrid", 40.41500, -3.68300, "Jerónimos"),
    ("eixample", "Barcelona", 41.38697, 2.170037, "La Dreta de l'Eixample"),
    ("valencia", "Valencia", 39.46977, -0.376619, "Sant Francesc"),
    # Pozuelo de Alarcón: a different municipality, so inside no polygon.
    ("pozuelo", "Madrid", 40.4350, -3.8130, None),
]


@pytest.fixture
def located(client: TestClient, repository: ListingRepository) -> TestClient:
    """Seed one listing per place and run the real assignment over them."""
    repository.upsert_many(
        [
            Listing(
                id=listing_id,
                source="test",
                title=f"Piso en {listing_id}",
                operation=Operation.SALE,
                property_type=PropertyType.FLAT,
                price=300_000,
                size_m2=100.0,
                rooms=3,
                latitude=lat,
                longitude=lon,
                zone=city,
            )
            for listing_id, city, lat, lon, _ in _PLACES
        ]
    )
    assign(repository)
    return client


def _by_id(payload: dict) -> set[str]:
    return {item["id"] for item in payload["items"]}


# ------------------------------------------------------------------- locate ---


def test_known_addresses_land_in_the_right_neighbourhood() -> None:
    index = geodata.neighbourhood_index()

    for listing_id, _, lat, lon, expected in _PLACES:
        found = index.locate(lat, lon)
        actual = None if found is None else found.name
        assert actual == expected, f"{listing_id}: {actual!r} != {expected!r}"


def test_a_listing_with_no_coordinates_is_in_no_neighbourhood() -> None:
    index = geodata.neighbourhood_index()

    assert index.locate(None, None) is None
    assert index.locate(40.41, None) is None


def test_every_neighbourhood_is_reachable_from_its_own_centre() -> None:
    """A polygon nothing can ever land in is a polygon with a broken ring.

    The centroid of a bounding box is not guaranteed to be inside a concave
    shape, so this only asserts that the grid *offers* each neighbourhood as a
    candidate for its own centre -- which is what would break if the index
    stamped the wrong cells.
    """
    index = geodata.neighbourhood_index()

    for item in index.all:
        lat = (item.lat_min + item.lat_max) / 2
        lon = (item.lon_min + item.lon_max) / 2
        cell = index._cells.get((int(lat // 0.01), int(lon // 0.01)), [])
        assert item in cell, f"{item.name} is not indexed at its own centre"


# ------------------------------------------------------------------- assign ---


def test_assignment_writes_both_the_id_and_the_name(
    located: TestClient, repository: ListingRepository
) -> None:
    listing = repository.get("test", "sol")

    assert listing is not None
    assert listing.neighbourhood == "Sol"
    assert listing.neighbourhood_id.startswith("0-EU-ES-28-")


def test_a_listing_outside_every_polygon_keeps_a_null(
    located: TestClient, repository: ListingRepository
) -> None:
    listing = repository.get("test", "pozuelo")

    assert listing is not None
    assert listing.neighbourhood is None
    assert listing.neighbourhood_id is None


def test_coverage_counts_what_was_located(
    located: TestClient, repository: ListingRepository
) -> None:
    coverage = repository.neighbourhood_coverage()

    assert coverage["total"] == len(_PLACES)
    assert coverage["located"] == len(_PLACES) - 1
    assert coverage["neighbourhoods"] == len(_PLACES) - 1


def test_rerunning_without_force_only_touches_the_unassigned(
    located: TestClient, repository: ListingRepository
) -> None:
    """The second run should have nothing to do but the one that is outside.

    That listing is reconsidered every time, and correctly so: NULL is
    indistinguishable from "not looked at yet" in the column, and the cost is
    one point test.
    """
    report = assign(repository)

    assert report["considerados"] == 1
    assert report["localizados"] == 0


def test_force_reassigns_everything(located: TestClient, repository: ListingRepository) -> None:
    report = assign(repository, force=True)

    assert report["considerados"] == len(_PLACES)
    assert report["localizados"] == len(_PLACES) - 1


def test_dry_run_writes_nothing(client: TestClient, repository: ListingRepository) -> None:
    repository.upsert_many(
        [
            Listing(
                id="sol",
                source="test",
                title="Piso en Sol",
                operation=Operation.SALE,
                property_type=PropertyType.FLAT,
                price=300_000,
                latitude=40.41659,
                longitude=-3.70379,
                zone="Madrid",
            )
        ]
    )
    report = assign(repository, dry_run=True)

    assert report["localizados"] == 1
    assert repository.get("test", "sol").neighbourhood is None


# ------------------------------------------------------------------- filter ---


def _sol_id() -> str:
    index = geodata.neighbourhood_index()
    return next(item.location_id for item in index.all if item.name == "Sol")


def test_filtering_by_neighbourhood_returns_only_what_is_inside(located: TestClient) -> None:
    response = located.get("/api/listings", params={"barrio": _sol_id()})

    assert response.status_code == 200
    assert _by_id(response.json()) == {"sol"}
    assert response.json()["total"] == 1


def test_several_neighbourhoods_are_an_or_not_an_and(located: TestClient) -> None:
    """Unlike `extras`, which are all required. A flat is in one neighbourhood."""
    index = geodata.neighbourhood_index()
    sol = _sol_id()
    eixample = next(
        item.location_id for item in index.all if item.name == "La Dreta de l'Eixample"
    )

    response = located.get("/api/listings", params=[("barrio", sol), ("barrio", eixample)])

    assert _by_id(response.json()) == {"sol", "eixample"}


def test_the_neighbourhood_filter_combines_with_the_others(located: TestClient) -> None:
    response = located.get(
        "/api/listings", params={"barrio": _sol_id(), "precio_min": 500_000}
    )

    assert response.json()["total"] == 0


def test_an_unknown_neighbourhood_id_finds_nothing_rather_than_failing(
    located: TestClient,
) -> None:
    response = located.get("/api/listings", params={"barrio": "no-existe"})

    assert response.status_code == 200
    assert response.json()["total"] == 0


def test_a_quoted_neighbourhood_id_cannot_smuggle_sql(located: TestClient) -> None:
    response = located.get("/api/listings", params={"barrio": "x' OR '1'='1"})

    assert response.status_code == 200
    assert response.json()["total"] == 0


def test_asking_for_more_neighbourhoods_than_exist_is_rejected(located: TestClient) -> None:
    response = located.get("/api/listings", params=[("barrio", f"n{i}") for i in range(278)])

    assert response.status_code == 422


# -------------------------------------------------------------------- facets ---


def test_facets_nest_the_neighbourhoods_inside_their_city(located: TestClient) -> None:
    zones = {zone["value"]: zone for zone in located.get("/api/listings/facets").json()["zones"]}

    madrid = zones["Madrid"]
    assert len(madrid["neighbourhoods"]) == 135
    assert {entry["city"] for entry in madrid["neighbourhoods"]} == {"Madrid"}


def test_facets_list_neighbourhoods_alphabetically_ignoring_accents(
    located: TestClient,
) -> None:
    """Sorting by code point would drop every accented name below Z."""
    madrid = next(
        zone
        for zone in located.get("/api/listings/facets").json()["zones"]
        if zone["value"] == "Madrid"
    )
    names = [entry["name"] for entry in madrid["neighbourhoods"]]

    assert names == sorted(names, key=lambda name: _strip(name))
    # The name that motivated it: with a raw sort "Ángel" lands after "Zofío".
    assert any(_strip(name) != name.casefold() for name in names)


def _strip(name: str) -> str:
    import unicodedata

    decomposed = unicodedata.normalize("NFKD", name)
    return "".join(c for c in decomposed if not unicodedata.combining(c)).casefold()


def test_a_neighbourhood_with_no_listings_is_still_offered(located: TestClient) -> None:
    """It exists on the map, so it has to be findable in the picker.

    The seed puts one listing in Sol and none in the other 134 of Madrid, which
    is exactly the case a GROUP BY over the listings would have hidden.
    """
    madrid = next(
        zone
        for zone in located.get("/api/listings/facets").json()["zones"]
        if zone["value"] == "Madrid"
    )
    counts = {entry["name"]: entry["count"] for entry in madrid["neighbourhoods"]}

    assert counts["Sol"] == 1
    assert counts["Chopera"] == 0


def test_facet_bounds_come_from_the_polygon_not_from_the_listings(
    located: TestClient,
) -> None:
    """One listing cannot define a neighbourhood's extent; its outline can."""
    madrid = next(
        zone
        for zone in located.get("/api/listings/facets").json()["zones"]
        if zone["value"] == "Madrid"
    )
    sol = next(entry for entry in madrid["neighbourhoods"] if entry["name"] == "Sol")

    assert sol["lat_min"] < 40.41659 < sol["lat_max"]
    assert sol["lon_min"] < -3.70379 < sol["lon_max"]
    # A box built from the single seeded listing would be a point.
    assert sol["lat_max"] - sol["lat_min"] > 0.001


# --------------------------------------------------------------------- stats ---


def test_stats_group_by_city_when_nothing_narrows_the_search(located: TestClient) -> None:
    payload = located.get("/api/stats").json()

    assert payload["by_zone_is_neighbourhood"] is False
    assert {row["zone"] for row in payload["by_zone"]} == {"Madrid", "Barcelona", "Valencia"}


def test_stats_group_by_neighbourhood_once_a_city_is_chosen(located: TestClient) -> None:
    """Otherwise the table is one row repeating the header."""
    payload = located.get("/api/stats", params={"zona": "Madrid"}).json()

    assert payload["by_zone_is_neighbourhood"] is True
    # The two Madrid listings sit in different neighbourhoods, and the one in
    # Pozuelo is in none, so it is absent rather than lumped into a catch-all.
    assert {row["zone"] for row in payload["by_zone"]} == {"Sol", "Jerónimos"}
    ids = {row["zone"]: row["neighbourhood_id"] for row in payload["by_zone"]}
    assert ids["Sol"] == _sol_id()


def test_stats_group_by_neighbourhood_when_neighbourhoods_are_chosen(
    located: TestClient,
) -> None:
    payload = located.get("/api/stats", params={"barrio": _sol_id()}).json()

    assert payload["by_zone_is_neighbourhood"] is True
    assert payload["overall"]["count"] == 1


def test_a_city_row_carries_no_neighbourhood_id(located: TestClient) -> None:
    payload = located.get("/api/stats").json()

    assert all(row["neighbourhood_id"] is None for row in payload["by_zone"])
