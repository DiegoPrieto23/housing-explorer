"""Contract tests for the query API: filtering, paging, detail and statistics.

The fixtures build a small, hand-checkable set so the expected numbers can be
worked out on paper rather than read back from the implementation.
"""

from __future__ import annotations

import pytest
from fastapi.testclient import TestClient

from app.models.listing import Amenity, Condition, Listing, Operation, PropertyType
from app.storage.repository import ListingRepository

# Madrid ~ (40.4, -3.7), Barcelona ~ (41.4, 2.2). Prices chosen so that the
# medians and averages below are exact.
_SEED = [
    ("m1", "Madrid", Operation.SALE, PropertyType.FLAT, 100_000, 50.0, 1, 40.40, -3.70),
    ("m2", "Madrid", Operation.SALE, PropertyType.FLAT, 200_000, 100.0, 3, 40.41, -3.71),
    ("m3", "Madrid", Operation.SALE, PropertyType.HOUSE, 300_000, 150.0, 4, 40.42, -3.72),
    ("b1", "Barcelona", Operation.SALE, PropertyType.FLAT, 400_000, 80.0, 2, 41.38, 2.17),
    ("b2", "Barcelona", Operation.RENT, PropertyType.STUDIO, 1_200, None, None, 41.39, 2.18),
]


#: Características por anuncio, para los filtros de extras y distancias.
#: (baños, planta, año, estado, km al centro, km al metro, extras)
_FEATURES = {
    "m1": (1, 0, 1970, Condition.NEEDS_WORK, 0.4, 0.1, [Amenity.LIFT]),
    "m2": (2, 3, 2005, Condition.GOOD, 1.2, 0.3, [Amenity.LIFT, Amenity.PARKING]),
    "m3": (3, 5, 2018, Condition.NEW, 4.0, 1.2, [Amenity.LIFT, Amenity.POOL, Amenity.GARDEN]),
    "b1": (2, 1, 1990, Condition.GOOD, 2.0, 0.2, [Amenity.TERRACE]),
    "b2": (1, -1, 1960, Condition.NEEDS_WORK, 0.8, 0.4, []),
}


@pytest.fixture
def seeded(client: TestClient, repository: ListingRepository) -> TestClient:
    repository.upsert_many(
        [
            Listing(
                id=listing_id,
                source="test",
                title=f"Anuncio {listing_id}",
                operation=operation,
                property_type=property_type,
                price=price,
                size_m2=size,
                rooms=rooms,
                latitude=lat,
                longitude=lon,
                zone=zone,
                bathrooms=_FEATURES[listing_id][0],
                floor=_FEATURES[listing_id][1],
                year_built=_FEATURES[listing_id][2],
                condition=_FEATURES[listing_id][3],
                distance_to_center_km=_FEATURES[listing_id][4],
                distance_to_metro_km=_FEATURES[listing_id][5],
                amenities=_FEATURES[listing_id][6],
            )
            for listing_id, zone, operation, property_type, price, size, rooms, lat, lon in _SEED
        ]
    )
    return client


def ids(payload: dict) -> set[str]:
    return {item["id"] for item in payload["items"]}


# -- filtering -------------------------------------------------------------


def test_unfiltered_page_returns_everything(seeded: TestClient) -> None:
    body = seeded.get("/api/listings").json()
    assert body["total"] == 5
    assert len(body["items"]) == 5
    assert body["limit"] == 100
    assert body["offset"] == 0


@pytest.mark.parametrize(
    ("query", "expected"),
    [
        ("precio_min=200000", {"m2", "m3", "b1"}),
        ("precio_max=200000", {"m1", "m2", "b2"}),
        ("precio_min=150000&precio_max=350000", {"m2", "m3"}),
        # A NULL size cannot satisfy "at least 80 m2", so b2 drops out.
        ("m2_min=80", {"m2", "m3", "b1"}),
        ("m2_max=100", {"m1", "m2", "b1"}),
        ("habitaciones=3", {"m2"}),
        ("habitaciones_min=3", {"m2", "m3"}),
        ("tipo_operacion=alquiler", {"b2"}),
        ("tipo_operacion=venta", {"m1", "m2", "m3", "b1"}),
        ("tipo_inmueble=casa", {"m3"}),
        ("zona=Barcelona", {"b1", "b2"}),
        # zona is case-insensitive on purpose: it comes from a URL.
        ("zona=barcelona", {"b1", "b2"}),
        ("zona=Madrid&precio_min=200000", {"m2", "m3"}),
        ("source=test", {"m1", "m2", "m3", "b1", "b2"}),
        ("source=otra", set()),
    ],
)
def test_filters_select_the_expected_listings(
    seeded: TestClient, query: str, expected: set[str]
) -> None:
    body = seeded.get(f"/api/listings?{query}").json()
    assert ids(body) == expected
    assert body["total"] == len(expected)


