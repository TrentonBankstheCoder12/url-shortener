"""A small thread-safe LRU cache that sits in front of the database for redirects.

Redirects are the hot path (read-heavy, many reads per write), and the code -> URL mapping
almost never changes, so it is an ideal cache candidate. We cache only immutable fields;
click counts always go to the database.
"""

from __future__ import annotations

import threading
from collections import OrderedDict
from dataclasses import dataclass
from datetime import datetime


@dataclass(frozen=True)
class CachedLink:
    link_id: int
    target_url: str
    expires_at: datetime | None


class LRUCache:
    def __init__(self, max_size: int):
        if max_size < 1:
            raise ValueError("max_size must be at least 1")
        self.max_size = max_size
        self._data: OrderedDict[str, CachedLink] = OrderedDict()
        # FastAPI runs sync endpoints in a thread pool, so concurrent access is real.
        self._lock = threading.Lock()
        self.hits = 0
        self.misses = 0

    def get(self, key: str) -> CachedLink | None:
        with self._lock:
            value = self._data.get(key)
            if value is None:
                self.misses += 1
                return None
            self._data.move_to_end(key)  # mark as most recently used
            self.hits += 1
            return value

    def put(self, key: str, value: CachedLink) -> None:
        with self._lock:
            self._data[key] = value
            self._data.move_to_end(key)
            if len(self._data) > self.max_size:
                self._data.popitem(last=False)  # evict least recently used

    def invalidate(self, key: str) -> None:
        with self._lock:
            self._data.pop(key, None)

    def __contains__(self, key: str) -> bool:
        with self._lock:
            return key in self._data

    def __len__(self) -> int:
        with self._lock:
            return len(self._data)
