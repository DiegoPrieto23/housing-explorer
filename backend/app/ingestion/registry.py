"""Name -> source lookup, so wiring a new provider is a single registration."""

from __future__ import annotations

from app.ingestion.base import ListingSource

_REGISTRY: dict[str, type[ListingSource]] = {}


def register_source(cls: type[ListingSource]) -> type[ListingSource]:
    """Class decorator that makes a source discoverable by name."""
    name = getattr(cls, "name", None)
    if not name:
        raise TypeError(f"{cls.__name__} must define a class-level `name`")
    if name in _REGISTRY and _REGISTRY[name] is not cls:
        raise ValueError(f"Duplicate ListingSource name: {name!r}")
    _REGISTRY[name] = cls
    return cls


def get_source_class(name: str) -> type[ListingSource]:
    try:
        return _REGISTRY[name]
    except KeyError:
        known = ", ".join(sorted(_REGISTRY)) or "<none>"
        raise KeyError(f"Unknown source {name!r}. Registered: {known}") from None


def available_sources() -> list[str]:
    return sorted(_REGISTRY)
