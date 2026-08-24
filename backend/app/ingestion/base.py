"""The extension point of the system: :class:`ListingSource`.

Adding a provider (the official Idealista API, a CSV dump, a portal scraper)
means writing one subclass and registering it. Nothing outside this package
should ever branch on which source produced a listing.
"""

from __future__ import annotations

from abc import ABC, abstractmethod
from collections import Counter
from collections.abc import Iterator
from dataclasses import dataclass, field
from typing import ClassVar

from app.models.listing import Listing


class ListingSourceError(RuntimeError):
    """Raised when a source cannot deliver listings (network, auth, schema)."""


@dataclass
class SourceStats:
    """What happened during the last read: how much came in, how much survived."""

    read: int = 0
    emitted: int = 0
    discarded: int = 0
    #: Discard reason -> count, e.g. {"missing coordinates": 12}.
    reasons: Counter[str] = field(default_factory=Counter)
    #: A few example rows per reason, to make a bad file diagnosable.
    samples: dict[str, str] = field(default_factory=dict)

    def discard(self, reason: str, detail: str = "") -> None:
        self.discarded += 1
        self.reasons[reason] += 1
        if detail and reason not in self.samples:
            self.samples[reason] = detail

    def summary(self) -> str:
        if not self.discarded:
            return f"read {self.read}, emitted {self.emitted}, discarded 0"
        breakdown = ", ".join(f"{reason}: {count}" for reason, count in self.reasons.most_common())
        return f"read {self.read}, emitted {self.emitted}, discarded {self.discarded} ({breakdown})"


class ListingSource(ABC):
    """Abstract provider of normalised :class:`Listing` objects.

    Subclasses declare a unique :attr:`name`, take whatever credentials or
    paths they need through ``__init__``, and translate their raw payload into
    ``Listing`` inside :meth:`fetch_listings`. All provider-specific concerns
    -- pagination, rate limits, auth tokens, field mapping, unit conversion --
    stay behind this method.
    """

    #: Unique slug stored on every listing produced by this source.
    name: ClassVar[str]

    def __init_subclass__(cls, **kwargs: object) -> None:
        super().__init_subclass__(**kwargs)
        if not getattr(cls, "name", None) and not getattr(cls, "__abstractmethods__", None):
            raise TypeError(f"{cls.__name__} must define a class-level `name`")

    @property
    def stats(self) -> SourceStats:
        """Counters for the last read. Reset by :meth:`reset_stats`."""
        if not hasattr(self, "_stats"):
            self._stats = SourceStats()
        return self._stats

    def reset_stats(self) -> None:
        self._stats = SourceStats()

    @abstractmethod
    def fetch_listings(self) -> list[Listing]:
        """Return the current batch of normalised listings from this source.

        Implementations must set ``source`` on every listing to :attr:`name`
        and should raise :class:`ListingSourceError` on unrecoverable failures
        rather than returning a partial batch silently.
        """

    def iter_listings(self) -> Iterator[Listing]:
        """Stream listings one at a time.

        Optional optimisation, not part of the contract: the default just walks
        the list from :meth:`fetch_listings`. Sources backed by large files
        override it so a load never holds the whole dataset in memory, and the
        pipeline uses it in preference to ``fetch_listings``.
        """
        yield from self.fetch_listings()

    def health_check(self) -> bool:
        """Cheap reachability probe. Overridden by sources that talk to a network."""
        return True

    def __repr__(self) -> str:  # pragma: no cover - debugging aid
        return f"<{type(self).__name__} name={getattr(self, 'name', '?')!r}>"
