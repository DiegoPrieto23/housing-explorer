"""Ingestion of the idealista18 static dataset.

`idealista18 <https://github.com/paezha/idealista18>`_ is an open data product
(ODbL v1.0) with 2018 real-estate listings for Madrid, Barcelona and Valencia,
published by Rey-Blanco, Arbues, Lopez and Paez (https://doi.org/10.1177/23998083241242844).

It ships as an R package, so the raw ``.rda`` files are exported to CSV once by
``scripts/export_idealista18.R`` and this source reads the result. No R at
runtime.

Two properties of the dataset drive the mapping below:

* It contains **sale listings only**, so every row becomes ``Operation.SALE``.
* The same dwelling can appear in several 2018 quarters under one ``ASSETID``.
  By default the ``ASSETID`` is the listing id, so the repository upsert keeps
  the last row read; the exporter sorts by ``PERIOD`` ascending, which makes
  that the most recent quarter. Pass ``keep_all_periods=True`` to store every
  quarter separately instead.
"""

from __future__ import annotations

import csv
import gzip
import logging
from collections.abc import Iterator
from contextlib import contextmanager
from pathlib import Path
from typing import IO, Any

from pydantic import ValidationError

from app.config import get_settings
from app.ingestion.base import ListingSource, ListingSourceError
from app.ingestion.registry import register_source
from app.models.listing import Amenity, Condition, Listing, Operation, PropertyType

logger = logging.getLogger(__name__)

#: Columns the normaliser cannot work without.
REQUIRED_COLUMNS = frozenset(
    {"ASSETID", "PERIOD", "PRICE", "CONSTRUCTEDAREA", "ROOMNUMBER", "LATITUDE", "LONGITUDE"}
)

#: Generous bounding box for Spain, Canary Islands included. Catches swapped or
#: garbled coordinates without rejecting legitimate ones.
SPAIN_BBOX = (27.4, -18.6, 44.1, 4.6)  # min_lat, min_lon, max_lat, max_lon

#: Filenames tried in the data directory, in order, when no path is given.
DEFAULT_FILENAMES = (
    "idealista18_sale.parquet",
    "idealista18_sale.csv",
    "idealista18_sale.csv.gz",
)

_CSV_SUFFIXES = {".csv", ".gz"}


def _clean(value: Any) -> str:
    """Normalise a raw cell to a stripped string; R writes NA as an empty field."""
    if value is None:
        return ""
    text = str(value).strip()
    return "" if text.upper() in {"", "NA", "NAN", "NULL", "NONE"} else text


def _to_float(value: Any) -> float | None:
    text = _clean(value)
    if not text:
        return None
    try:
        number = float(text)
    except ValueError:
        return None
    return None if number != number else number  # drop NaN


def _to_int(value: Any) -> int | None:
    number = _to_float(value)
    return None if number is None else int(number)


#: Columna del dataset -> extra del modelo. Las que el dataset trae y el modelo
#: no contempla (orientaciones, plaza de garaje incluida en el precio) se quedan
#: fuera a propósito: no son criterios de búsqueda de un portal.
_AMENITY_FLAGS: dict[str, Amenity] = {
    "HASLIFT": Amenity.LIFT,
    "HASTERRACE": Amenity.TERRACE,
    "HASPARKINGSPACE": Amenity.PARKING,
    "HASAIRCONDITIONING": Amenity.AIR_CONDITIONING,
    "HASSWIMMINGPOOL": Amenity.POOL,
    "HASDOORMAN": Amenity.DOORMAN,
    "HASGARDEN": Amenity.GARDEN,
    "HASBOXROOM": Amenity.STORAGE,
    "HASWARDROBE": Amenity.WARDROBES,
}

#: Los tres BUILTTYPEID del dataset, que son excluyentes entre sí (comprobado:
#: el 100 % de las filas suman exactamente 1).
_CONDITION_FLAGS: dict[str, Condition] = {
    "BUILTTYPEID_1": Condition.NEW,
    "BUILTTYPEID_2": Condition.NEEDS_WORK,
    "BUILTTYPEID_3": Condition.GOOD,
}

#: Un edificio anterior a esto en un dataset de 2018 es un error de captura, no
#: una catedral en venta.
_EARLIEST_YEAR = 1500


