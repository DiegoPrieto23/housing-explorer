"""SQLite connection handling and schema bootstrap."""

from __future__ import annotations

import sqlite3
from collections.abc import Iterator
from contextlib import contextmanager
from pathlib import Path

from app.geometry import point_in_polygon

TABLE_SCHEMA = """
CREATE TABLE IF NOT EXISTS listings (
    global_id     TEXT PRIMARY KEY,
    id            TEXT NOT NULL,
    source        TEXT NOT NULL,
    title         TEXT NOT NULL,
    url           TEXT,
    operation     TEXT NOT NULL,
    property_type TEXT NOT NULL,
    price         REAL NOT NULL,
    size_m2       REAL,
    rooms         INTEGER,
    latitude      REAL,
    longitude     REAL,
    address       TEXT,
    zone          TEXT,
    ingested_at   TEXT NOT NULL,
    -- Derived, not ingested: written by `python -m scripts.score_listings`, which
    -- runs the price model over the source dataset. NULL means "not scored" and
    -- is a first-class state -- a listing with no surface area cannot be judged.
    expected_price   REAL,
    price_deviation  REAL,
    -- Características que casi cualquier portal reporta. Van como columnas y no
    -- como un JSON o una máscara de bits porque son criterios de filtro: SQLite
    -- puede indexarlas, y el SQL se lee.
    bathrooms        INTEGER,
    floor            INTEGER,
    year_built       INTEGER,
    condition        TEXT,
    distance_to_center_km REAL,
    distance_to_metro_km  REAL,
    -- Barrio, resuelto geométricamente contra los polígonos del dataset por
    -- `python -m scripts.assign_neighbourhoods`. Derivado, como el precio
    -- estimado: la fuente no lo trae. NULL significa "fuera de todos los
    -- polígonos", que es un estado legítimo y frecuente -- el dataset cubre el
    -- área metropolitana y los polígonos paran en el término municipal.
    --
    -- Se guardan el id y el nombre. El id es la clave del dataset y lo que
    -- filtra la API, porque los nombres no son únicos: "Sant Antoni" existe en
    -- Barcelona y en Valencia. El nombre viaja al lado para que una consulta o
    -- un `SELECT` a mano se lean sin tener que cruzar con el GeoJSON.
    neighbourhood_id      TEXT,
    neighbourhood         TEXT,
    has_lift             INTEGER,
    has_terrace          INTEGER,
    has_parking          INTEGER,
    has_air_conditioning INTEGER,
    has_pool             INTEGER,
    has_doorman          INTEGER,
    has_garden           INTEGER,
    has_storage          INTEGER,
    has_wardrobes        INTEGER,
    UNIQUE (source, id)
);
"""