def test_bounding_box_selects_the_visible_area(seeded: TestClient) -> None:
    madrid = "lat_min=40.0&lat_max=41.0&lon_min=-4.0&lon_max=-3.0"
    body = seeded.get(f"/api/listings?{madrid}").json()
    assert ids(body) == {"m1", "m2", "m3"}


def test_bounding_box_combines_with_the_other_filters(seeded: TestClient) -> None:
    madrid = "lat_min=40.0&lat_max=41.0&lon_min=-4.0&lon_max=-3.0"
    body = seeded.get(f"/api/listings?{madrid}&habitaciones_min=4").json()
    assert ids(body) == {"m3"}


def test_partial_bounding_box_is_rejected(seeded: TestClient) -> None:
    response = seeded.get("/api/listings?lat_min=40.0&lat_max=41.0")
    assert response.status_code == 422
    assert "bounding box" in response.text


@pytest.mark.parametrize(
    "query",
    [
        "precio_min=300000&precio_max=100000",
        "m2_min=200&m2_max=50",
        "habitaciones=2&habitaciones_min=2",
        "precio_min=-1",
        "tipo_operacion=permuta",
        "lat_min=41.0&lat_max=40.0&lon_min=-4.0&lon_max=-3.0",
        # extra="forbid": a typo must fail loudly, not widen the search.
        "precio_minimo=1000",
    ],
)
def test_invalid_queries_are_rejected(seeded: TestClient, query: str) -> None:
    assert seeded.get(f"/api/listings?{query}").status_code == 422


# -- paging ----------------------------------------------------------------


def test_paging_walks_every_listing_exactly_once(seeded: TestClient) -> None:
    seen: list[str] = []
    for offset in (0, 2, 4):
        body = seeded.get(f"/api/listings?limit=2&offset={offset}").json()
        assert body["total"] == 5
        seen.extend(item["id"] for item in body["items"])

    assert sorted(seen) == ["b1", "b2", "m1", "m2", "m3"]


def test_total_ignores_paging_but_respects_filters(seeded: TestClient) -> None:
    body = seeded.get("/api/listings?zona=Madrid&limit=1").json()
    assert body["total"] == 3
    assert len(body["items"]) == 1


# -- detail ----------------------------------------------------------------


def test_detail_by_global_id(seeded: TestClient) -> None:
    body = seeded.get("/api/listings/test:m1").json()
    assert body["id"] == "m1"
    assert body["zone"] == "Madrid"
    assert body["price"] == 100_000


def test_detail_by_bare_id(seeded: TestClient) -> None:
    assert seeded.get("/api/listings/m1").json()["source"] == "test"


def test_detail_by_source_and_id(seeded: TestClient) -> None:
    assert seeded.get("/api/listings/test/m1").json()["id"] == "m1"


def test_detail_of_an_unknown_id_is_404(seeded: TestClient) -> None:
    assert seeded.get("/api/listings/nope").status_code == 404
    assert seeded.get("/api/listings/test:nope").status_code == 404
    assert seeded.get("/api/listings/otra/m1").status_code == 404


def test_an_id_shared_by_two_sources_is_409(
    seeded: TestClient, repository: ListingRepository
) -> None:
    repository.upsert_many(
        [
            Listing(
                id="m1",
                source="otra",
                title="Mismo id, otra fuente",
                operation=Operation.SALE,
                price=1,
            )
        ]
    )
    response = seeded.get("/api/listings/m1")
    assert response.status_code == 409
    assert "fuente:id" in response.json()["detail"]
    # The disambiguated forms still work.
    assert seeded.get("/api/listings/test:m1").json()["source"] == "test"
    assert seeded.get("/api/listings/otra/m1").json()["source"] == "otra"


# -- facets ----------------------------------------------------------------


def test_facets_describe_the_whole_set(seeded: TestClient) -> None:
    body = seeded.get("/api/listings/facets").json()
    assert body["total"] == 5
    assert [(zone["value"], zone["count"]) for zone in body["zones"]] == [
        ("Madrid", 3),
        ("Barcelona", 2),
    ]
    # Each zone carries its own bounding box, so picking a city can fly the map
    # there without the frontend hardcoding any coordinates.
    madrid = body["zones"][0]
    assert (madrid["lat_min"], madrid["lat_max"]) == (40.40, 40.42)
    assert (madrid["lon_min"], madrid["lon_max"]) == (-3.72, -3.70)
    assert {entry["value"] for entry in body["operations"]} == {"venta", "alquiler"}
    assert (body["price_min"], body["price_max"]) == (1_200, 400_000)
    assert (body["size_min"], body["size_max"]) == (50, 150)
    assert (body["rooms_min"], body["rooms_max"]) == (1, 4)