def _amenities(row: dict[str, Any]) -> list[Amenity]:
    return [
        amenity for column, amenity in _AMENITY_FLAGS.items() if _to_int(row.get(column)) == 1
    ]


def _condition(row: dict[str, Any]) -> Condition | None:
    for column, condition in _CONDITION_FLAGS.items():
        if _to_int(row.get(column)) == 1:
            return condition
    return None


def _year(row: dict[str, Any]) -> int | None:
    for column in ("CADCONSTRUCTIONYEAR", "CONSTRUCTIONYEAR"):
        year = _to_int(row.get(column))
        if year is not None and _EARLIEST_YEAR <= year <= 2100:
            return year
    return None


def _is_true(value: Any) -> bool:
    """The dataset encodes booleans as 0/1 integers."""
    return _to_float(value) == 1.0


def _property_type(row: dict[str, Any]) -> PropertyType:
    """The dataset has no type column, only studio/duplex flags on dwellings."""
    if _is_true(row.get("ISSTUDIO")):
        return PropertyType.STUDIO
    if _is_true(row.get("ISDUPLEX")):
        return PropertyType.DUPLEX
    return PropertyType.FLAT


def _title(property_type: PropertyType, rooms: int | None, size_m2: float | None, zone: str) -> str:
    """The dataset carries no advert text, so build a readable label."""
    parts = [property_type.value.capitalize()]
    if rooms:
        parts.append(f"de {rooms} hab.")
    if size_m2:
        parts.append(f"y {size_m2:g} m2" if rooms else f"de {size_m2:g} m2")
    if zone:
        parts.append(f"en {zone}")
    return " ".join(parts)


