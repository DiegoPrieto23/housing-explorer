"""Concrete sources. Import each module here so it registers itself."""

from app.ingestion.sources.idealista import IdealistaApiSource
from app.ingestion.sources.sample_csv import SampleCsvSource
from app.ingestion.sources.static_dataset import StaticDatasetSource

__all__ = ["IdealistaApiSource", "SampleCsvSource", "StaticDatasetSource"]
