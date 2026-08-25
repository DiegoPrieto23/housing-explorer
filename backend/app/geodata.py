"""The fixed geography the map draws on top of the listings.

Two GeoJSON files, written by ``scripts/export_idealista18.R`` and versioned
with the repo: the neighbourhood polygons (``LOCATIONID`` / ``LOCATIONNAME``)
and the points of interest (city centre, metro stations, main street). They
live in ``backend/geo/`` next to the price model, for the same reason: they are
small, they never change, and the image should be self-sufficient.

Not in SQLite, and that is a deliberate departure from where everything else
lives. A table would buy indexing and filtering, and there is nothing here to
index -- 277 polygons and 807 points, always read whole, never joined against a
listing, never written. What it would cost is a schema migration, a loader, and
a second place where the ingestion can half-finish. A file the endpoint reads
once is the honest shape of the data.

Distinct from :mod:`app.geometry`, which is the point-in-polygon arithmetic
behind the "draw your own area" filter. This module holds no maths at all.
"""

from __future__ import annotations

import json
from dataclasses import dataclass
from functools import lru_cache
from pathlib import Path
from typing import Any

BACKEND_DIR = Path(__file__).resolve().parent.parent

#: Where the R export leaves them, and where the Dockerfile copies them from.
GEO_DIR = BACKEND_DIR / "geo"

NEIGHBOURHOODS_FILE = "neighbourhoods.geojson"
POINTS_OF_INTEREST_FILE = "points_of_interest.geojson"

#: The three kinds of point of interest the dataset distinguishes. The frontend
#: draws each one differently, so an unknown fourth kind appearing in the file
#: would be silently invisible -- hence the check at load time.
POI_KINDS = frozenset({"centro", "metro", "calle"})


class GeoDataUnavailable(RuntimeError):
    """A GeoJSON file is missing or is not a usable FeatureCollection.

    Raised at load time rather than per request: a truncated file should fail
    once, loudly, with the path in the message, instead of producing an empty
    map layer nobody can explain.
    """


def _validate(payload: Any, path: Path, kinds: frozenset[str] | None) -> dict[str, Any]:
    """Check the shape enough that a bad file cannot reach the browser.

    Not a full GeoJSON validator: the file is written by a script in this repo,
    so the failure being guarded against is "the export broke or the file got
    truncated", not "someone handed us arbitrary GeoJSON".
    """
    if not isinstance(payload, dict) or payload.get("type") != "FeatureCollection":
        raise GeoDataUnavailable(f"{path} is not a GeoJSON FeatureCollection")

    features = payload.get("features")
    if not isinstance(features, list) or not features:
        raise GeoDataUnavailable(f"{path} has no features")

    for feature in features:
        if not isinstance(feature, dict) or feature.get("type") != "Feature":
            raise GeoDataUnavailable(f"{path} contains something that is not a Feature")
        if not isinstance(feature.get("geometry"), dict):
            raise GeoDataUnavailable(f"{path} contains a Feature without geometry")
        properties = feature.get("properties")
        if not isinstance(properties, dict) or not properties.get("city"):
            raise GeoDataUnavailable(f"{path} contains a Feature without a city")

    if kinds is not None:
        present = {feature["properties"].get("kind") for feature in features}
        unknown = present - kinds
        if unknown:
            raise GeoDataUnavailable(
                f"{path} has point-of-interest kind(s) the frontend cannot draw: "
                f"{', '.join(sorted(str(k) for k in unknown))}"
            )

    return payload


@lru_cache(maxsize=4)
def _collection(filename: str) -> dict[str, Any]:
    """Read and validate one file, once per process."""
    path = GEO_DIR / filename
    try:
        payload = json.loads(path.read_bytes())
    except FileNotFoundError as error:
        raise GeoDataUnavailable(
            f"{path} not found. Run: Rscript scripts/export_idealista18.R"
        ) from error
    except json.JSONDecodeError as error:
        raise GeoDataUnavailable(f"{path} is not valid JSON: {error}") from error

    kinds = POI_KINDS if filename == POINTS_OF_INTEREST_FILE else None
    return _validate(payload, path, kinds)