@register_source
class StaticDatasetSource(ListingSource):
    """Reads the exported idealista18 file and normalises it to :class:`Listing`.

    Accepts CSV, gzipped CSV or Parquet. Parquet needs ``pyarrow`` installed
    (``pip install pyarrow``); CSV needs nothing beyond the standard library.
    """

    name = "idealista18"

    def __init__(self, path: Path | str | None = None, *, keep_all_periods: bool = False) -> None:
        self.keep_all_periods = keep_all_periods
        self._explicit_path = Path(path) if path else None

    # -- file resolution -------------------------------------------------

    @property
    def path(self) -> Path:
        """The dataset file, resolved lazily so construction never fails."""
        if self._explicit_path is not None:
            return self._explicit_path

        data_dir = get_settings().data_dir
        for filename in DEFAULT_FILENAMES:
            candidate = data_dir / filename
            if candidate.is_file():
                return candidate
        return data_dir / DEFAULT_FILENAMES[1]  # the CSV, for the error message

    def health_check(self) -> bool:
        return self.path.is_file()

    # -- ListingSource contract ------------------------------------------

    def fetch_listings(self) -> list[Listing]:
        """Read the whole dataset into memory.

        Prefer :meth:`iter_listings` for the full file: it is ~190k rows.
        """
        return list(self.iter_listings())

    def iter_listings(self) -> Iterator[Listing]:
        """Stream normalised listings, discarding rows that cannot be trusted."""
        path = self.path
        if not path.is_file():
            raise ListingSourceError(
                f"Dataset not found: {path}. Export it first with "
                "`Rscript scripts/export_idealista18.R`."
            )

        self.reset_stats()
        stats = self.stats

        for row in self._read_rows(path):
            stats.read += 1
            listing = self._to_listing(row)
            if listing is not None:
                stats.emitted += 1
                yield listing

        logger.info("%s: %s", path.name, stats.summary())
        for reason, example in stats.samples.items():
            logger.debug("  example of %r: %s", reason, example)

    # -- readers ----------------------------------------------------------

    def _read_rows(self, path: Path) -> Iterator[dict[str, Any]]:
        if path.suffix.lower() == ".parquet":
            yield from self._read_parquet(path)
        else:
            yield from self._read_csv(path)

    @contextmanager
    def _open_text(self, path: Path) -> Iterator[IO[str]]:
        opener = gzip.open if path.suffix.lower() == ".gz" else open
        handle = opener(path, mode="rt", encoding="utf-8-sig", newline="")
        try:
            yield handle  # type: ignore[misc]
        finally:
            handle.close()

    def _read_csv(self, path: Path) -> Iterator[dict[str, Any]]:
        with self._open_text(path) as handle:
            reader = csv.DictReader(handle)
            self._check_columns(reader.fieldnames, path)
            yield from reader

    def _read_parquet(self, path: Path) -> Iterator[dict[str, Any]]:
        try:
            import pyarrow.parquet as pq
        except ImportError as exc:  # pragma: no cover - depends on the environment
            raise ListingSourceError(
                f"Reading {path.name} needs pyarrow. Install it (`pip install pyarrow`) "
                "or export the dataset as CSV instead."
            ) from exc

        try:
            parquet_file = pq.ParquetFile(path)
        except Exception as exc:
            raise ListingSourceError(f"{path} is not readable as Parquet: {exc}") from exc

        self._check_columns(parquet_file.schema_arrow.names, path)
        for batch in parquet_file.iter_batches(batch_size=8192):
            yield from batch.to_pylist()

    def _check_columns(self, columns: list[str] | None, path: Path) -> None:
        if not columns:
            raise ListingSourceError(f"{path} has no header row")
        missing = REQUIRED_COLUMNS - set(columns)
        if missing:
            raise ListingSourceError(
                f"{path} is missing required column(s): {', '.join(sorted(missing))}. "
                "Re-export it with scripts/export_idealista18.R."
            )

    # -- normalisation -----------------------------------------------------

    def _to_listing(self, row: dict[str, Any]) -> Listing | None:
        """Map one raw row onto :class:`Listing`, or record why it was dropped."""
        stats = self.stats

        asset_id = _clean(row.get("ASSETID"))
        if not asset_id:
            stats.discard("missing ASSETID")
            return None

        price = _to_float(row.get("PRICE"))
        if price is None or price <= 0:
            stats.discard("missing or non-positive price", f"ASSETID={asset_id}")
            return None

        latitude = _to_float(row.get("LATITUDE"))
        longitude = _to_float(row.get("LONGITUDE"))
        if latitude is None or longitude is None:
            stats.discard("missing coordinates", f"ASSETID={asset_id}")
            return None

        min_lat, min_lon, max_lat, max_lon = SPAIN_BBOX
        if not (min_lat <= latitude <= max_lat and min_lon <= longitude <= max_lon):
            stats.discard(
                "coordinates outside Spain",
                f"ASSETID={asset_id} lat={latitude} lon={longitude}",
            )
            return None

        period = _clean(row.get("PERIOD"))
        listing_id = f"{asset_id}-{period}" if self.keep_all_periods and period else asset_id

        zone = _clean(row.get("CITY")) or None
        property_type = _property_type(row)
        rooms = _to_int(row.get("ROOMNUMBER"))
        size_m2 = _to_float(row.get("CONSTRUCTEDAREA"))

        # Negative or zero areas/rooms are noise, not data: null them rather
        # than dropping an otherwise usable row.
        if size_m2 is not None and size_m2 <= 0:
            size_m2 = None
        if rooms is not None and rooms < 0:
            rooms = None

        try:
            return Listing(
                id=listing_id,
                source=self.name,
                title=_title(property_type, rooms, size_m2, zone or ""),
                url=None,  # the dataset carries no advert URLs
                operation=Operation.SALE,  # idealista18 is sale-only
                property_type=property_type,
                price=price,
                size_m2=size_m2,
                rooms=rooms,
                latitude=latitude,
                longitude=longitude,
                address=None,
                zone=zone,
                bathrooms=_to_int(row.get("BATHNUMBER")),
                floor=_to_int(row.get("FLOORCLEAN")),
                # CADCONSTRUCTIONYEAR viene del catastro y está en todas las
                # filas; CONSTRUCTIONYEAR falta en la mitad y llega a traer
                # valores imposibles (año 7). Se prefiere el catastral y el otro
                # solo cubre los huecos, ya filtrado por el rango del modelo.
                year_built=_year(row),
                condition=_condition(row),
                distance_to_center_km=_to_float(row.get("DISTANCE_TO_CITY_CENTER")),
                distance_to_metro_km=_to_float(row.get("DISTANCE_TO_METRO")),
                amenities=_amenities(row),
            )
        except ValidationError as exc:
            stats.discard("failed schema validation", f"ASSETID={asset_id}: {exc.errors()[:1]}")
            return None
