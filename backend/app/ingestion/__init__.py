"""Ingestion layer: pluggable data sources behind a single interface."""

from app.ingestion import sources as sources  # noqa: F401  (import registers them)
from app.ingestion.base import ListingSource, ListingSourceError
from app.ingestion.registry import available_sources, get_source_class, register_source

__all__ = [
    "ListingSource",
    "ListingSourceError",
    "available_sources",
    "get_source_class",
    "register_source",
]
