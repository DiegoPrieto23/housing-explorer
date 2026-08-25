"""The neighbourhood polygons and points of interest, and the endpoints for them.

These run against the real files in ``backend/geo/`` rather than fixtures. That
is the point: the failure worth catching is "the export wrote something the map
cannot draw", and a fixture I write by hand cannot catch that. Two tests use
temporary files instead, for the cases the real data is not allowed to have.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import pytest
from fastapi.testclient import TestClient

from app import geodata


@pytest.fixture(autouse=True)
def _clear_caches() -> None:
    """The loader caches per process; a test that repoints GEO_DIR must not inherit."""
    geodata._collection.cache_clear()
    geodata._rendered.cache_clear()


def _features(payload: dict[str, Any]) -> list[dict[str, Any]]:
    return payload["features"]


# --------------------------------------------------------------------- files ---


def test_neighbourhoods_cover_the_three_cities() -> None:
    features = _features(json.loads(geodata.neighbourhoods()))

    by_city: dict[str, int] = {}
    for feature in features:
        by_city[feature["properties"]["city"]] = by_city.get(feature["properties"]["city"], 0) + 1

    assert by_city == {"Madrid": 135, "Barcelona": 69, "Valencia": 73}
    assert len(features) == 277


def test_every_neighbourhood_has_an_id_and_a_name() -> None:
    for feature in _features(json.loads(geodata.neighbourhoods())):
        properties = feature["properties"]
        assert properties["location_id"].startswith("0-EU-ES-")
        assert properties["name"].strip()


def test_accented_names_survived_the_export() -> None:
    """The bug this is here for shipped once: "Timón" came out as "TimÃ³n".

    The .rda holds UTF-8 bytes with no encoding declared, and R on Windows
    re-encoded them from its Latin-1 code page. Doubly-encoded text round-trips
    through JSON without complaint and only looks wrong to a reader, so nothing
    but an assertion on a known name catches it.
    """
    names = {f["properties"]["name"] for f in _features(json.loads(geodata.neighbourhoods()))}

    assert "Timón" in names
    # The signature of the double encoding: every accent gains a Ã or Â.
    assert not [name for name in names if "Ã" in name or "Â" in name]


def test_polygons_are_closed_rings_in_the_right_hemisphere() -> None:
    for feature in _features(json.loads(geodata.neighbourhoods())):
        assert feature["geometry"]["type"] == "MultiPolygon"
        for polygon in feature["geometry"]["coordinates"]:
            for ring in polygon:
                # GeoJSON requires the first and last position to be identical,
                # and at least four of them. Leaflet is forgiving about this;
                # anything that computes an area is not.
                assert len(ring) >= 4
                assert ring[0] == ring[-1]
                for lon, lat in ring:
                    # Peninsular Spain, generously. Catches a lon/lat swap,
                    # which is the classic GeoJSON mistake and draws a map of
                    # the Indian Ocean.
                    assert -10 < lon < 5
                    assert 35 < lat < 45


def test_points_of_interest_have_the_three_kinds() -> None:
    features = _features(json.loads(geodata.points_of_interest()))

    kinds: dict[str, int] = {}
    for feature in features:
        kinds[feature["properties"]["kind"]] = kinds.get(feature["properties"]["kind"], 0) + 1

    # One centre and one main street per city; the rest are metro stations.
    assert kinds["centro"] == 3
    assert kinds["calle"] == 3
    assert kinds["metro"] > 700


def test_main_streets_are_lines_and_the_rest_are_points() -> None:
    streets = []
    for feature in _features(json.loads(geodata.points_of_interest())):
        kind = feature["properties"]["kind"]
        geometry = feature["geometry"]
        if kind == "calle":
            assert geometry["type"] == "LineString"
            # A street of two points is a hint that the ordering step collapsed.
            assert len(geometry["coordinates"]) > 10
            streets.append(feature["properties"]["name"])
        else:
            assert geometry["type"] == "Point"

    assert set(streets) == {
        "Paseo de la Castellana",
        "Avinguda Diagonal",
        "Avinguda de Blasco Ibáñez",
    }


def test_metro_stations_are_near_their_city() -> None:
    """Valencia shipped a station at lon +0.40 -- a flipped sign, 67 km out to sea.

    The export drops anything past 40 km. If a future re-export stops doing so,
    the map grows a marker in the Mediterranean and this says why.
    """
    payload = json.loads(geodata.points_of_interest())
    centres = {
        f["properties"]["city"]: f["geometry"]["coordinates"]
        for f in _features(payload)
        if f["properties"]["kind"] == "centro"
    }

    for feature in _features(payload):
        if feature["properties"]["kind"] != "metro":
            continue
        lon, lat = feature["geometry"]["coordinates"]
        centre_lon, centre_lat = centres[feature["properties"]["city"]]
        km = (((lon - centre_lon) * 85) ** 2 + ((lat - centre_lat) * 111) ** 2) ** 0.5
        assert km < 40, f"metro station {km:.0f} km from {feature['properties']['city']}"


def test_filtering_by_city_is_case_insensitive() -> None:
    madrid = _features(json.loads(geodata.neighbourhoods("madrid")))

    assert len(madrid) == 135
    assert {f["properties"]["city"] for f in madrid} == {"Madrid"}


def test_an_unknown_city_is_an_empty_collection_not_an_error() -> None:
    payload = json.loads(geodata.neighbourhoods("Bilbao"))

    assert payload["type"] == "FeatureCollection"
    assert payload["features"] == []


# ------------------------------------------------------------------ failures ---


def test_a_truncated_file_fails_loudly(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """Half a file must not become half a map."""
    broken = tmp_path / geodata.NEIGHBOURHOODS_FILE
    broken.write_text('{"type":"FeatureCollection","features":[{"type":"Fea', encoding="utf-8")
    monkeypatch.setattr(geodata, "GEO_DIR", tmp_path)

    with pytest.raises(geodata.GeoDataUnavailable, match="not valid JSON"):
        geodata.neighbourhoods()


def test_a_missing_file_names_the_command_that_makes_it(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(geodata, "GEO_DIR", tmp_path)

    with pytest.raises(geodata.GeoDataUnavailable, match="export_idealista18.R"):
        geodata.neighbourhoods()


def test_an_unknown_poi_kind_is_rejected(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A fourth kind would be invisible on the map; better to refuse to serve it."""
    (tmp_path / geodata.POINTS_OF_INTEREST_FILE).write_text(
        json.dumps(
            {
                "type": "FeatureCollection",
                "features": [
                    {
                        "type": "Feature",
                        "properties": {"kind": "aeropuerto", "city": "Madrid"},
                        "geometry": {"type": "Point", "coordinates": [-3.5, 40.5]},
                    }
                ],
            }
        ),
        encoding="utf-8",
    )
    monkeypatch.setattr(geodata, "GEO_DIR", tmp_path)

    with pytest.raises(geodata.GeoDataUnavailable, match="aeropuerto"):
        geodata.points_of_interest()