def test_facets_ignore_the_current_selection(seeded: TestClient) -> None:
    """The route takes no filters: the sidebar options must not move under the user."""
    assert seeded.get("/api/listings/facets?zona=Madrid").status_code == 200
    assert seeded.get("/api/listings/facets").json()["total"] == 5


# -- stats -----------------------------------------------------------------


def test_overall_stats(seeded: TestClient) -> None:
    overall = seeded.get("/api/stats?tipo_operacion=venta").json()["overall"]
    assert overall["count"] == 4
    assert overall["min_price"] == 100_000
    assert overall["max_price"] == 400_000
    assert overall["avg_price"] == 250_000
    # Nearest rank: with 4 values the 50th percentile is the 2nd.
    assert overall["median_price"] == 200_000
    # (2000 + 2000 + 2000 + 5000) / 4
    assert overall["avg_price_per_m2"] == pytest.approx(2_750)


def test_stats_accept_the_same_filters_as_listings(seeded: TestClient) -> None:
    overall = seeded.get("/api/stats?zona=Madrid").json()["overall"]
    assert overall["count"] == 3
    assert overall["avg_price"] == pytest.approx(200_000)


def test_stats_respect_the_bounding_box(seeded: TestClient) -> None:
    body = seeded.get("/api/stats?lat_min=41.0&lat_max=42.0&lon_min=2.0&lon_max=3.0").json()
    assert body["overall"]["count"] == 2
    assert [zone["zone"] for zone in body["by_zone"]] == ["Barcelona"]


def test_zone_stats(seeded: TestClient) -> None:
    by_zone = {zone["zone"]: zone for zone in seeded.get("/api/stats").json()["by_zone"]}
    assert set(by_zone) == {"Madrid", "Barcelona"}

    madrid = by_zone["Madrid"]
    assert madrid["count"] == 3
    assert madrid["avg_price"] == pytest.approx(200_000)
    assert madrid["median_price"] == 200_000
    assert madrid["min_price"] == 100_000
    assert madrid["max_price"] == 300_000
    assert madrid["avg_price_per_m2"] == pytest.approx(2_000)


def test_zone_stats_are_ordered_by_volume(seeded: TestClient) -> None:
    zones = seeded.get("/api/stats").json()["by_zone"]
    assert [zone["zone"] for zone in zones] == ["Madrid", "Barcelona"]


def test_price_distribution_covers_every_listing(seeded: TestClient) -> None:
    body = seeded.get("/api/stats?intervalos=4").json()
    buckets = body["price_distribution"]
    assert sum(bucket["count"] for bucket in buckets) == body["overall"]["count"]
    # bins + the open-ended overflow bucket.
    assert len(buckets) == 5
    assert buckets[-1]["upper"] is None
    # Contiguous: each bucket starts where the previous one ended.
    for previous, current in zip(buckets, buckets[1:], strict=False):
        assert previous["upper"] == pytest.approx(current["lower"])


def test_stats_of_an_empty_selection_do_not_blow_up(seeded: TestClient) -> None:
    body = seeded.get("/api/stats?precio_min=9000000").json()
    assert body["overall"]["count"] == 0
    assert body["overall"]["avg_price"] is None
    assert body["by_zone"] == []
    assert body["price_distribution"] == []


def test_openapi_documents_the_spanish_query_parameters(client: TestClient) -> None:
    schema = client.get("/openapi.json").json()
    parameters = schema["paths"]["/api/listings"]["get"]["parameters"]
    names = {parameter["name"] for parameter in parameters}
    assert {"precio_min", "precio_max", "m2_min", "m2_max", "habitaciones"} <= names
    assert {"tipo_operacion", "zona", "lat_min", "lat_max", "lon_min", "lon_max"} <= names


def test_cors_allows_the_local_frontend(client: TestClient) -> None:
    response = client.get("/api/health", headers={"Origin": "http://localhost:5173"})
    assert response.headers["access-control-allow-origin"] == "http://localhost:5173"


# -- map data --------------------------------------------------------------


def test_map_returns_every_point_when_they_fit(seeded: TestClient) -> None:
    body = seeded.get("/api/listings/map").json()
    assert body["mode"] == "points"
    assert body["total"] == 5
    # No cap: five matches means five markers, not a truncated page of them.
    assert len(body["points"]) == 5
    assert body["clusters"] == []
    assert {point["global_id"] for point in body["points"]} == {
        "test:m1", "test:m2", "test:m3", "test:b1", "test:b2",
    }


