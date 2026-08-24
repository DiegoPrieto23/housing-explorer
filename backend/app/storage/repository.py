"""Persistence and aggregation for listings. All SQL lives here, not in the API."""

from __future__ import annotations

import math
import sqlite3
from datetime import datetime
from itertools import groupby
from typing import Any

from app.models.filters import ListingFilters
from app.models.listing import (
    AMENITY_COLUMNS,
    BARGAIN_THRESHOLD,
    Condition,
    Listing,
    Operation,
    PropertyType,
)
from app.storage.cache import stats_cache
from app.storage.database import Database

#: Everything a listing carries. The amenity flags sit at the end so that the
#: row-to-model conversion can read them as one block.
_COLUMNS = (
    "global_id, id, source, title, url, operation, property_type, price, "
    "size_m2, rooms, latitude, longitude, address, zone, ingested_at, "
    "expected_price, price_deviation, "
    "bathrooms, floor, year_built, condition, "
    "distance_to_center_km, distance_to_metro_km, "
    "has_lift, has_terrace, has_parking, has_air_conditioning, has_pool, "
    "has_doorman, has_garden, has_storage, has_wardrobes"
)

#: What a source actually provides. The two score columns are derived, so they
#: are never part of an INSERT: see _UPSERT.
_INGESTED_COLUMNS = (
    "global_id, id, source, title, url, operation, property_type, price, "
    "size_m2, rooms, latitude, longitude, address, zone, ingested_at, "
    "bathrooms, floor, year_built, condition, "
    "distance_to_center_km, distance_to_metro_km, "
    "has_lift, has_terrace, has_parking, has_air_conditioning, has_pool, "
    "has_doorman, has_garden, has_storage, has_wardrobes"
)

#: How many placeholders the upsert needs. Derived, not written by hand: the
#: last time this was a literal it had to be kept in step with the column list
#: by hand, which is exactly the kind of thing that goes wrong silently.
_INGESTED_COUNT = len(_INGESTED_COLUMNS.split(","))

_UPSERT = (
    "INSERT INTO listings ({columns}) VALUES ({placeholders}) "
    "ON CONFLICT(global_id) DO UPDATE SET "
    "title=excluded.title, url=excluded.url, operation=excluded.operation, "
    "property_type=excluded.property_type, price=excluded.price, "
    "size_m2=excluded.size_m2, rooms=excluded.rooms, "
    "latitude=excluded.latitude, longitude=excluded.longitude, "
    "address=excluded.address, zone=excluded.zone, "
    "ingested_at=excluded.ingested_at, "
    "bathrooms=excluded.bathrooms, floor=excluded.floor, "
    "year_built=excluded.year_built, condition=excluded.condition, "
    "distance_to_center_km=excluded.distance_to_center_km, "
    "distance_to_metro_km=excluded.distance_to_metro_km, "
    "has_lift=excluded.has_lift, has_terrace=excluded.has_terrace, "
    "has_parking=excluded.has_parking, "
    "has_air_conditioning=excluded.has_air_conditioning, "
    "has_pool=excluded.has_pool, has_doorman=excluded.has_doorman, "
    "has_garden=excluded.has_garden, has_storage=excluded.has_storage, "
    "has_wardrobes=excluded.has_wardrobes, "
    # Any re-ingest can change the price or the surface area, which is exactly
    # what the estimate was computed from. Rather than try to work out whether
    # this particular update invalidates it, drop the score and let
    # `scripts.score_listings` recompute: a stale bargain flag is worse than an
    # absent one, and re-scoring is cheap.
    "expected_price=NULL, price_deviation=NULL"
)

#: Above this many matching rows, percentiles are read one query per rank
#: instead of by fetching every price. See ListingRepository._percentiles.
PERCENTILE_SCAN_THRESHOLD = 50_000

#: Above this many matching rows the map switches from individual markers to
#: server-side grid cells. Chosen so the JSON stays around a megabyte and the
#: browser is not asked to keep more DOM nodes than it can animate: a marker is
#: ~90 bytes on the wire, a cell ~50.
DEFAULT_MAP_POINT_BUDGET = 6_000

#: Ceiling on the number of aggregated cells returned. Past this the cells are
#: smaller than the dots drawn for them, so the extra rows buy nothing.
MAX_MAP_CELLS = 2_000

#: At or below this zoom the map groups by zone instead of by grid square.
#:
#: A fixed grid has to cut *somewhere*, and at zoom 6 the cell is 0.94 degrees
#: wide -- about 104 km -- with a boundary that lands at longitude -3.75, which
#: is inside Madrid. The result was one dot for Barcelona, one for Valencia and
#: **two** for Madrid, split down the middle of the city. Nudging the grid only
#: moves the problem to a different city.
#:
#: So while a cell would be larger than a city, the grouping key is the zone: one
#: dot per city, which is what the country-wide view is asking about anyway. A
#: city is roughly 0.2 degrees across, and the cell stops being bigger than that
#: at zoom 9, hence the threshold.
ZONE_GROUPING_MAX_ZOOM = 8

# Stable tie-breaker so paging never repeats or skips a row.
_ORDER_BY = " ORDER BY ingested_at DESC, global_id"

#: Orderings the API exposes. Every one ends in global_id so that paging is
#: deterministic even when the leading column ties -- without it, two rows with
#: the same deviation could swap places between page 1 and page 2 and be shown
#: twice, or not at all.
_ORDERINGS = {
    "reciente": "ingested_at DESC, global_id",
    "precio": "price ASC, global_id",
    "precio_desc": "price DESC, global_id",
    # NULLs last: unscored listings are not "the biggest bargain", they are
    # unknown, and SQLite sorts NULL first by default.
    "desviacion": "price_deviation IS NULL, price_deviation ASC, global_id",
}