@lru_cache(maxsize=32)
def _rendered(filename: str, city: str | None) -> bytes:
    """The response body, serialised once and kept.

    The endpoint hands these bytes straight to the client instead of returning
    a model FastAPI would validate and re-encode. That is not a micro-optimisa-
    tion: the neighbourhoods file is 12.101 vertices, and running it through
    Pydantic on every request costs more than reading it from disk did. The
    files never change while the process is up, so there is nothing to
    invalidate -- which is exactly the condition that makes caching bytes safe.
    """
    collection = _collection(filename)

    if city is not None:
        wanted = city.casefold()
        features = [
            feature
            for feature in collection["features"]
            if str(feature["properties"].get("city", "")).casefold() == wanted
        ]
        collection = {"type": "FeatureCollection", "features": features}

    # separators without spaces: ~8% off the wire for nothing but taste.
    return json.dumps(collection, ensure_ascii=False, separators=(",", ":")).encode("utf-8")


def neighbourhoods(city: str | None = None) -> bytes:
    """Neighbourhood polygons, optionally for one city only."""
    return _rendered(NEIGHBOURHOODS_FILE, city)


def points_of_interest(city: str | None = None) -> bytes:
    """City centre, metro stations and main street, optionally for one city."""
    return _rendered(POINTS_OF_INTEREST_FILE, city)


def summary() -> dict[str, Any]:
    """What was loaded, for /health and for the tests to assert against."""
    counts: dict[str, Any] = {}
    for label, filename in (
        ("neighbourhoods", NEIGHBOURHOODS_FILE),
        ("points_of_interest", POINTS_OF_INTEREST_FILE),
    ):
        try:
            features = _collection(filename)["features"]
        except GeoDataUnavailable as error:
            counts[label] = {"available": False, "error": str(error)}
            continue

        cities: dict[str, int] = {}
        for feature in features:
            city = str(feature["properties"]["city"])
            cities[city] = cities.get(city, 0) + 1
        counts[label] = {"available": True, "features": len(features), "by_city": cities}

    return counts


# ==============================================================================
# Locating a listing inside a neighbourhood
# ==============================================================================
#
# The listings do not carry a neighbourhood key. The `_Sale` tables of
# idealista18 have no `LOCATIONID` -- checked column by column in all three
# .rda -- so the only way to say which neighbourhood a flat is in is to test its
# coordinates against the polygons.
#
# That test is done **once, offline**, by `python -m scripts.assign_neighbourhoods`,
# which writes the answer into two columns. Doing it per query was the
# alternative and it is not close: the "draw your area" filter already pays one
# Python callback per candidate row and that is affordable only because a drawn
# area is one polygon over a few thousand rows. Here it would be 277 polygons
# over 150k rows on every request.


@dataclass(frozen=True, slots=True)
class Neighbourhood:
    """One polygon, with the box that lets most points be rejected for free."""

    location_id: str
    name: str
    city: str
    lat_min: float
    lat_max: float
    lon_min: float
    lon_max: float
    #: (exterior, *holes) per part, each ring a tuple of (lat, lon).
    parts: tuple[tuple[tuple[tuple[float, float], ...], ...], ...]

    def contains(self, latitude: float, longitude: float) -> bool:
        if not (self.lat_min <= latitude <= self.lat_max):
            return False
        if not (self.lon_min <= longitude <= self.lon_max):
            return False

        for rings in self.parts:
            if not _in_ring(latitude, longitude, rings[0]):
                continue
            # Inside the outer ring. A hole would put it back outside; the
            # current data has none, but the format allows them and a silent
            # wrong answer here would be invisible.
            if any(_in_ring(latitude, longitude, hole) for hole in rings[1:]):
                continue
            return True
        return False


