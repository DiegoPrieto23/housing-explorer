"""FastAPI dependencies: one place to wire storage into the request cycle."""

from __future__ import annotations

from functools import lru_cache
from typing import Annotated

from fastapi import Depends

from app.config import Settings, get_settings
from app.storage.database import Database
from app.storage.repository import ListingRepository


@lru_cache
def get_database() -> Database:
    settings: Settings = get_settings()
    return Database(settings.database_path)


def get_repository() -> ListingRepository:
    return ListingRepository(get_database())


#: Reusable annotation so routes stay free of call-in-default patterns.
RepositoryDep = Annotated[ListingRepository, Depends(get_repository)]
