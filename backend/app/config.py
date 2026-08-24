"""Application settings, read from environment / .env."""

from __future__ import annotations

from functools import lru_cache
from pathlib import Path

from pydantic_settings import BaseSettings, SettingsConfigDict

BACKEND_DIR = Path(__file__).resolve().parent.parent
PROJECT_ROOT = BACKEND_DIR.parent


class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        env_file=(PROJECT_ROOT / ".env", BACKEND_DIR / ".env"),
        env_file_encoding="utf-8",
        extra="ignore",
    )

    app_name: str = "housing-explorer"
    debug: bool = False

    # Storage
    database_path: Path = PROJECT_ROOT / "data" / "housing.db"

    # Where local datasets live (git-ignored, see data/README.md)
    data_dir: Path = PROJECT_ROOT / "data"

    # CORS. The explicit list is for deployed frontends; the regex covers local
    # development, where Vite moves to 5174+ if 5173 is taken.
    cors_origins: list[str] = []
    cors_origin_regex: str | None = r"https?://(localhost|127\.0\.0\.1)(:\d+)?"

    # Credentials for the future official Idealista source
    idealista_api_key: str | None = None
    idealista_api_secret: str | None = None


@lru_cache
def get_settings() -> Settings:
    """Cached settings instance; call get_settings.cache_clear() in tests."""
    return Settings()
