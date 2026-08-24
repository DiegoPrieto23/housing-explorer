from __future__ import annotations

from collections.abc import Iterator
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from app.api import deps
from app.main import create_app
from app.storage.cache import stats_cache
from app.storage.database import Database
from app.storage.repository import ListingRepository


@pytest.fixture(autouse=True)
def _isolated_cache() -> Iterator[None]:
    """The aggregate cache lives in the process, so it outlives a test database.

    Autouse rather than opt-in: a test that only reads would otherwise be able
    to see an entry another test computed over a completely different database,
    and that failure looks like a bug in the query rather than in the fixtures.
    """
    stats_cache.bump()
    yield
    stats_cache.bump()


@pytest.fixture
def database(tmp_path: Path) -> Database:
    db = Database(tmp_path / "test.db")
    db.init_schema()
    return db


@pytest.fixture
def repository(database: Database) -> ListingRepository:
    return ListingRepository(database)


@pytest.fixture
def client(database: Database) -> Iterator[TestClient]:
    app = create_app()
    app.dependency_overrides[deps.get_repository] = lambda: ListingRepository(database)
    with TestClient(app) as test_client:
        yield test_client
