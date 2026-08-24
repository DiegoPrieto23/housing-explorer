"""Fetch from a source and hand the batches to storage. No business rules yet."""

from __future__ import annotations

import logging
from dataclasses import dataclass, field

from app.ingestion.base import ListingSource
from app.ingestion.registry import get_source_class
from app.models.listing import Listing
from app.storage.repository import ListingRepository

logger = logging.getLogger(__name__)

#: Rows per INSERT batch. Big enough to keep SQLite fast, small enough that a
#: 190k-row dataset never sits in memory all at once.
DEFAULT_BATCH_SIZE = 2000


@dataclass(frozen=True)
class IngestionResult:
    """What one ingestion run did, for logs and for the CLI to print."""

    source: str
    read: int = 0
    emitted: int = 0
    discarded: int = 0
    written: int = 0
    stored: int = 0
    total_in_db: int = 0
    reasons: dict[str, int] = field(default_factory=dict)

    def summary(self) -> str:
        line = (
            f"{self.source}: read {self.read}, normalised {self.emitted}, "
            f"discarded {self.discarded}, written {self.written}, "
            f"{self.stored} rows for this source, {self.total_in_db} in database"
        )
        if self.reasons:
            breakdown = ", ".join(f"{reason}: {count}" for reason, count in self.reasons.items())
            line += f" | discards -> {breakdown}"
        return line


def run_ingestion(
    source: str | ListingSource,
    repository: ListingRepository,
    *,
    batch_size: int = DEFAULT_BATCH_SIZE,
) -> IngestionResult:
    """Stream every listing from ``source`` into ``repository`` in batches."""
    instance = source if isinstance(source, ListingSource) else get_source_class(source)()

    logger.info("Ingesting from %s", instance.name)

    batch: list[Listing] = []
    written = 0

    # One connection for the whole run; each batch still commits on its own.
    with repository.database.bulk():
        for listing in instance.iter_listings():
            batch.append(listing)
            if len(batch) >= batch_size:
                written += repository.upsert_many(batch)
                batch.clear()

        if batch:
            written += repository.upsert_many(batch)

    stats = instance.stats
    result = IngestionResult(
        source=instance.name,
        # Sources that do not track stats (the default) report what they emitted.
        read=stats.read or written,
        emitted=stats.emitted or written,
        discarded=stats.discarded,
        written=written,
        stored=repository.counts_by_source().get(instance.name, 0),
        total_in_db=repository.count(),
        reasons=dict(stats.reasons),
    )

    logger.info(result.summary())
    return result
