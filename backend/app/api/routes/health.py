"""Liveness and readiness endpoints."""

from __future__ import annotations

from fastapi import APIRouter

from app.api.deps import RepositoryDep
from app.config import get_settings

router = APIRouter(tags=["health"])


@router.get("/health")
def health() -> dict[str, str]:
    settings = get_settings()
    return {"status": "ok", "app": settings.app_name}


@router.get("/health/ready")
def ready(repository: RepositoryDep) -> dict[str, object]:
    """Confirms the SQLite file is reachable and reports what is stored."""
    return {"status": "ok", "listings": repository.count()}