def test_map_aggregates_when_there_are_too_many(seeded: TestClient) -> None:
    """Below the budget the total is still exact; only the drawing changes."""
    body = seeded.get("/api/listings/map?max_puntos=1&zoom=6").json()
    assert body["mode"] == "clusters"
    assert body["total"] == 5
    assert body["points"] == []
    # At zoom 6 the cell is ~0.94 degrees, so Madrid and Barcelona fall apart.
    assert len(body["clusters"]) == 2
    assert sum(cell["count"] for cell in body["clusters"]) == 5


def test_map_cells_shrink_with_zoom(seeded: TestClient) -> None:
    coarse = seeded.get("/api/listings/map?max_puntos=1&zoom=6").json()
    fine = seeded.get("/api/listings/map?max_puntos=1&zoom=14").json()
    # The three Madrid listings share a cell when it is a degree wide and sit in
    # their own once it is metres wide.
    assert len(fine["clusters"]) > len(coarse["clusters"])
    assert sum(cell["count"] for cell in fine["clusters"]) == 5


def test_map_respects_the_other_filters(seeded: TestClient) -> None:
    body = seeded.get("/api/listings/map?zona=Madrid").json()
    assert body["total"] == 3
    assert {point["global_id"] for point in body["points"]} == {"test:m1", "test:m2", "test:m3"}


def test_map_of_an_empty_selection_is_empty(seeded: TestClient) -> None:
    body = seeded.get("/api/listings/map?precio_min=9000000").json()
    assert body == {"mode": "points", "total": 0, "points": [], "clusters": []}


# -- drawn area ------------------------------------------------------------

#: A box around Madrid only, given as an explicit polygon.
_AROUND_MADRID = "40.35,-3.80;40.35,-3.60;40.50,-3.60;40.50,-3.80"


def test_polygon_selects_only_what_is_inside(seeded: TestClient) -> None:
    body = seeded.get(f"/api/listings?poligono={_AROUND_MADRID}").json()
    assert body["total"] == 3
    assert ids(body) == {"m1", "m2", "m3"}


def test_polygon_count_matches_the_page(seeded: TestClient) -> None:
    """The point of testing inside SQL: the total cannot drift from the rows."""
    page = seeded.get(f"/api/listings?poligono={_AROUND_MADRID}&limit=2").json()
    assert page["total"] == 3
    assert len(page["items"]) == 2


def test_polygon_combines_with_the_other_filters(seeded: TestClient) -> None:
    body = seeded.get(
        f"/api/listings?poligono={_AROUND_MADRID}&tipo_inmueble=piso"
    ).json()
    assert ids(body) == {"m1", "m2"}


def test_polygon_applies_to_stats_and_map_too(seeded: TestClient) -> None:
    stats = seeded.get(f"/api/stats?poligono={_AROUND_MADRID}").json()
    assert stats["overall"]["count"] == 3
    assert stats["overall"]["avg_price"] == 200_000

    map_data = seeded.get(f"/api/listings/map?poligono={_AROUND_MADRID}").json()
    assert map_data["total"] == 3


def test_polygon_excludes_points_outside_its_shape(seeded: TestClient) -> None:
    """A triangle whose bounding box covers Madrid but whose area misses m3."""
    triangle = "40.38,-3.73;40.38,-3.68;40.43,-3.68"
    body = seeded.get(f"/api/listings?poligono={triangle}").json()
    assert ids(body) == {"m1"}


@pytest.mark.parametrize(
    "polygon",
    [
        "40.4,-3.7",                      # one vertex
        "40.4,-3.7;40.5,-3.6",            # two vertices
        "40.4;-3.7;40.5",                 # not lat,lon pairs
        "cuarenta,-3.7;40.5,-3.6;40.6,-3.5",  # not numbers
        "95.0,-3.7;40.5,-3.6;40.6,-3.5",  # off the planet
    ],
)
def test_malformed_polygon_is_rejected(seeded: TestClient, polygon: str) -> None:
    assert seeded.get(f"/api/listings?poligono={polygon}").status_code == 422


