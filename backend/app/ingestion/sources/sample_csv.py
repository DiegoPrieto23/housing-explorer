"""Reference implementation of :class:`ListingSource` backed by a local CSV.

Exists so the skeleton has something to run end to end. It is also the shortest
example of the contract a real source has to honour.
"""

from __future__ import annotations

import csv
from pathlib import Path

from app.config import get_settings
from app.ingestion.base import ListingSource, ListingSourceError
from app.ingestion.registry import register_source
from app.models.listing import Listing, Operation, PropertyType


@register_source
class SampleCsvSource(ListingSource):
    """Reads adverts from a semicolon-free UTF-8 CSV in the data directory."""

    name = "sample_csv"

    def __init__(self, path: Path | str | None = None) -> None:
        settings = get_settings()
        self.path = Path(path) if path else settings.data_dir / "sample_listings.csv"

    def health_check(self) -> bool:
        return self.path.is_file()

    def fetch_listings(self) -> list[Listing]:
        if not self.path.is_file():
            raise ListingSourceError(f"CSV not found: {self.path}")

        listings: list[Listing] = []
        with self.path.open(encoding="utf-8", newline="") as handle:
            for row_number, row in enumerate(csv.DictReader(handle), start=2):
                try:
                    listings.append(self._to_listing(row))
                except (KeyError, ValueError) as exc:
                    raise ListingSourceError(
                        f"{self.path}:{row_number} could not be normalised: {exc}"
                    ) from exc
        return listings

    def _to_listing(self, row: dict[str, str]) -> Listing:
        """Map one raw CSV row onto the normalised schema."""
        return Listing(
            id=row["id"].strip(),
            source=self.name,
            title=row["title"].strip(),
            url=_opt(row.get("url")),
            operation=Operation(row["operation"].strip().lower()),
            property_type=PropertyType(row.get("property_type", "otro").strip().lower()),
            price=float(row["price"]),
            size_m2=_opt_float(row.get("size_m2")),
            rooms=_opt_int(row.get("rooms")),
            latitude=_opt_float(row.get("latitude")),
            longitude=_opt_float(row.get("longitude")),
            address=_opt(row.get("address")),
            zone=_opt(row.get("zone")),
        )


def _opt(value: str | None) -> str | None:
    value = (value or "").strip()
    return value or None


def _opt_float(value: str | None) -> float | None:
    raw = _opt(value)
    return float(raw) if raw is not None else None


def _opt_int(value: str | None) -> int | None:
    raw = _opt(value)
    return int(float(raw)) if raw is not None else None