def _in_ring(latitude: float, longitude: float, ring: tuple[tuple[float, float], ...]) -> bool:
    """Ray casting, same rule as :func:`app.geometry.point_in_polygon`.

    Not a call into that module: this one works on decoded coordinates rather
    than on the "lat,lon;..." wire format, and it is run 150.000 times in a row,
    where re-parsing a string would dominate.
    """
    inside = False
    previous_lat, previous_lon = ring[-1]

    for current_lat, current_lon in ring:
        # The asymmetric comparison is what stops a vertex sitting exactly on
        # the ray from being counted twice.
        if (current_lat > latitude) != (previous_lat > latitude):
            crossing = current_lon + (latitude - current_lat) / (previous_lat - current_lat) * (
                previous_lon - current_lon
            )
            if longitude < crossing:
                inside = not inside
        previous_lat, previous_lon = current_lat, current_lon

    return inside


#: Side of the lookup grid, in degrees. 0,01 is about 1,1 km: fine enough that a
#: cell holds one or two neighbourhoods, coarse enough that the three cities
#: together need only ~1.500 cells.
_GRID = 0.01


class NeighbourhoodIndex:
    """Every neighbourhood, plus a grid that makes "which one is this in?" cheap.

    Without the grid, locating a point means testing it against all 277
    polygons. With it, the bounding box of each polygon is stamped onto the
    cells it covers, and a lookup tests only the handful that could match --
    which took the assignment of 149.923 listings from minutes to seconds.
    """

    def __init__(self, neighbourhoods: list[Neighbourhood]) -> None:
        self.all = tuple(neighbourhoods)
        self.by_id = {item.location_id: item for item in neighbourhoods}

        self._cells: dict[tuple[int, int], list[Neighbourhood]] = {}
        for item in neighbourhoods:
            for row in range(int(item.lat_min // _GRID), int(item.lat_max // _GRID) + 1):
                for column in range(int(item.lon_min // _GRID), int(item.lon_max // _GRID) + 1):
                    self._cells.setdefault((row, column), []).append(item)

    def locate(self, latitude: float | None, longitude: float | None) -> Neighbourhood | None:
        """The neighbourhood containing the point, or None if it is in none.

        None is a real answer and not a failure: plenty of listings sit outside
        every polygon, because the dataset covers a metropolitan area while the
        polygons stop at the municipal boundary.
        """
        if latitude is None or longitude is None:
            return None

        candidates = self._cells.get((int(latitude // _GRID), int(longitude // _GRID)))
        if not candidates:
            return None

        for candidate in candidates:
            if candidate.contains(latitude, longitude):
                return candidate
        return None


def _neighbourhood_from(feature: dict[str, Any]) -> Neighbourhood:
    geometry = feature["geometry"]
    coordinates = geometry["coordinates"]
    if geometry["type"] == "Polygon":
        coordinates = [coordinates]

    # GeoJSON stores [lon, lat]; everything downstream of here works in
    # (lat, lon), so the flip happens once, at the edge.
    parts = tuple(
        tuple(tuple((lat, lon) for lon, lat in ring) for ring in polygon)
        for polygon in coordinates
    )

    latitudes = [lat for polygon in parts for ring in polygon for lat, _ in ring]
    longitudes = [lon for polygon in parts for ring in polygon for _, lon in ring]

    properties = feature["properties"]
    return Neighbourhood(
        location_id=properties["location_id"],
        name=properties["name"],
        city=properties["city"],
        lat_min=min(latitudes),
        lat_max=max(latitudes),
        lon_min=min(longitudes),
        lon_max=max(longitudes),
        parts=parts,
    )


@lru_cache(maxsize=1)
def neighbourhood_index() -> NeighbourhoodIndex:
    """The index, built once per process from the GeoJSON on disk."""
    collection = _collection(NEIGHBOURHOODS_FILE)
    return NeighbourhoodIndex([_neighbourhood_from(f) for f in collection["features"]])