def test_zone_box_ignores_a_stray_listing(
    seeded: TestClient, repository: ListingRepository
) -> None:
    """In the real dataset one of 75.804 Madrid listings sits in Almería.

    The box must describe the city, not the 400 km between the two. Being rare
    is part of the property under test -- a point that is a quarter of the data
    is not an outlier, it is a second cluster -- so the city is seeded densely
    enough for the stray to actually be one.
    """
    repository.upsert_many(
        [
            Listing(
                id=f"city{index}",
                source="test",
                title="En la ciudad",
                operation=Operation.SALE,
                price=200_000,
                latitude=40.40 + (index % 20) * 0.002,
                longitude=-3.70 - (index % 20) * 0.002,
                zone="Madrid",
            )
            for index in range(300)
        ]
        + [
            Listing(
                id="stray",
                source="test",
                title="Mal etiquetado",
                operation=Operation.SALE,
                price=191_000,
                latitude=36.75,
                longitude=-2.75,
                zone="Madrid",
            )
        ]
    )
    madrid = next(
        zone
        for zone in seeded.get("/api/listings/facets").json()["zones"]
        if zone["value"] == "Madrid"
    )
    assert madrid["count"] == 304
    # Almería is at 36.75; the box must not reach anywhere near it.
    assert madrid["lat_min"] > 39.5
    assert madrid["lon_max"] < -3.0


# -- price deviation and bargains -----------------------------------------


@pytest.fixture
def scored(seeded: TestClient, repository: ListingRepository) -> TestClient:
    """Hand-set estimates, so the expected deviations are arithmetic, not model output.

    m1 is a bargain (-50%), m2 mildly cheap (-20%, inside the model's own error),
    m3 dead on, b1 expensive. b2 is left unscored on purpose: "no estimate" has
    to behave differently from "estimate of zero deviation".
    """
    repository.update_scores([
        ("test:m1", 200_000.0, -50.0),
        ("test:m2", 250_000.0, -20.0),
        ("test:m3", 300_000.0, 0.0),
        ("test:b1", 320_000.0, 25.0),
    ])
    return seeded


def test_listings_carry_their_estimate(scored: TestClient) -> None:
    body = scored.get("/api/listings/test:m1").json()
    assert body["expected_price"] == 200_000
    assert body["price_deviation"] == -50.0


def test_an_unscored_listing_reports_null(scored: TestClient) -> None:
    body = scored.get("/api/listings/test:b2").json()
    assert body["expected_price"] is None
    assert body["price_deviation"] is None


def test_bargains_only_selects_the_cheap_ones(scored: TestClient) -> None:
    body = scored.get("/api/listings?solo_chollos=true").json()
    # -25% is the threshold: m1 (-50) qualifies, m2 (-20) does not.
    assert ids(body) == {"m1"}
    assert body["total"] == 1


def test_bargains_never_include_unscored_listings(scored: TestClient) -> None:
    """NULL must not slip through a `<=` comparison and be sold as a bargain."""
    assert "b2" not in ids(scored.get("/api/listings?solo_chollos=true").json())
    assert "b2" not in ids(scored.get("/api/listings?desviacion_max=100").json())


@pytest.mark.parametrize(
    ("umbral", "esperado"),
    [
        (-60, set()),
        (-50, {"m1"}),
        (-20, {"m1", "m2"}),
        (0, {"m1", "m2", "m3"}),
        (25, {"m1", "m2", "m3", "b1"}),
    ],
)
def test_deviation_max_is_an_upper_bound(
    scored: TestClient, umbral: int, esperado: set[str]
) -> None:
    assert ids(scored.get(f"/api/listings?desviacion_max={umbral}").json()) == esperado


def test_an_explicit_threshold_beats_the_bargain_flag(scored: TestClient) -> None:
    body = scored.get("/api/listings?solo_chollos=true&desviacion_max=-10").json()
    assert ids(body) == {"m1", "m2"}


def test_scored_only_keeps_everything_with_an_estimate(scored: TestClient) -> None:
    assert ids(scored.get("/api/listings?solo_estimados=true").json()) == {"m1", "m2", "m3", "b1"}


def test_ordering_by_deviation_puts_bargains_first(scored: TestClient) -> None:
    items = scored.get("/api/listings?orden=desviacion").json()["items"]
    assert [item["id"] for item in items] == ["m1", "m2", "m3", "b1", "b2"]
    # And the unscored one lands at the end, not at the front where SQLite
    # would put a NULL by default.
    assert items[-1]["price_deviation"] is None


def test_ordering_by_price(scored: TestClient) -> None:
    subida = [i["price"] for i in scored.get("/api/listings?orden=precio").json()["items"]]
    assert subida == sorted(subida)
    bajada = [i["price"] for i in scored.get("/api/listings?orden=precio_desc").json()["items"]]
    assert bajada == sorted(bajada, reverse=True)


def test_an_unknown_ordering_is_rejected(scored: TestClient) -> None:
    assert scored.get("/api/listings?orden=carisimo").status_code == 422


