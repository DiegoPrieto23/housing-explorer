"""Placeholder for the official Idealista API source.

Kept as a skeleton to show exactly what adding a second provider costs: this
file, one line in ``sources/__init__.py``, and credentials in ``.env``. No
other layer changes.
"""

from __future__ import annotations

from app.config import get_settings
from app.ingestion.base import ListingSource, ListingSourceError
from app.ingestion.registry import register_source
from app.models.listing import Listing


@register_source
class IdealistaApiSource(ListingSource):
    """Fetches adverts from the official Idealista Search API (not implemented).

    The real implementation will need to: exchange ``api_key``/``api_secret``
    for an OAuth2 bearer token, page through ``/3.5/es/search``, respect the
    request quota, and map each ``elementList`` entry onto :class:`Listing`.
    """

    name = "idealista"

    def __init__(self, api_key: str | None = None, api_secret: str | None = None) -> None:
        settings = get_settings()
        self.api_key = api_key or settings.idealista_api_key
        self.api_secret = api_secret or settings.idealista_api_secret

    def health_check(self) -> bool:
        return bool(self.api_key and self.api_secret)

    def fetch_listings(self) -> list[Listing]:
        if not self.health_check():
            raise ListingSourceError("Missing IDEALISTA_API_KEY / IDEALISTA_API_SECRET")
        raise NotImplementedError(
            "IdealistaApiSource.fetch_listings is a placeholder; implement the "
            "OAuth2 token exchange and /3.5/es/search mapping here."
        )