# Separado de la tabla a propósito. init_schema añade las columnas que falten
# entre una cosa y la otra, y idx_listings_deviation es un índice parcial sobre
# price_deviation: crearlo antes de que la columna exista falla.
INDEX_SCHEMA = """
CREATE INDEX IF NOT EXISTS idx_listings_operation ON listings (operation);
CREATE INDEX IF NOT EXISTS idx_listings_source    ON listings (source);
CREATE INDEX IF NOT EXISTS idx_listings_bbox      ON listings (latitude, longitude);

-- Serves `zone = ? COLLATE NOCASE`, which a BINARY index cannot.
CREATE INDEX IF NOT EXISTS idx_listings_zone      ON listings (zone COLLATE NOCASE);

-- Composite filter indexes. The panel never sends one condition at a time: it
-- sends a price range plus some combination of zone, rooms and property type.
-- A narrow index on each column separately cannot help with that -- SQLite uses
-- one index per table, so it would narrow by price and then read the table for
-- every surviving row. Measured on 150k rows, zone+price+rooms went from 467ms
-- to 2.9ms once one index covered the whole predicate.
--
-- The trailing columns are there to make the index *covering*: with price,
-- rooms and size_m2 all present, a COUNT or an average never touches the table.
--
-- `operation` sits at the *tail*, never at the head. It has one distinct value in
-- the current dataset and two in the worst case, so as a prefix it discards
-- nothing while making the index unusable whenever the caller omits it; at the
-- tail it costs no extra pages and keeps the index covering when the caller does
-- send it. Measured: zone+price+rooms+operation went from 240ms to 12ms, and
-- operation+price from 796ms to 17ms, for the same file size.
-- Narrow companion to idx_listings_price_filter, and not redundant with it. The
-- unfiltered aggregates scan the whole price index; the wide version carries two
-- more columns per entry, which is more pages to read for a scan that needs none
-- of them. Keeping both took /stats without filters from 89ms back to 54ms and
-- the per-zone statistics from 141ms to 107ms, at no measurable cost in file
-- size -- SQLite reuses the pages freed by the indexes this set replaced.
CREATE INDEX IF NOT EXISTS idx_listings_price_size ON listings (price, size_m2);

CREATE INDEX IF NOT EXISTS idx_listings_price_filter
    ON listings (price, rooms, size_m2, operation);
CREATE INDEX IF NOT EXISTS idx_listings_zone_filter
    ON listings (zone COLLATE NOCASE, price, rooms, size_m2, operation);
CREATE INDEX IF NOT EXISTS idx_listings_type_filter
    ON listings (property_type, price, rooms, size_m2, operation);

-- The same zone key again, with the default BINARY collation. Not redundant:
-- _zone_stats_grouped looks each zone up by the exact value it just read from a
-- GROUP BY, and a NOCASE index cannot serve a BINARY equality. Dropping this one
-- and keeping only the NOCASE version took the per-zone statistics from 109ms to
-- 2.7s, because every median query fell back to a full scan and sort.
CREATE INDEX IF NOT EXISTS idx_listings_zone_binary ON listings (zone, price, size_m2);

-- Superseded by the three above, which lead with the same columns and carry
-- more. Dropped rather than left behind: every extra index is paid for on each
-- insert, and the loader writes 150k rows.
DROP INDEX IF EXISTS idx_listings_type;
DROP INDEX IF EXISTS idx_listings_rooms;
DROP INDEX IF EXISTS idx_listings_size;

-- Both superseded by idx_listings_price_filter / idx_listings_zone_filter.
-- idx_listings_zone_price was worse than useless: it indexed `zone` with the
-- default BINARY collation, so `zone = ? COLLATE NOCASE` could not use it and
-- fell back to the narrow idx_listings_zone, then read the table for all 75k
-- Madrid rows.
DROP INDEX IF EXISTS idx_listings_price;
DROP INDEX IF EXISTS idx_listings_zone_price;

-- Los extras son criterios muy poco selectivos por separado (el 73 % tiene
-- ascensor), así que no llevan índice propio: siempre acompañan a un filtro de
-- precio o de zona, que es el que acota, y comprobar un entero en las filas
-- supervivientes es más barato que mantener nueve índices en cada inserción.
-- La distancia sí, porque un radio pequeño alrededor del centro descarta mucho.
CREATE INDEX IF NOT EXISTS idx_listings_center ON listings (distance_to_center_km);

-- Sirve el filtro por barrio, que es el más selectivo de todos: 277 valores
-- sobre 150k filas, así que uno solo deja unos cientos. Las tres columnas de
-- cola lo hacen cubridor para el `COUNT(*)` y para las medias de precio y de
-- €/m² que pide el resumen del barrio, que se piden juntos y siempre.
CREATE INDEX IF NOT EXISTS idx_listings_neighbourhood
    ON listings (neighbourhood_id, price, size_m2) WHERE neighbourhood_id IS NOT NULL;

-- Serves the bargain filter and ordering by deviation. Partial, because only
-- scored rows can ever match: it indexes ~1/3 of the table instead of all of it.
CREATE INDEX IF NOT EXISTS idx_listings_deviation
    ON listings (price_deviation) WHERE price_deviation IS NOT NULL;

-- Covering index for the zone facets, which group by zone and average the
-- coordinates to build each city's box. Without it that GROUP BY falls back to
-- idx_listings_zone_price and has to visit the table for every row: 948ms
-- against 90ms over 150k rows, and far worse under concurrent load, where it
-- was measured taking 20s while the map and stats queries ran alongside it.
CREATE INDEX IF NOT EXISTS idx_listings_zone_geo ON listings (zone, latitude, longitude);

-- Covering index for the map aggregation, which groups every matching row into
-- a lat/lon grid and averages the price. Reading the index instead of the table
-- took the unfiltered, country-wide view from 1734ms to 1099ms over 150k rows.
-- The GROUP BY still needs a temp b-tree; that is the remaining second.
CREATE INDEX IF NOT EXISTS idx_listings_map ON listings (latitude, longitude, price);

-- Serves the default ORDER BY of every listing page. Without it SQLite scans the
-- table and sorts all 150k rows in a temp b-tree just to return the first 100.
--
-- latitude and longitude ride along so that a page *inside a bounding box* can
-- be answered by walking this index in order and stopping at 24 matches, instead
-- of collecting every row in the box and sorting them. That is the difference
-- between 178ms and 7.6ms for a city-sized viewport. See ListingRepository.list
-- for the `likely()` hint that makes SQLite actually choose it.
CREATE INDEX IF NOT EXISTS idx_listings_recent
    ON listings (ingested_at DESC, global_id, latitude, longitude);
"""


