"""Point-in-polygon over SQLite, for the "draw your area" search.

Lives in ``app`` and not in ``app.storage`` even though SQLite is its only
consumer: the query model imports it to validate a drawn polygon, and
``app.storage`` imports the query model, so putting it under storage closed an
import cycle that only showed up when something imported the models first.

SQLite has no geometry types, so the test is registered as a user-defined
function and called from the ``WHERE`` clause. That keeps the filter *exact*
and, crucially, keeps ``COUNT(*)`` honest: filtering in Python after the fact
would give a page of results that no longer matches the total printed above it.

The cost is one Python call per candidate row, so every query that uses this
also carries the polygon's bounding box as an indexed clause. SQLite evaluates
the cheap indexed range first and only calls back here for the survivors --
for a neighbourhood-sized polygon that is a few thousand rows out of 150k.
"""

from __future__ import annotations

from functools import lru_cache

#: Separators of the wire format: "lat,lon;lat,lon;...".
_POINT_SEPARATOR = ";"
_COORD_SEPARATOR = ","

#: Guard against a pathological request. A freehand polygon simplified in the
#: browser lands well under this; anything above it is not a real drawing.
MAX_VERTICES = 500


class PolygonError(ValueError):
    """The encoded polygon is malformed or degenerate."""


@lru_cache(maxsize=32)
def parse_polygon(encoded: str) -> tuple[tuple[float, float], ...]:
    """Decode ``"lat,lon;lat,lon;..."`` into vertices, cached by the raw string.

    The cache is what makes the SQL callback affordable: the same polygon
    arrives once per row, and re-splitting the string every time would dominate
    the query.
    """
    vertices: list[tuple[float, float]] = []

    for chunk in encoded.split(_POINT_SEPARATOR):
        chunk = chunk.strip()
        if not chunk:
            continue
        parts = chunk.split(_COORD_SEPARATOR)
        if len(parts) != 2:
            raise PolygonError(f"vértice mal formado: {chunk!r}; se esperaba 'lat,lon'")
        try:
            latitude, longitude = float(parts[0]), float(parts[1])
        except ValueError:
            raise PolygonError(f"vértice no numérico: {chunk!r}") from None
        if not -90.0 <= latitude <= 90.0 or not -180.0 <= longitude <= 180.0:
            raise PolygonError(f"vértice fuera del mundo: {chunk!r}")
        vertices.append((latitude, longitude))

    if len(vertices) < 3:
        raise PolygonError("un polígono necesita al menos 3 vértices")
    if len(vertices) > MAX_VERTICES:
        raise PolygonError(f"demasiados vértices ({len(vertices)}); el máximo es {MAX_VERTICES}")

    # A closing vertex equal to the first is redundant for the ray-casting test,
    # which already treats the ring as closed. Drop it so it is not counted twice.
    if len(vertices) > 3 and vertices[0] == vertices[-1]:
        vertices.pop()

    return tuple(vertices)


def polygon_bounds(encoded: str) -> tuple[float, float, float, float]:
    """(lat_min, lat_max, lon_min, lon_max) of the polygon, for the SQL prefilter."""
    vertices = parse_polygon(encoded)
    latitudes = [vertex[0] for vertex in vertices]
    longitudes = [vertex[1] for vertex in vertices]
    return min(latitudes), max(latitudes), min(longitudes), max(longitudes)


def point_in_polygon(latitude: float | None, longitude: float | None, encoded: str) -> int:
    """Ray casting. Returns 1 inside, 0 outside -- SQLite has no boolean type.

    A listing with no coordinates cannot be inside a drawn area, so it is out.
    """
    if latitude is None or longitude is None:
        return 0

    vertices = parse_polygon(encoded)
    inside = False

    previous_lat, previous_lon = vertices[-1]
    for current_lat, current_lon in vertices:
        # Does the horizontal ray at `latitude` cross this edge? The asymmetric
        # comparison (one strict, one not) is what stops a vertex exactly on the
        # ray from being counted twice.
        if (current_lat > latitude) != (previous_lat > latitude):
            crossing_lon = current_lon + (latitude - current_lat) / (previous_lat - current_lat) * (
                previous_lon - current_lon
            )
            if longitude < crossing_lon:
                inside = not inside
        previous_lat, previous_lon = current_lat, current_lon

    return 1 if inside else 0