# ----------------------------------------------------------------- endpoints ---


def test_neighbourhoods_endpoint_returns_geojson(client: TestClient) -> None:
    response = client.get("/api/neighbourhoods")

    assert response.status_code == 200
    assert response.headers["content-type"].startswith("application/geo+json")
    assert "max-age" in response.headers["cache-control"]
    assert response.json()["type"] == "FeatureCollection"


def test_points_of_interest_endpoint_returns_geojson(client: TestClient) -> None:
    response = client.get("/api/points-of-interest")

    assert response.status_code == 200
    assert len(response.json()["features"]) > 700


def test_endpoint_filters_by_city(client: TestClient) -> None:
    response = client.get("/api/points-of-interest", params={"ciudad": "Valencia"})

    cities = {f["properties"]["city"] for f in response.json()["features"]}
    assert cities == {"Valencia"}


def test_the_american_spelling_is_not_a_404(client: TestClient) -> None:
    """`/neighborhoods` is an alias. The code is British; the user need not be."""
    assert client.get("/api/neighborhoods").status_code == 200


def test_missing_geography_is_a_503_that_says_what_to_run(
    client: TestClient, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(geodata, "GEO_DIR", tmp_path)

    response = client.get("/api/neighbourhoods")

    assert response.status_code == 503
    assert "export_idealista18.R" in response.json()["detail"]


def test_the_response_is_compressed_when_asked(client: TestClient) -> None:
    """279 kB of borders down to 67. The middleware is why the map loads fast."""
    plain = client.get("/api/neighbourhoods", headers={"Accept-Encoding": "identity"})
    compressed = client.get("/api/neighbourhoods", headers={"Accept-Encoding": "gzip"})

    assert compressed.headers.get("content-encoding") == "gzip"
    assert int(compressed.headers["content-length"]) < len(plain.content) / 3