#: Bumped whenever an existing index or column changes shape rather than being
#: added. See Database._migrate.
SCHEMA_VERSION = 5


class Database:
    """Thin wrapper over sqlite3 that owns the file path and the schema."""

    def __init__(self, path: Path | str) -> None:
        self.path = Path(path)
        self._pinned: sqlite3.Connection | None = None

    def connect(self) -> sqlite3.Connection:
        if str(self.path) != ":memory:":
            self.path.parent.mkdir(parents=True, exist_ok=True)
        connection = sqlite3.connect(self.path, check_same_thread=False)
        connection.row_factory = sqlite3.Row
        # foreign_keys is per-connection, so it has to be set every time.
        # journal_mode is not: WAL is stored in the database header, and asking
        # for it on every connect cost ~8 of the ~12ms each open took. It is set
        # once in init_schema() instead.
        connection.execute("PRAGMA foreign_keys = ON")
        # Lets the "draw your area" filter run inside SQL instead of in Python
        # after the fact, which is what keeps COUNT(*) honest. Declared
        # deterministic so SQLite may cache and reorder the calls.
        connection.create_function(
            "point_in_polygon", 3, point_in_polygon, deterministic=True
        )
        return connection

    @contextmanager
    def session(self) -> Iterator[sqlite3.Connection]:
        """Transaction scope: commits on success, rolls back on error.

        Inside a :meth:`bulk` block the pinned connection is reused and left
        open; the commit/rollback semantics are identical either way.
        """
        if self._pinned is not None:
            try:
                yield self._pinned
                self._pinned.commit()
            except Exception:
                self._pinned.rollback()
                raise
            return

        connection = self.connect()
        try:
            yield connection
            connection.commit()
        except Exception:
            connection.rollback()
            raise
        finally:
            connection.close()

    @contextmanager
    def bulk(self) -> Iterator[sqlite3.Connection]:
        """Share one connection across many sessions, for bulk loads.

        Reopening SQLite once per batch means re-running its PRAGMAs and
        checkpointing the WAL on every close. Pinning one connection measured
        ~1.4x faster on the 190k-row idealista18 load (median of 5 interleaved
        runs of 40k rows: 11.8k vs 8.2k rows/s). Batches still commit
        individually, so an interrupted load leaves its finished batches on
        disk.

        Not re-entrant across threads: intended for the one-off load script.
        """
        if self._pinned is not None:
            yield self._pinned
            return

        connection = self.connect()
        self._pinned = connection
        try:
            yield connection
        finally:
            self._pinned = None
            connection.close()

    #: Columns added after the first release. `CREATE TABLE IF NOT EXISTS` leaves
    #: an existing table alone, so a database created before them would keep
    #: working right up to the first query that mentions one.
    _ADDED_COLUMNS = {
        "expected_price": "REAL",
        "price_deviation": "REAL",
        "bathrooms": "INTEGER",
        "floor": "INTEGER",
        "year_built": "INTEGER",
        "condition": "TEXT",
        "distance_to_center_km": "REAL",
        "distance_to_metro_km": "REAL",
        "has_lift": "INTEGER",
        "has_terrace": "INTEGER",
        "has_parking": "INTEGER",
        "has_air_conditioning": "INTEGER",
        "has_pool": "INTEGER",
        "has_doorman": "INTEGER",
        "has_garden": "INTEGER",
        "has_storage": "INTEGER",
        "has_wardrobes": "INTEGER",
        "neighbourhood_id": "TEXT",
        "neighbourhood": "TEXT",
    }

    def init_schema(self) -> None:
        """Create the tables and indexes, and put the database in WAL mode."""
        with self.session() as connection:
            connection.execute("PRAGMA journal_mode = WAL")
            connection.executescript(TABLE_SCHEMA)
            # Between the two: an index over a column added later cannot be
            # created until the column is there.
            self._add_missing_columns(connection)
            # Definition changes go here, between adding columns and creating
            # indexes: a redefined index has to be dropped before INDEX_SCHEMA
            # can recreate it.
            migrated = self._migrate(connection)

            connection.executescript(INDEX_SCHEMA)

            # Without table statistics the planner guesses, and with several
            # overlapping composite indexes a guess is often the wrong one. Only
            # after a migration or on a database that has never been analysed:
            # ANALYZE over 150k rows is not something to pay for on every boot.
            never_analysed = connection.execute(
                "SELECT COUNT(*) FROM sqlite_master WHERE name = 'sqlite_stat1'"
            ).fetchone()[0] == 0
            if migrated or never_analysed:
                connection.execute("ANALYZE")

    def _migrate(self, connection: sqlite3.Connection) -> bool:
        """Apply definition changes an `IF NOT EXISTS` cannot. Returns True if it did.

        `CREATE INDEX IF NOT EXISTS` is a no-op when an index of that name
        exists, *whatever its definition*. So an index that gains a column keeps
        its old shape forever on any database that already had it, and the only
        symptom is that queries stay slow. Dropping by name first is the fix, and
        `PRAGMA user_version` is what keeps it from happening on every boot.
        """
        version = connection.execute("PRAGMA user_version").fetchone()[0]
        if version >= SCHEMA_VERSION:
            return False

        if version < 2:
            # v2: the composite filter indexes gained a trailing `operation`.
            for name in (
                "idx_listings_price_filter",
                "idx_listings_zone_filter",
                "idx_listings_type_filter",
            ):
                connection.execute(f"DROP INDEX IF EXISTS {name}")

        if version < 3:
            # v3: idx_listings_recent gained latitude and longitude.
            connection.execute("DROP INDEX IF EXISTS idx_listings_recent")

        # v4 y v5 solo añaden columnas, que _add_missing_columns ya ha creado
        # antes de llegar aquí. Se anotan igualmente para que la versión cuente
        # la historia completa y la siguiente migración sepa desde dónde parte.
        #
        # Ojo con v5: las columnas de barrio nacen vacías y hay que rellenarlas
        # con `python -m scripts.assign_neighbourhoods`. Una base migrada pero
        # sin ese paso responde correctamente a todo salvo al filtro por barrio,
        # que no encuentra nada. El script lo dice al arrancar.

        connection.execute(f"PRAGMA user_version = {SCHEMA_VERSION}")
        return True

    def _add_missing_columns(self, connection: sqlite3.Connection) -> None:
        """Bring an older database up to the current column set.

        Deliberately not a migration framework: this project has one table and
        adds columns to it, and `ALTER TABLE ADD COLUMN` on SQLite is an O(1)
        metadata change. The moment a column needs backfilling or renaming, this
        should be replaced rather than extended.
        """
        existing = {row["name"] for row in connection.execute("PRAGMA table_info(listings)")}
        for column, kind in self._ADDED_COLUMNS.items():
            if column not in existing:
                connection.execute(f"ALTER TABLE listings ADD COLUMN {column} {kind}")