def test_bargains_combine_with_the_other_filters(scored: TestClient) -> None:
    assert ids(scored.get("/api/listings?solo_chollos=true&zona=Barcelona").json()) == set()
    assert ids(scored.get("/api/listings?solo_chollos=true&zona=Madrid").json()) == {"m1"}


def test_bargains_reach_the_map_and_the_stats(scored: TestClient) -> None:
    mapa = scored.get("/api/listings/map?solo_chollos=true").json()
    assert mapa["total"] == 1
    assert [p["global_id"] for p in mapa["points"]] == ["test:m1"]

    stats = scored.get("/api/stats?solo_chollos=true").json()
    assert stats["overall"]["count"] == 1
    assert stats["overall"]["avg_price"] == 100_000


def test_paging_by_deviation_never_repeats_a_listing(scored: TestClient) -> None:
    """The tie-breaker matters most here: m3 and b1 could otherwise swap pages."""
    vistos: list[str] = []
    for offset in (0, 2, 4):
        page = scored.get(f"/api/listings?orden=desviacion&limit=2&offset={offset}").json()
        vistos.extend(item["id"] for item in page["items"])
    assert sorted(vistos) == ["b1", "b2", "m1", "m2", "m3"]


def test_reingesting_a_listing_drops_its_estimate(
    scored: TestClient, repository: ListingRepository
) -> None:
    """A stale bargain flag is worse than no flag: the price it was based on changed."""
    assert scored.get("/api/listings/test:m1").json()["price_deviation"] == -50.0

    repository.upsert_many([
        Listing(
            id="m1", source="test", title="Anuncio m1", operation=Operation.SALE,
            property_type=PropertyType.FLAT, price=95_000, size_m2=50.0, rooms=1,
            latitude=40.40, longitude=-3.70, zone="Madrid",
        )
    ])

    vuelto = scored.get("/api/listings/test:m1").json()
    assert vuelto["price"] == 95_000
    assert vuelto["price_deviation"] is None
    assert ids(scored.get("/api/listings?solo_chollos=true").json()) == set()


def test_score_bookkeeping(seeded: TestClient, repository: ListingRepository) -> None:
    """The batch job's view of the database: what is scored, what is left."""
    assert repository.global_ids("test", only_unscored=True) == {
        "test:m1", "test:m2", "test:m3", "test:b1", "test:b2",
    }
    assert repository.global_ids("otra") == set()

    written = repository.update_scores([("test:m1", 200_000.0, -50.0)])
    assert written == 1

    # Scored rows drop out of the work queue, so a re-run does not redo them.
    assert "test:m1" not in repository.global_ids("test", only_unscored=True)
    assert "test:m1" in repository.global_ids("test")

    coverage = repository.scoring_coverage()
    assert coverage == {"total": 5, "scored": 1, "bargains": 1}

    # An id that is not in the database is skipped, not an error: the CSV holds
    # rows that were never ingested.
    assert repository.update_scores([("test:fantasma", 1.0, -90.0)]) == 0

    assert repository.clear_scores("test") == 5
    assert repository.scoring_coverage()["scored"] == 0


# -- heat map --------------------------------------------------------------


def test_heat_aggregates_even_when_the_points_would_fit(seeded: TestClient) -> None:
    """The heat layer must not vanish on zooming in, so it never switches to points."""
    normal = seeded.get("/api/listings/map").json()
    assert normal["mode"] == "points"

    heat = seeded.get("/api/listings/map?calor=true&zoom=6").json()
    assert heat["mode"] == "clusters"
    assert heat["total"] == 5
    assert heat["points"] == []
    assert len(heat["clusters"]) == 2


def test_heat_cells_carry_price_per_m2(seeded: TestClient) -> None:
    madrid = next(
        cell
        for cell in seeded.get("/api/listings/map?calor=true&zoom=6").json()["clusters"]
        if cell["latitude"] < 41
    )
    # m1 100.000/50, m2 200.000/100, m3 300.000/150 -> todos a 2.000 €/m².
    assert madrid["avg_price_per_m2"] == 2000
    assert madrid["with_size"] == 3
    assert madrid["count"] == 3


def test_a_cell_with_no_declared_area_has_no_price_per_m2(
    client: TestClient, repository: ListingRepository
) -> None:
    """Null, not zero: unknown and free are not the same colour."""
    repository.upsert_many([
        Listing(
            id="sin", source="test", title="Sin superficie", operation=Operation.SALE,
            price=100_000, latitude=40.4, longitude=-3.7, zone="Madrid",
        )
    ])
    cell = client.get("/api/listings/map?calor=true&zoom=6").json()["clusters"][0]
    assert cell["avg_price_per_m2"] is None
    assert cell["with_size"] == 0