def build_where(
    filters: ListingFilters | None,
    *extra_clauses: str,
    hint_unselective: bool = False,
) -> tuple[str, list[Any]]:
    """Translate filters into a WHERE fragment and its bound parameters.

    Returns ``("", [])`` when there is nothing to filter on, so callers can
    concatenate the fragment unconditionally.

    ``hint_unselective`` wraps the bounding-box test in SQLite's ``likely()``.
    That is a hint, not a directive: it tells the planner the term is usually
    true, so filtering by it first buys little. Only the ordered, paged query
    passes it -- see :meth:`ListingRepository.list`.
    """
    clauses: list[str] = []
    params: list[Any] = []

    if filters is not None:
        if filters.operation is not None:
            clauses.append("operation = ?")
            params.append(filters.operation.value)

        if filters.property_type is not None:
            clauses.append("property_type = ?")
            params.append(filters.property_type.value)

        if filters.source is not None:
            clauses.append("source = ?")
            params.append(filters.source)

        if filters.zone is not None:
            # COLLATE NOCASE matches idx_listings_zone, so this stays indexed.
            clauses.append("zone = ? COLLATE NOCASE")
            params.append(filters.zone)

        if filters.price_min is not None:
            clauses.append("price >= ?")
            params.append(filters.price_min)
        if filters.price_max is not None:
            clauses.append("price <= ?")
            params.append(filters.price_max)

        # A NULL size or room count fails these comparisons, which is the
        # intended reading: "at least 50 m2" cannot include unknown areas.
        if filters.size_min is not None:
            clauses.append("size_m2 >= ?")
            params.append(filters.size_min)
        if filters.size_max is not None:
            clauses.append("size_m2 <= ?")
            params.append(filters.size_max)

        if filters.rooms is not None:
            clauses.append("rooms = ?")
            params.append(filters.rooms)
        if filters.rooms_min is not None:
            clauses.append("rooms >= ?")
            params.append(filters.rooms_min)

        bbox = filters.bbox
        if bbox is not None:
            lat_min, lat_max, lon_min, lon_max = bbox
            clauses.append(
                "likely(latitude BETWEEN ? AND ?) AND likely(longitude BETWEEN ? AND ?)"
                if hint_unselective
                else "latitude BETWEEN ? AND ? AND longitude BETWEEN ? AND ?"
            )
            params.extend([lat_min, lat_max, lon_min, lon_max])

        if filters.deviation_max is not None:
            # An unscored listing is not a bargain: NULL fails the comparison,
            # which is the behaviour we want and comes for free in SQL.
            clauses.append("price_deviation <= ?")
            params.append(filters.deviation_max)

        if filters.bathrooms_min is not None:
            clauses.append("bathrooms >= ?")
            params.append(filters.bathrooms_min)

        if filters.floor_min is not None:
            clauses.append("floor >= ?")
            params.append(filters.floor_min)
        if filters.floor_max is not None:
            clauses.append("floor <= ?")
            params.append(filters.floor_max)

        if filters.year_min is not None:
            clauses.append("year_built >= ?")
            params.append(filters.year_min)

        if filters.condition is not None:
            clauses.append("condition = ?")
            params.append(filters.condition.value)

        if filters.center_max_km is not None:
            clauses.append("distance_to_center_km <= ?")
            params.append(filters.center_max_km)
        if filters.metro_max_km is not None:
            clauses.append("distance_to_metro_km <= ?")
            params.append(filters.metro_max_km)

        for amenity in filters.amenities or ():
            # Every requested amenity is required, not any of them: a search
            # panel that returns homes without a lift when you ticked "lift" is
            # broken, however many other boxes you also ticked.
            clauses.append(f"{AMENITY_COLUMNS[amenity]} = 1")

        if filters.scored_only:
            clauses.append("price_deviation IS NOT NULL")

        if filters.ids:
            # Favourites live in the browser, so the only way to show them as a
            # list is to ask for exactly these ids. One placeholder each rather
            # than a joined string: the values come from localStorage, which is
            # user-writable, and a bound parameter cannot be read as SQL.
            placeholders = ", ".join(["?"] * len(filters.ids))
            clauses.append(f"global_id IN ({placeholders})")
            params.extend(filters.ids)

        if filters.polygon is not None:
            # Order matters. The bounding box goes first so idx_listings_bbox
            # narrows the candidate set; only then does SQLite pay for one
            # Python callback per surviving row.
            lat_min, lat_max, lon_min, lon_max = filters.polygon_bbox  # type: ignore[misc]
            clauses.append("latitude BETWEEN ? AND ? AND longitude BETWEEN ? AND ?")
            params.extend([lat_min, lat_max, lon_min, lon_max])
            clauses.append("point_in_polygon(latitude, longitude, ?) = 1")
            params.append(filters.polygon)

    # Extra clauses go last, so any placeholders they carry bind after the
    # filter parameters: callers pass [*params, *extra_params].
    clauses.extend(extra_clauses)

    where = " WHERE " + " AND ".join(clauses) if clauses else ""
    return where, params


def cache_key(prefix: str, filters: ListingFilters | None, **extra: Any) -> str:
    """A key that changes exactly when the answer would.

    Built from the compiled WHERE clause and its parameters rather than from the
    filter object, because that is what actually determines the rows: two
    different filter objects that compile to the same SQL genuinely have the
    same answer, and should share an entry.
    """
    where, params = build_where(filters)
    tail = ",".join(f"{name}={value}" for name, value in sorted(extra.items()))
    return f"{prefix}|{where}|{params}|{tail}"


