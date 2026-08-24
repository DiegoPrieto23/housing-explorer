"""Introspection of the registered ingestion sources."""

from __future__ import annotations

from fastapi import APIRouter

from app.api.deps import RepositoryDep
from app.ingestion import available_sources, get_source_class

router = APIRouter(prefix="/sources", tags=["sources"])


@router.get("")
def list_sources(repository: RepositoryDep) -> list[dict[str, object]]:
    stored = repository.counts_by_source()
    result: list[dict[str, object]] = []
    for name in available_sources():
        try:
            healthy = get_source_class(name)().health_check()
        except Exception:  # a misconfigured source must not break the endpoint
            healthy = False
        result.append({"name": name, "healthy": healthy, "listings": stored.get(name, 0)})
    return result
