"""A tiny read-through cache for aggregates, keyed on the data it summarises.

Statistics are the most expensive thing the API computes and the least likely
to change: the map fires `/stats` on every pan, and a hundred users panning over
Madrid ask the identical question a hundred times. Caching it is the difference
between recomputing an average over 75.000 rows and returning a dict.

The whole design rests on one idea: **the cache key carries a version of the
data**, so invalidation is not something anyone has to remember to do. When a
write bumps the version, every previously cached entry becomes unreachable at
once, and the old entries are evicted by the LRU as normal. There is no
`invalidate()` to forget to call, and no window in which a stale answer can be
served -- which is what usually goes wrong with hand-managed caches.

It is deliberately in-process and unbounded in time: this is a read-only
single-node API, so there is nothing to coordinate with. The moment there is a
second writer -- or a second process -- this needs to become a shared cache
keyed on a version read from the database, and the docstring should stop being
true rather than quietly become a lie.
"""

from __future__ import annotations

import threading
from collections import OrderedDict
from collections.abc import Callable
from typing import Any


class VersionedCache:
    """LRU cache whose entries are scoped to a data version.

    Not `functools.lru_cache`: that has no notion of the underlying data
    changing, and clearing it wholesale on every write would throw away the
    entries a version bump already made unreachable anyway. Being explicit about
    the version also makes the hit rate observable, which a decorator hides.
    """

    def __init__(self, maxsize: int = 256, wait_timeout: float = 30.0) -> None:
        self._entries: OrderedDict[tuple[int, str], Any] = OrderedDict()
        self._maxsize = maxsize
        self._version = 0
        #: Keys somebody is computing right now -> the event that says "done".
        #: This is what makes concurrent misses on the same key collapse into a
        #: single computation instead of one per caller.
        self._in_flight: dict[tuple[int, str], threading.Event] = {}
        #: Cap on how long a waiter blocks. A hung computation must not turn
        #: into a hung request; past this the waiter goes and computes itself.
        self._wait_timeout = wait_timeout
        # The API is served by a threadpool, so two requests can miss on the
        # same key at once. The lock guards the two dicts; the computation runs
        # outside it, coordinated through _in_flight so only one caller does it.
        self._lock = threading.Lock()
        self.hits = 0
        self.misses = 0

    @property
    def version(self) -> int:
        return self._version

    def bump(self) -> int:
        """Declare the data changed. Every existing entry becomes unreachable."""
        with self._lock:
            self._version += 1
            # Dropping them now rather than letting the LRU do it keeps memory
            # flat after a bulk load, which would otherwise leave a full cache
            # of entries nobody can ever reach again.
            self._entries.clear()
            # Los cómputos en vuelo llevan la versión vieja en su clave, así que
            # ya son inalcanzables; soltar a quien espere evita que se coma el
            # tiempo de espera entero para nada.
            for event in self._in_flight.values():
                event.set()
            self._in_flight.clear()
            return self._version

    def get_or_compute(self, key: str, compute: Callable[[], Any]) -> Any:
        """Return the cached value, computing it at most once across all callers.

        The "at most once" is not an optimisation, it is what keeps the API
        standing up. A browser tab stuck in a reload loop was seen firing the
        same `/stats` and `/facets` requests over and over; with each miss
        computing independently, 24 threads ended up recomputing identical
        aggregates over 150k rows at the same time, fighting each other for the
        GIL, and the server stopped answering anything at all.

        So the first caller for a key computes and the rest wait for its result
        -- the pattern usually called single-flight. Waiting on an aggregate
        somebody else is already running is free; running it again is not.
        """
        versioned = (self._version, key)

        while True:
            with self._lock:
                if versioned in self._entries:
                    self._entries.move_to_end(versioned)
                    self.hits += 1
                    return self._entries[versioned]

                in_flight = self._in_flight.get(versioned)
                if in_flight is None:
                    # Nobody is computing this: claim it and go do the work.
                    self.misses += 1
                    in_flight = threading.Event()
                    self._in_flight[versioned] = in_flight
                    break

                self.hits += 1

            # Somebody else claimed it. Wait for them, then loop round: the value
            # should be in the cache, but if their computation raised, or a bump
            # invalidated it in the meantime, the next pass simply tries again.
            in_flight.wait(timeout=self._wait_timeout)
            with self._lock:
                if versioned in self._entries:
                    return self._entries[versioned]
                if self._in_flight.get(versioned) is in_flight:
                    # The other caller failed and did not clean up, or the wait
                    # timed out. Drop the marker so this thread can claim it.
                    self._in_flight.pop(versioned, None)

        try:
            # Computed outside the lock: an aggregate over 150k rows takes long
            # enough that holding it would serialise every other request.
            value = compute()
        finally:
            with self._lock:
                self._in_flight.pop(versioned, None)
            # Woken whatever happened, so a failed computation frees the waiters
            # to retry instead of leaving them on the timeout.
            in_flight.set()

        with self._lock:
            # The version can have moved while we were computing, in which case
            # this result already describes stale data. Throwing it away is the
            # cheap, obviously-correct option.
            if self._version == versioned[0]:
                self._entries[versioned] = value
                self._entries.move_to_end(versioned)
                while len(self._entries) > self._maxsize:
                    self._entries.popitem(last=False)

        return value

    def stats(self) -> dict[str, int | float]:
        total = self.hits + self.misses
        return {
            "version": self._version,
            "entries": len(self._entries),
            "in_flight": len(self._in_flight),
            "hits": self.hits,
            "misses": self.misses,
            "hit_rate": round(self.hits / total, 3) if total else 0.0,
        }

    def clear(self) -> None:
        """Forget everything without declaring a new version. For tests."""
        with self._lock:
            self._entries.clear()
            for event in self._in_flight.values():
                event.set()
            self._in_flight.clear()
            self.hits = 0
            self.misses = 0


#: One cache for the process. Imported by the repository, which is the only
#: thing that knows both when data is read and when it changes.
stats_cache = VersionedCache()