def test_heat_cells_report_their_extent(seeded: TestClient) -> None:
    """A heat cell is drawn as the rectangle it covers, not a blob on the centroid."""
    cell = next(
        c
        for c in seeded.get("/api/listings/map?calor=true&zoom=6").json()["clusters"]
        if c["latitude"] < 41
    )
    assert (cell["lat_min"], cell["lat_max"]) == (40.40, 40.42)
    assert cell["lat_min"] <= cell["latitude"] <= cell["lat_max"]


def test_heat_respects_the_filters(seeded: TestClient) -> None:
    body = seeded.get("/api/listings/map?calor=true&zoom=6&zona=Barcelona").json()
    assert body["total"] == 2
    assert len(body["clusters"]) == 1


# -- favourites: fetching listings by id -----------------------------------


def test_ids_selects_exactly_those_listings(seeded: TestClient) -> None:
    body = seeded.get("/api/listings?ids=test:m1&ids=test:b2").json()
    assert ids(body) == {"m1", "b2"}
    assert body["total"] == 2


def test_ids_ignores_the_ones_that_do_not_exist(seeded: TestClient) -> None:
    """A favourite can outlive the listing it points at; that must not be an error."""
    body = seeded.get("/api/listings?ids=test:m1&ids=test:fantasma").json()
    assert ids(body) == {"m1"}
    assert body["total"] == 1


def test_ids_combines_with_the_other_filters(seeded: TestClient) -> None:
    body = seeded.get("/api/listings?ids=test:m1&ids=test:m3&precio_min=250000").json()
    assert ids(body) == {"m3"}


def test_a_quoted_id_cannot_smuggle_sql(seeded: TestClient) -> None:
    """Favourites come from localStorage, which the user can edit by hand."""
    response = seeded.get("/api/listings?ids=test:m1'); DROP TABLE listings; --")
    assert response.status_code == 200
    assert response.json()["total"] == 0
    # The table is still there.
    assert seeded.get("/api/listings").json()["total"] == 5


# -- distributions for the sidebar charts ----------------------------------


def test_stats_break_down_by_rooms(seeded: TestClient) -> None:
    rooms = seeded.get("/api/stats").json()["by_rooms"]
    by_label = {row["label"]: row for row in rooms}
    # b2 has no rooms and no size, so it is not in any bucket.
    assert set(by_label) == {"1", "2", "3", "4"}
    assert by_label["1"]["avg_price"] == 100_000
    assert by_label["1"]["avg_price_per_m2"] == 2000
    assert sum(row["count"] for row in rooms) == 4


def test_stats_break_down_by_size(seeded: TestClient) -> None:
    sizes = seeded.get("/api/stats").json()["by_size"]
    by_label = {row["label"]: row for row in sizes}
    # 50 -> "40-60", 80 -> "60-80", 100 -> "80-100", 150 -> "130-170".
    assert set(by_label) == {"40–60", "60–80", "80–100", "130–170"}
    assert by_label["40–60"]["count"] == 1
    assert by_label["40–60"]["avg_price"] == 100_000


def test_the_distributions_follow_the_filters(seeded: TestClient) -> None:
    rooms = seeded.get("/api/stats?zona=Barcelona").json()["by_rooms"]
    assert [row["label"] for row in rooms] == ["2"]
    assert rooms[0]["avg_price"] == 400_000


def test_the_distributions_are_ordered_for_a_chart(seeded: TestClient) -> None:
    body = seeded.get("/api/stats").json()
    for key in ("by_rooms", "by_size"):
        buckets = [row["bucket"] for row in body[key]]
        assert buckets == sorted(buckets)


# -- características de la vivienda ----------------------------------------


def test_the_features_survive_a_round_trip(seeded: TestClient) -> None:
    body = seeded.get("/api/listings/test:m2").json()
    assert body["bathrooms"] == 2
    assert body["floor"] == 3
    assert body["year_built"] == 2005
    assert body["condition"] == "buen_estado"
    assert body["distance_to_center_km"] == 1.2
    assert sorted(body["amenities"]) == ["ascensor", "garaje"]


def test_a_listing_with_no_amenities_reports_an_empty_list(seeded: TestClient) -> None:
    """Empty, not null: "we know it has none" and "we do not know" differ."""
    assert seeded.get("/api/listings/test:b2").json()["amenities"] == []


