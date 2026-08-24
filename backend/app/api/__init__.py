"""HTTP layer: routing and dependency wiring only."""

from fastapi import APIRouter

from app.api.routes import health, listings, sources, stats

api_router = APIRouter()
api_router.include_router(health.router)
api_router.include_router(listings.router)
api_router.include_router(sources.router)
api_router.include_router(stats.router)

__all__ = ["api_router"]