class ListingRepository:
    """Reads and writes over the ``listings`` table."""

    def __init__(self, database: Database) -> None:
        self.database = database

    # -- writes ------------------------------------------------------------

    def upsert_many(self, listings: list[Listing]) -> int:
        """Insert or refresh listings keyed on (source, id). Returns rows written."""
        if not listings:
            return 0

        rows = [self._to_row(listing) for listing in listings]
        statement = _UPSERT.format(
            columns=_INGESTED_COLUMNS, placeholders=", ".join(["?"] * _INGESTED_COUNT)
        )
        with self.database.session() as connection:
            cursor = connection.executemany(statement, rows)
            written = cursor.rowcount if cursor.rowcount != -1 else len(rows)

        # Every cached aggregate now describes data that no longer exists.
        # Bumping here, in the one method that writes listings, is what makes
        # invalidation impossible to forget at a call site.
        stats_cache.bump()
        return written

    # -- reads -------------------------------------------------------------

    def list(
        self,
        filters: ListingFilters | None = None,
        *,
        limit: int = 200,
        offset: int = 0,
        order: str = "reciente",
    ) -> list[Listing]:
        # The hint is what makes SQLite walk idx_listings_recent in order and stop
        # at `limit` matches, rather than gathering every row in the viewport and
        # sorting them to throw all but 24 away. Only safe here, where there is an
        # ORDER BY worth serving; on a COUNT it would talk the planner out of the
        # covering bounding-box index for nothing.
        where, params = build_where(filters, hint_unselective=True)
        order_by = " ORDER BY " + _ORDERINGS[order]
        query = "SELECT " + _COLUMNS + " FROM listings" + where + order_by + " LIMIT ? OFFSET ?"
        with self.database.session() as connection:
            rows = connection.execute(query, [*params, limit, offset]).fetchall()
        return [self._to_listing(row) for row in rows]

    def count(self, filters: ListingFilters | None = None) -> int:
        """Cached: the list, the map and the statistics all ask for the same total."""
        return stats_cache.get_or_compute(cache_key("count", filters), lambda: self._count(filters))

    def _count(self, filters: ListingFilters | None = None) -> int:
        where, params = build_where(filters)
        with self.database.session() as connection:
            row = connection.execute("SELECT COUNT(*) FROM listings" + where, params).fetchone()
        return int(row[0])

    def get(self, source: str, listing_id: str) -> Listing | None:
        return self.get_by_global_id(f"{source}:{listing_id}")

    def get_by_global_id(self, global_id: str) -> Listing | None:
        query = "SELECT " + _COLUMNS + " FROM listings WHERE global_id = ?"
        with self.database.session() as connection:
            row = connection.execute(query, (global_id,)).fetchone()
        return self._to_listing(row) if row else None

    def find_by_id(self, listing_id: str) -> list[Listing]:
        """Every listing with this source-local id, across sources.

        Lets ``GET /listings/{id}`` accept a bare id and only complain about
        ambiguity when two sources really do share one.
        """
        query = "SELECT " + _COLUMNS + " FROM listings WHERE id = ?" + _ORDER_BY
        with self.database.session() as connection:
            rows = connection.execute(query, (listing_id,)).fetchall()
        return [self._to_listing(row) for row in rows]

    def counts_by_source(self) -> dict[str, int]:
        with self.database.session() as connection:
            rows = connection.execute(
                "SELECT source, COUNT(*) AS total FROM listings GROUP BY source"
            ).fetchall()
        return {row["source"]: row["total"] for row in rows}

    def facets(self) -> dict[str, Any]:
        """Cached. See _facets for the query."""
        return stats_cache.get_or_compute("facets", self._facets)

    def _facets(self) -> dict[str, Any]:
        """The vocabulary of the stored set: what values a filter panel can offer.

        Deliberately unfiltered. The sidebar needs stable options and slider
        bounds; recomputing them from the current selection would make the
        controls move under the user's cursor as they drag.
        """
        with self.database.session() as connection:
            # The bounds travel with each zone so that picking a city in the
            # sidebar can fly the map there. Without them the frontend would
            # have to hardcode coordinates for Madrid, Barcelona and Valencia,
            # which stops being true the moment a new source is ingested.
            #
            # The sums are what make the box robust; see _zone_box.
            zones = [
                {"value": row["zone"], "count": int(row["count"]), **self._zone_box(row)}
                for row in connection.execute(
                    "SELECT zone, COUNT(*) AS count,"
                    " MIN(latitude) AS lat_min, MAX(latitude) AS lat_max,"
                    " MIN(longitude) AS lon_min, MAX(longitude) AS lon_max,"
                    " AVG(latitude) AS lat_mean, AVG(latitude * latitude) AS lat_sq,"
                    " AVG(longitude) AS lon_mean, AVG(longitude * longitude) AS lon_sq"
                    " FROM listings WHERE zone IS NOT NULL AND latitude IS NOT NULL"
                    " GROUP BY zone ORDER BY COUNT(*) DESC, zone"
                )
            ]
            operations = [
                {"value": row["operation"], "count": int(row["count"])}
                for row in connection.execute(
                    "SELECT operation, COUNT(*) AS count FROM listings"
                    " GROUP BY operation ORDER BY COUNT(*) DESC, operation"
                )
            ]
            property_types = [
                {"value": row["property_type"], "count": int(row["count"])}
                for row in connection.execute(
                    "SELECT property_type, COUNT(*) AS count FROM listings"
                    " GROUP BY property_type ORDER BY COUNT(*) DESC, property_type"
                )
            ]
            amenity_counts = connection.execute(
                "SELECT "
                + ", ".join(
                    f"SUM(CASE WHEN {column} = 1 THEN 1 ELSE 0 END) AS c{index}"
                    for index, column in enumerate(AMENITY_COLUMNS.values())
                )
                + " FROM listings"
            ).fetchone()
            amenities = sorted(
                (
                    {"value": amenity.value, "count": int(amenity_counts[f"c{index}"] or 0)}
                    for index, amenity in enumerate(AMENITY_COLUMNS)
                    if amenity_counts[f"c{index}"]
                ),
                key=lambda item: item["count"],
                reverse=True,
            )

            conditions = [
                {"value": row["condition"], "count": int(row["count"])}
                for row in connection.execute(
                    "SELECT condition, COUNT(*) AS count FROM listings"
                    " WHERE condition IS NOT NULL"
                    " GROUP BY condition ORDER BY COUNT(*) DESC"
                )
            ]

            ranges = connection.execute(
                "SELECT COUNT(*) AS total,"
                " MIN(price) AS price_min, MAX(price) AS price_max,"
                " MIN(size_m2) AS size_min, MAX(size_m2) AS size_max,"
                " MIN(rooms) AS rooms_min, MAX(rooms) AS rooms_max,"
                " MIN(bathrooms) AS bathrooms_min, MAX(bathrooms) AS bathrooms_max,"
                " MIN(floor) AS floor_min, MAX(floor) AS floor_max,"
                " MIN(year_built) AS year_min, MAX(year_built) AS year_max,"
                " MAX(distance_to_center_km) AS center_max,"
                " MAX(distance_to_metro_km) AS metro_max"
                " FROM listings"
            ).fetchone()

        return {
            "total": int(ranges["total"]),
            "zones": zones,
            "operations": operations,
            "property_types": property_types,
            "price_min": ranges["price_min"],
            "price_max": ranges["price_max"],
            "size_min": ranges["size_min"],
            "size_max": ranges["size_max"],
            "rooms_min": ranges["rooms_min"],
            "rooms_max": ranges["rooms_max"],
            "amenities": amenities,
            "conditions": conditions,
            "bathrooms_min": ranges["bathrooms_min"],
            "bathrooms_max": ranges["bathrooms_max"],
            "floor_min": ranges["floor_min"],
            "floor_max": ranges["floor_max"],
            "year_min": ranges["year_min"],
            "year_max": ranges["year_max"],
            "center_max_km": ranges["center_max"],
            "metro_max_km": ranges["metro_max"],
        }

    @staticmethod
    def _zone_box(row: sqlite3.Row) -> dict[str, float | None]:
        """A zone's box, immune to the odd listing filed under the wrong city.

        The plain MIN/MAX is not usable for flying the map: one of the 75.804
        Madrid listings sits in Almería, 400 km away, and the full extent would
        open the map on half of Spain to show a city.

        So the box is the mean plus or minus three standard deviations, clipped
        to the real extent. Three sigma keeps a genuinely spread-out city whole
        while a lone stray point -- which barely moves the mean and is far
        outside the spread -- falls outside it.
        """
        if row["lat_mean"] is None:
            return {"lat_min": None, "lat_max": None, "lon_min": None, "lon_max": None}

        def spread(mean: float, mean_of_squares: float) -> float:
            # Var(X) = E[X^2] - E[X]^2, clamped because floating point can push
            # a zero variance a hair below zero.
            return math.sqrt(max(mean_of_squares - mean * mean, 0.0))

        lat_sigma = spread(row["lat_mean"], row["lat_sq"])
        lon_sigma = spread(row["lon_mean"], row["lon_sq"])

        # A minimum half-width keeps a zone with a single listing, or one whose
        # points share a coordinate, from collapsing into a zero-area box that
        # flyToBounds would zoom to maximum on.
        margin = 0.005

        return {
            "lat_min": max(row["lat_mean"] - 3 * lat_sigma - margin, row["lat_min"]),
            "lat_max": min(row["lat_mean"] + 3 * lat_sigma + margin, row["lat_max"]),
            "lon_min": max(row["lon_mean"] - 3 * lon_sigma - margin, row["lon_min"]),
            "lon_max": min(row["lon_mean"] + 3 * lon_sigma + margin, row["lon_max"]),
        }

    def global_ids(self, source: str, *, only_unscored: bool = False) -> set[str]:
        """The keys of a source's rows, for a batch job to join against.

        A set rather than a list: the caller checks membership once per row of a
        190k-line CSV, and that is the difference between a scan and a hash.
        """
        query = "SELECT global_id FROM listings WHERE source = ?"
        if only_unscored:
            query += " AND price_deviation IS NULL"
        with self.database.session() as connection:
            return {row["global_id"] for row in connection.execute(query, (source,))}

    def update_scores(self, scores: list[tuple[str, float, float]]) -> int:
        """Write ``(global_id, expected_price, price_deviation)`` in bulk.

        Returns the number of rows actually updated, which is not necessarily
        the number passed in: a global_id that is not in the database is
        silently skipped, and the caller is expected to compare the counts.
        """
        if not scores:
            return 0

        with self.database.session() as connection:
            cursor = connection.executemany(
                "UPDATE listings SET expected_price = ?, price_deviation = ? WHERE global_id = ?",
                [(expected, deviation, key) for key, expected, deviation in scores],
            )
            written = cursor.rowcount if cursor.rowcount != -1 else len(scores)

        stats_cache.bump()
        return written

    def clear_scores(self, source: str | None = None) -> int:
        """Forget every estimate, so a re-score starts from a clean slate."""
        where, params = (" WHERE source = ?", [source]) if source else ("", [])
        with self.database.session() as connection:
            cursor = connection.execute(
                "UPDATE listings SET expected_price = NULL, price_deviation = NULL" + where,
                params,
            )
            cleared = cursor.rowcount

        stats_cache.bump()
        return cleared

    def scoring_coverage(self) -> dict[str, int]:
        """How much of the database carries an estimate. For /health and the CLI."""
        with self.database.session() as connection:
            row = connection.execute(
                "SELECT COUNT(*) AS total,"
                " SUM(price_deviation IS NOT NULL) AS scored,"
                " SUM(price_deviation <= ?) AS bargains"
                " FROM listings",
                (BARGAIN_THRESHOLD,),
            ).fetchone()
        return {
            "total": int(row["total"]),
            "scored": int(row["scored"] or 0),
            "bargains": int(row["bargains"] or 0),
        }

    # -- map ---------------------------------------------------------------

    @staticmethod
    def grid_step(zoom: int) -> float:
        """Cell size in degrees for a given map zoom.

        A web-mercator tile spans ``360 / 2**zoom`` degrees of longitude, and
        six cells per tile puts the aggregated dots far enough apart to read as
        separate clusters without turning into confetti.
        """
        return 360.0 / (2 ** max(zoom, 0)) / 6.0

    def map_data(
        self,
        filters: ListingFilters | None = None,
        *,
        zoom: int = 6,
        point_budget: int = DEFAULT_MAP_POINT_BUDGET,
        always_aggregate: bool = False,
    ) -> dict[str, Any]:
        """What to draw on the map, at whatever resolution the view can take.

        Two modes, picked by how many listings match rather than by a fixed
        cap:

        ``points``
            Few enough to send individually. Every matching listing is
            returned -- there is no truncation and nothing is hidden.
        ``clusters``
            Too many. The rows are grouped into a lat/lon grid **inside SQL**
            and only the cell totals travel, so all 150k listings are still
            represented; they are just counted rather than drawn one by one.

        Either way ``total`` is the exact number of matches, so the map never
        has to say "showing 1000 of 149.923".
        """
        total = self.count(filters)
        where, params = build_where(filters, "latitude IS NOT NULL")

        if total == 0:
            return {"mode": "points", "total": 0, "points": [], "clusters": []}

        if total <= point_budget and not always_aggregate:
            query = (
                "SELECT global_id, latitude, longitude, price, property_type,"
                " operation, price_deviation"
                " FROM listings" + where
            )
            with self.database.session() as connection:
                rows = connection.execute(query, params).fetchall()
            # Rounded on the way out. A marker cannot be placed to more than
            # about a metre, a price is not asked in cents, and a deviation is
            # shown as a whole percentage -- but SQLite hands back full doubles,
            # and serialising 40.415002935187 instead of 40.415 costs 16% of the
            # payload for digits nobody can see or use. Six thousand markers is
            # where that stops being pedantry.
            points = [
                {
                    "global_id": row["global_id"],
                    # 5 decimals of latitude is ~1.1 m on the ground.
                    "latitude": round(row["latitude"], 5),
                    "longitude": round(row["longitude"], 5),
                    "price": round(row["price"]),
                    "property_type": PropertyType(row["property_type"]),
                    "operation": Operation(row["operation"]),
                    "price_deviation": (
                        None
                        if row["price_deviation"] is None
                        else round(row["price_deviation"], 1)
                    ),
                }
                for row in rows
            ]
            return {"mode": "points", "total": total, "points": points, "clusters": []}

        step = self.grid_step(zoom)
        # The +90 / +180 offsets make the coordinates positive before the cast,
        # because CAST truncates towards zero and would fold the cells either
        # side of the equator and the prime meridian into one.
        #
        # `group_key` is what decides whether a cell is a grid square or a whole
        # zone. Listings with no zone always fall back to the grid, so a source
        # that does not fill it in still gets a usable map.
        if zoom <= ZONE_GROUPING_MAX_ZOOM:
            group_key = (
                "CASE WHEN zone IS NOT NULL THEN 'z:' || zone"
                " ELSE 'g:' || cell_y || ',' || cell_x END"
            )
        else:
            group_key = "'g:' || cell_y || ',' || cell_x"

        query = (
            "SELECT CAST((latitude + 90.0) / ? AS INTEGER) AS cell_y,"
            " CAST((longitude + 180.0) / ? AS INTEGER) AS cell_x,"
            " COUNT(*) AS count, AVG(price) AS avg_price,"
            # Averaging the ratio, not dividing the averages. The mean of
            # price/m2 is what "what does a metre cost around here" means; the
            # total price over the total area would let one 400 m2 listing speak
            # for a whole neighbourhood of studios.
            " AVG(CASE WHEN size_m2 > 0 THEN price / size_m2 END) AS avg_price_per_m2,"
            " COUNT(CASE WHEN size_m2 > 0 THEN 1 END) AS with_size,"
            " AVG(latitude) AS latitude, AVG(longitude) AS longitude,"
            # The cell's real extent, so a heat map can draw the rectangle it
            # actually covers instead of a blob around the centroid.
            " MIN(latitude) AS lat_min, MAX(latitude) AS lat_max,"
            " MIN(longitude) AS lon_min, MAX(longitude) AS lon_max,"
            # Sums of squares, so the extent can be made robust in Python. A
            # plain MIN/MAX is unusable once a cell is a whole city: one of the
            # 75.804 Madrid listings sits in Almería, and its rectangle would
            # stretch 400 km south of the city.
            " AVG(latitude * latitude) AS lat_sq,"
            " AVG(longitude * longitude) AS lon_sq,"
            " zone AS zone"
            " FROM listings" + where +
            f" GROUP BY {group_key} ORDER BY count DESC LIMIT ?"
        )
        with self.database.session() as connection:
            rows = connection.execute(query, [step, step, *params, MAX_MAP_CELLS]).fetchall()

        clusters = [
            {
                "latitude": round(row["latitude"], 5),
                "longitude": round(row["longitude"], 5),
                **self._cell_extent(row),
                "count": int(row["count"]),
                "avg_price": None if row["avg_price"] is None else round(row["avg_price"]),
                # A cell where nothing declares its area has no price per metre.
                # None, not zero: "unknown" and "free" are not the same colour.
                "avg_price_per_m2": (
                    None
                    if row["avg_price_per_m2"] is None or row["with_size"] == 0
                    else round(row["avg_price_per_m2"])
                ),
                "with_size": int(row["with_size"] or 0),
                # Only meaningful when the grouping was by zone; a grid square
                # spanning two cities would report whichever one SQLite picked.
                "zone": row["zone"] if zoom <= ZONE_GROUPING_MAX_ZOOM else None,
            }
            for row in rows
        ]
        return {"mode": "clusters", "total": total, "points": [], "clusters": clusters}

    #: Upper bounds of the distance-to-centre bands, in km.
    DISTANCE_BANDS = (0.5, 1, 1.5, 2, 3, 4, 6, 8)

    def by_distance(self, filters: ListingFilters | None = None) -> list[dict[str, Any]]:
        """Price per m2 against distance to the city centre. Cached.

        The gradient the notebook found, live: it is steep, and it is **not the
        same slope in each city**, so it is only worth reading with a city
        selected. With all three mixed the curve averages three curves and
        describes none of them.
        """
        return stats_cache.get_or_compute(
            cache_key("distance", filters), lambda: self._by_distance(filters)
        )

    def _by_distance(self, filters: ListingFilters | None) -> list[dict[str, Any]]:
        bounds = self.DISTANCE_BANDS
        cases = " ".join(
            f"WHEN distance_to_center_km <= {upper} THEN {index}"
            for index, upper in enumerate(bounds)
        )
        bucket = f"CASE {cases} ELSE {len(bounds)} END"
        where, params = build_where(
            filters, "size_m2 > 0", "distance_to_center_km IS NOT NULL"
        )
        query = (
            f"SELECT {bucket} AS bucket, COUNT(*) AS count,"
            " AVG(price) AS avg_price, AVG(price / size_m2) AS avg_price_per_m2"
            " FROM listings" + where + " GROUP BY bucket ORDER BY bucket"
        )
        with self.database.session() as connection:
            rows = connection.execute(query, params).fetchall()

        def label(index: int) -> str:
            if index == 0:
                return f"<{bounds[0]:g}"
            if index >= len(bounds):
                return f">{bounds[-1]:g}"
            return f"{bounds[index - 1]:g}-{bounds[index]:g}"

        return [
            {
                "bucket": int(row["bucket"]),
                "label": label(int(row["bucket"])),
                "count": int(row["count"]),
                "avg_price": round(row["avg_price"]),
                "avg_price_per_m2": round(row["avg_price_per_m2"]),
            }
            for row in rows
        ]

    def amenity_impact(self, filters: ListingFilters | None = None) -> list[dict[str, Any]]:
        """For each extra: how many have it, and the price per m2 with and without.

        **This is a correlation, and the interface has to say so.** Pools and
        doormen turn up where the money already is; fitting one does not add 40%
        to a flat. What these numbers describe is where such homes are, not what
        the feature is worth.

        Computed as one scan with conditional aggregates rather than two queries
        per amenity: eighteen passes over the filtered set to draw nine bars is
        not a trade worth making.
        """
        return stats_cache.get_or_compute(
            cache_key("amenities", filters), lambda: self._amenity_impact(filters)
        )

    def _amenity_impact(self, filters: ListingFilters | None) -> list[dict[str, Any]]:
        selects = []
        for index, column in enumerate(AMENITY_COLUMNS.values()):
            selects.append(f"SUM(CASE WHEN {column} = 1 THEN 1 ELSE 0 END) AS n{index}")
            selects.append(f"AVG(CASE WHEN {column} = 1 THEN price / size_m2 END) AS y{index}")
            selects.append(f"AVG(CASE WHEN {column} = 0 THEN price / size_m2 END) AS n_{index}")

        where, params = build_where(filters, "size_m2 > 0")
        query = "SELECT COUNT(*) AS total, " + ", ".join(selects) + " FROM listings" + where
        with self.database.session() as connection:
            row = connection.execute(query, params).fetchone()

        total = int(row["total"])
        results = []
        for index, amenity in enumerate(AMENITY_COLUMNS):
            with_it, without_it = row[f"y{index}"], row[f"n_{index}"]
            # A comparison needs both sides. If every listing in the selection
            # has a lift, "with a lift it costs more than without" means nothing,
            # and a bar drawn from one side alone would be a lie.
            if with_it is None or without_it is None or not without_it:
                continue
            count = int(row[f"n{index}"])
            results.append(
                {
                    "amenity": amenity,
                    "count": count,
                    "share": round(100 * count / total, 1) if total else 0.0,
                    "with_it": round(with_it),
                    "without_it": round(without_it),
                    "difference": round(100 * (with_it / without_it - 1), 1),
                }
            )
        results.sort(key=lambda item: item["difference"], reverse=True)
        return results

    # -- distributions -----------------------------------------------------

    def by_rooms(self, filters: ListingFilters | None = None) -> list[dict[str, Any]]:
        """Median price and price per m2 for each room count. Cached."""
        return stats_cache.get_or_compute(
            cache_key("rooms", filters), lambda: self._by_bucket(filters, "rooms")
        )

    def by_size(self, filters: ListingFilters | None = None) -> list[dict[str, Any]]:
        """The same, for bands of floor area. Cached."""
        return stats_cache.get_or_compute(
            cache_key("size", filters), lambda: self._by_bucket(filters, "size")
        )

    #: Upper bounds of the floor-area bands, in m2. Chosen so each holds a real
    #: kind of home rather than an equal slice of the range: a studio, a one-bed,
    #: a family flat, a house.
    SIZE_BANDS = (40, 60, 80, 100, 130, 170, 220, 300)

    def _by_bucket(self, filters: ListingFilters | None, dimension: str) -> list[dict[str, Any]]:
        """Aggregate into buckets of rooms or of floor area, in one pass.

        The median is not computed here. It would need one extra query per
        bucket, and this feeds a bar chart two centimetres wide in a sidebar --
        the mean is what it can show, and asking the database nine more times to
        draw the same nine bars is not a trade worth making.
        """
        if dimension == "rooms":
            # Everything above 6 in one bucket: past that the counts are too
            # small for the average to mean anything.
            bucket = "CASE WHEN rooms > 6 THEN 6 ELSE rooms END"
            extra = "rooms IS NOT NULL AND size_m2 > 0"
        else:
            bounds = self.SIZE_BANDS
            cases = " ".join(
                f"WHEN size_m2 <= {upper} THEN {index}"
                for index, upper in enumerate(bounds)
            )
            bucket = f"CASE {cases} ELSE {len(bounds)} END"
            extra = "size_m2 > 0"

        where, params = build_where(filters, extra)
        query = (
            f"SELECT {bucket} AS bucket, COUNT(*) AS count,"
            " AVG(price) AS avg_price,"
            " AVG(price / size_m2) AS avg_price_per_m2"
            " FROM listings" + where + " GROUP BY bucket ORDER BY bucket"
        )
        with self.database.session() as connection:
            rows = connection.execute(query, params).fetchall()

        return [
            {
                "bucket": int(row["bucket"]),
                "label": self._bucket_label(dimension, int(row["bucket"])),
                "count": int(row["count"]),
                "avg_price": round(row["avg_price"]),
                "avg_price_per_m2": round(row["avg_price_per_m2"]),
            }
            for row in rows
        ]

    @classmethod
    def _bucket_label(cls, dimension: str, bucket: int) -> str:
        if dimension == "rooms":
            return "6+" if bucket >= 6 else str(bucket)
        bounds = cls.SIZE_BANDS
        if bucket == 0:
            return f"≤{bounds[0]}"
        if bucket >= len(bounds):
            return f">{bounds[-1]}"
        return f"{bounds[bucket - 1]}–{bounds[bucket]}"

    @staticmethod
    def _cell_extent(row: sqlite3.Row) -> dict[str, float]:
        """The rectangle a cell really covers, ignoring the odd stray listing.

        Same trick as :meth:`_zone_box`, and for the same reason: the extent is
        what the heat map draws and what a click on the cell searches inside, so
        one mislabelled listing 400 km away must not define either. Mean plus or
        minus three standard deviations, clipped to the true extent.
        """
        margin = 0.002  # ~200 m, so a cell holding one listing still has area

        def bounds(mean: float, mean_sq: float, low: float, high: float) -> tuple[float, float]:
            spread = math.sqrt(max(mean_sq - mean * mean, 0.0))
            return (
                max(mean - 3 * spread - margin, low),
                min(mean + 3 * spread + margin, high),
            )

        lat_min, lat_max = bounds(
            row["latitude"], row["lat_sq"], row["lat_min"], row["lat_max"]
        )
        lon_min, lon_max = bounds(
            row["longitude"], row["lon_sq"], row["lon_min"], row["lon_max"]
        )
        return {
            "lat_min": round(lat_min, 5),
            "lat_max": round(lat_max, 5),
            "lon_min": round(lon_min, 5),
            "lon_max": round(lon_max, 5),
        }

    # -- aggregates --------------------------------------------------------

    def overall_stats(self, filters: ListingFilters | None = None) -> dict[str, Any]:
        """Cached. See _overall_stats for the query."""
        return stats_cache.get_or_compute(
            cache_key("overall", filters), lambda: self._overall_stats(filters)
        )

    def _overall_stats(self, filters: ListingFilters | None = None) -> dict[str, Any]:
        """Count, averages and price percentiles over the filtered set.

        Percentiles use the nearest-rank definition and are read one at a time
        with ORDER BY price / LIMIT 1 OFFSET k, which walks the
        ``idx_listings_price_size`` covering index. The obvious alternative --
        ROW_NUMBER() in a CTE -- has to materialise every ranked row and
        measured ~55x slower on 150k rows (1378ms vs 25ms for five values).
        """
        where, params = build_where(filters)
        aggregates = (
            "SELECT COUNT(*) AS count,"
            " AVG(price) AS avg_price,"
            " MIN(price) AS min_price,"
            " MAX(price) AS max_price,"
            " AVG(CASE WHEN size_m2 > 0 THEN price / size_m2 END) AS avg_price_per_m2"
            " FROM listings" + where
        )

        percentiles = {
            "p25_price": 0.25,
            "median_price": 0.50,
            "p75_price": 0.75,
            "p90_price": 0.90,
            "p99_price": 0.99,
        }

        with self.database.session() as connection:
            stats = dict(connection.execute(aggregates, params).fetchone())
            count = int(stats["count"])
            values = self._percentiles(
                connection, where, params, count, tuple(percentiles.values())
            )
        for key, quantile in percentiles.items():
            stats[key] = values[quantile]
        return stats

    def zone_stats(
        self, filters: ListingFilters | None = None, *, total: int | None = None
    ) -> list[dict[str, Any]]:
        """Per-zone aggregates, busiest zone first. Rows without a zone are skipped.

        Same trade-off as :meth:`_percentiles`, for the same reason. The SQL
        GROUP BY leans on the (zone, price, size_m2) covering index and wins
        when nothing else is filtered; but it needs one follow-up query per
        zone for the median, and each of those re-runs the filter. Under a
        bounding box that cost dominates -- 807 ms against 6,658 matching rows
        -- so below the threshold everything is read once, sorted by zone and
        price, and folded in Python instead.

        Pass ``total`` when the caller already knows the row count, to save a
        COUNT(*).
        """
        if total is None:
            total = self.count(filters)

        def compute() -> list[dict[str, Any]]:
            if total <= PERCENTILE_SCAN_THRESHOLD:
                return self._zone_stats_scanned(filters)
            return self._zone_stats_grouped(filters)

        return stats_cache.get_or_compute(cache_key("zones", filters), compute)

    def _zone_stats_scanned(self, filters: ListingFilters | None) -> list[dict[str, Any]]:
        """One ordered read, folded per zone in Python."""
        where, params = build_where(filters, "zone IS NOT NULL")
        query = "SELECT zone, price, size_m2 FROM listings" + where + " ORDER BY zone, price"
        with self.database.session() as connection:
            rows = connection.execute(query, params).fetchall()

        results: list[dict[str, Any]] = []
        for zone, group in groupby(rows, key=lambda row: row["zone"]):
            prices: list[float] = []
            per_m2: list[float] = []
            for row in group:
                prices.append(float(row["price"]))
                if row["size_m2"] and row["size_m2"] > 0:
                    per_m2.append(float(row["price"]) / float(row["size_m2"]))

            count = len(prices)
            results.append(
                {
                    "zone": zone,
                    "count": count,
                    # prices arrives sorted, so min/max/median are positional.
                    "avg_price": sum(prices) / count,
                    "min_price": prices[0],
                    "max_price": prices[-1],
                    "median_price": prices[math.ceil(count * 0.5) - 1],
                    "avg_price_per_m2": sum(per_m2) / len(per_m2) if per_m2 else None,
                }
            )

        results.sort(key=lambda entry: (-entry["count"], entry["zone"]))
        return results

    def _zone_stats_grouped(self, filters: ListingFilters | None) -> list[dict[str, Any]]:
        """SQL GROUP BY plus one median query per zone."""
        where, params = build_where(filters, "zone IS NOT NULL")
        query = (
            "SELECT zone, COUNT(*) AS count,"
            " AVG(price) AS avg_price,"
            " MIN(price) AS min_price,"
            " MAX(price) AS max_price,"
            " AVG(CASE WHEN size_m2 > 0 THEN price / size_m2 END) AS avg_price_per_m2"
            " FROM listings" + where + " GROUP BY zone ORDER BY COUNT(*) DESC, zone"
        )
        # Binary comparison, not COLLATE NOCASE: the value comes straight from
        # the GROUP BY above, so it matches exactly what is stored -- and it
        # lets the (zone, price, size_m2) index supply the ORDER BY for free,
        # instead of falling back to idx_listings_zone plus a temp b-tree.
        zone_where, zone_params = build_where(filters, "zone = ?")

        results: list[dict[str, Any]] = []
        with self.database.session() as connection:
            for row in connection.execute(query, params).fetchall():
                entry = dict(row)
                entry["median_price"] = self._percentiles(
                    connection,
                    zone_where,
                    [*zone_params, entry["zone"]],
                    int(entry["count"]),
                    (0.50,),
                )[0.50]
                results.append(entry)
        return results

    @staticmethod
    def _percentiles(
        connection: sqlite3.Connection,
        where: str,
        params: list[Any],
        count: int,
        quantiles: tuple[float, ...],
    ) -> dict[float, float | None]:
        """Nearest-rank percentiles: the cheapest price whose rank reaches each quantile.

        Two strategies, because neither wins everywhere (median of 3 runs over
        the 150k-row dataset):

        =================  ==================  ================
        filter             one query per rank  one sorted fetch
        =================  ==================  ================
        none                          63 ms            110 ms
        zona=Barcelona               378 ms            159 ms
        bounding box                 197 ms             27 ms
        =================  ==================  ================

        Unfiltered, ``ORDER BY price`` walks ``idx_listings_price_size`` and
        ``LIMIT 1 OFFSET k`` stops early, so a query per rank is cheapest. Once
        a filter the price index cannot serve is present, every one of those
        queries re-sorts the same rows; fetching them sorted once and slicing
        in Python pays for itself. The row count is the switch.
        """
        if count <= 0:
            return dict.fromkeys(quantiles)

        offsets = {
            quantile: max(0, min(math.ceil(count * quantile) - 1, count - 1))
            for quantile in quantiles
        }
        order_by = " ORDER BY price"

        if count <= PERCENTILE_SCAN_THRESHOLD:
            prices = [
                row[0]
                for row in connection.execute(
                    "SELECT price FROM listings" + where + order_by, params
                )
            ]
            if not prices:
                return dict.fromkeys(quantiles)
            return {
                quantile: float(prices[min(offset, len(prices) - 1)])
                for quantile, offset in offsets.items()
            }

        results: dict[float, float | None] = {}
        for quantile, offset in offsets.items():
            row = connection.execute(
                "SELECT price FROM listings" + where + order_by + " LIMIT 1 OFFSET ?",
                [*params, offset],
            ).fetchone()
            results[quantile] = float(row[0]) if row else None
        return results

    def price_histogram(
        self,
        filters: ListingFilters | None = None,
        *,
        lower: float,
        upper: float,
        bins: int,
    ) -> dict[int, int]:
        """Cached. See _price_histogram for the query."""
        return stats_cache.get_or_compute(
            cache_key("histogram", filters, lower=lower, upper=upper, bins=bins),
            lambda: self._price_histogram(filters, lower=lower, upper=upper, bins=bins),
        )

    def _price_histogram(
        self,
        filters: ListingFilters | None,
        *,
        lower: float,
        upper: float,
        bins: int,
    ) -> dict[int, int]:
        """Count listings per equal-width price bucket between ``lower`` and ``upper``.

        Bucket ``bins`` is the open-ended overflow: everything at or above
        ``upper``. Returns only non-empty buckets; the caller fills the gaps.
        """
        if bins < 1 or upper <= lower:
            raise ValueError("price_histogram needs bins >= 1 and upper > lower")

        width = (upper - lower) / bins
        where, params = build_where(filters)
        query = (
            "SELECT MIN(CAST((price - ?) / ? AS INTEGER), ?) AS bucket, COUNT(*) AS count"
            " FROM listings" + where + " GROUP BY bucket ORDER BY bucket"
        )
        with self.database.session() as connection:
            rows = connection.execute(query, [lower, width, bins, *params]).fetchall()
        return {int(row["bucket"]): int(row["count"]) for row in rows}

    # -- row conversion ----------------------------------------------------

    @staticmethod
    def _to_row(listing: Listing) -> tuple[object, ...]:
        return (
            listing.global_id,
            listing.id,
            listing.source,
            listing.title,
            listing.url,
            listing.operation.value,
            listing.property_type.value,
            listing.price,
            listing.size_m2,
            listing.rooms,
            listing.latitude,
            listing.longitude,
            listing.address,
            listing.zone,
            listing.ingested_at.isoformat(),
            listing.bathrooms,
            listing.floor,
            listing.year_built,
            None if listing.condition is None else listing.condition.value,
            listing.distance_to_center_km,
            listing.distance_to_metro_km,
            # One column per amenity, in the order AMENITY_COLUMNS declares.
            *(int(amenity in listing.amenities) for amenity in AMENITY_COLUMNS),
        )

    @staticmethod
    def _to_listing(row: sqlite3.Row) -> Listing:
        return Listing(
            id=row["id"],
            source=row["source"],
            title=row["title"],
            url=row["url"],
            operation=Operation(row["operation"]),
            property_type=PropertyType(row["property_type"]),
            price=row["price"],
            size_m2=row["size_m2"],
            rooms=row["rooms"],
            latitude=row["latitude"],
            longitude=row["longitude"],
            address=row["address"],
            zone=row["zone"],
            ingested_at=datetime.fromisoformat(row["ingested_at"]),
            expected_price=row["expected_price"],
            price_deviation=row["price_deviation"],
            bathrooms=row["bathrooms"],
            floor=row["floor"],
            year_built=row["year_built"],
            condition=None if row["condition"] is None else Condition(row["condition"]),
            distance_to_center_km=row["distance_to_center_km"],
            distance_to_metro_km=row["distance_to_metro_km"],
            amenities=[
                amenity
                for amenity, column in AMENITY_COLUMNS.items()
                if row[column]
            ],
        )