@pytest.mark.parametrize(
    ("query", "expected"),
    [
        ("banos_min=2", {"m2", "m3", "b1"}),
        ("banos_min=3", {"m3"}),
        # planta_min=1 es "sin bajos ni sótanos": m1 está en el bajo y b2 en el -1.
        ("planta_min=1", {"m2", "m3", "b1"}),
        ("planta_max=1", {"m1", "b1", "b2"}),
        ("anio_min=2000", {"m2", "m3"}),
        ("estado=a_reformar", {"m1", "b2"}),
        ("estado=obra_nueva", {"m3"}),
        ("centro_max_km=1", {"m1", "b2"}),
        ("metro_max_km=0.25", {"m1", "b1"}),
    ],
)
def test_the_new_filters_select_what_they_say(
    seeded: TestClient, query: str, expected: set[str]
) -> None:
    assert ids(seeded.get(f"/api/listings?{query}").json()) == expected


def test_one_amenity(seeded: TestClient) -> None:
    assert ids(seeded.get("/api/listings?extras=ascensor").json()) == {"m1", "m2", "m3"}


def test_several_amenities_are_all_required(seeded: TestClient) -> None:
    """Ticking two boxes must narrow the search, not widen it."""
    assert ids(seeded.get("/api/listings?extras=ascensor&extras=garaje").json()) == {"m2"}
    assert ids(seeded.get("/api/listings?extras=ascensor&extras=piscina").json()) == {"m3"}
    # Una combinación que no tiene nadie devuelve vacío, no todo.
    assert ids(seeded.get("/api/listings?extras=piscina&extras=terraza").json()) == set()


def test_amenities_combine_with_the_other_filters(seeded: TestClient) -> None:
    assert ids(
        seeded.get("/api/listings?extras=ascensor&precio_min=250000").json()
    ) == {"m3"}


def test_an_unknown_amenity_is_rejected(seeded: TestClient) -> None:
    assert seeded.get("/api/listings?extras=helipuerto").status_code == 422


def test_a_backwards_floor_range_is_rejected(seeded: TestClient) -> None:
    assert seeded.get("/api/listings?planta_min=5&planta_max=1").status_code == 422


def test_the_new_filters_reach_the_map_and_the_stats(seeded: TestClient) -> None:
    assert seeded.get("/api/listings/map?extras=ascensor").json()["total"] == 3
    assert seeded.get("/api/stats?extras=ascensor").json()["overall"]["count"] == 3


# -- facetas de lo nuevo ----------------------------------------------------


def test_facets_offer_the_amenities_that_exist(seeded: TestClient) -> None:
    body = seeded.get("/api/listings/facets").json()
    counts = {row["value"]: row["count"] for row in body["amenities"]}
    assert counts["ascensor"] == 3
    assert counts["garaje"] == 1
    # Nadie tiene trastero, así que no se ofrece: un filtro que sabemos que
    # devuelve cero no debería estar en el panel.
    assert "trastero" not in counts


def test_facets_offer_the_conditions_and_the_ranges(seeded: TestClient) -> None:
    body = seeded.get("/api/listings/facets").json()
    assert {row["value"] for row in body["conditions"]} == {
        "a_reformar", "buen_estado", "obra_nueva",
    }
    assert (body["bathrooms_min"], body["bathrooms_max"]) == (1, 3)
    assert (body["floor_min"], body["floor_max"]) == (-1, 5)
    assert (body["year_min"], body["year_max"]) == (1960, 2018)
    assert body["center_max_km"] == 4.0


# -- estadísticas nuevas ----------------------------------------------------


def test_stats_break_down_by_distance(seeded: TestClient) -> None:
    rows = seeded.get("/api/stats").json()["by_distance"]
    # b2 no declara superficie, así que no entra en ningún tramo.
    assert sum(row["count"] for row in rows) == 4
    assert [row["bucket"] for row in rows] == sorted(row["bucket"] for row in rows)


def test_amenity_impact_compares_both_sides(seeded: TestClient) -> None:
    rows = {row["amenity"]: row for row in seeded.get("/api/stats").json()["amenities"]}
    lift = rows["ascensor"]
    assert lift["count"] == 3
    # m1/m2/m3 están todos a 2.000 €/m²; b1 (sin ascensor) a 5.000.
    assert lift["with_it"] == 2000
    assert lift["without_it"] == 5000
    assert lift["difference"] == -60.0


def test_an_amenity_nobody_lacks_is_left_out(seeded: TestClient) -> None:
    """Sin los dos lados no hay comparación, y media barra sería mentira."""
    rows = seeded.get("/api/stats?extras=ascensor").json()["amenities"]
    assert "ascensor" not in {row["amenity"] for row in rows}


def test_the_new_stats_follow_the_filters(seeded: TestClient) -> None:
    body = seeded.get("/api/stats?zona=Barcelona").json()
    assert sum(row["count"] for row in body["by_distance"]) == 1
