"""Per-client token bucket rate limiter.

Each client has a bucket holding up to `capacity` tokens. A request spends one token;
tokens refill continuously at `refill_per_sec`. This allows short bursts (up to capacity)
while capping the sustained rate, which is friendlier than a fixed window that resets
all at once and lets a client send 2x the limit across a window boundary.
"""

from __future__ import annotations

import threading
import time
from collections import OrderedDict
from collections.abc import Callable
from dataclasses import dataclass


@dataclass
class _Bucket:
    tokens: float
    updated: float


class TokenBucketLimiter:
    def __init__(
        self,
        capacity: int,
        refill_per_sec: float,
        clock: Callable[[], float] = time.monotonic,
        max_clients: int = 10_000,
    ):
        self.capacity = capacity
        self.refill_per_sec = refill_per_sec
        self._clock = clock  # injectable so tests can move time without sleeping
        self._max_clients = max_clients
        self._buckets: OrderedDict[str, _Bucket] = OrderedDict()
        self._lock = threading.Lock()

    def allow(self, key: str) -> tuple[bool, float]:
        """Try to spend one token. Returns (allowed, seconds until a token is available)."""
        now = self._clock()
        with self._lock:
            bucket = self._buckets.get(key)
            if bucket is None:
                bucket = _Bucket(tokens=float(self.capacity), updated=now)
                self._buckets[key] = bucket
                # Bound memory: forget the least recently seen client. A forgotten client
                # simply gets a full bucket, which is the safe direction to fail.
                if len(self._buckets) > self._max_clients:
                    self._buckets.popitem(last=False)
            self._buckets.move_to_end(key)

            # Refill lazily based on elapsed time instead of running a background timer.
            elapsed = now - bucket.updated
            bucket.tokens = min(self.capacity, bucket.tokens + elapsed * self.refill_per_sec)
            bucket.updated = now

            if bucket.tokens >= 1:
                bucket.tokens -= 1
                return True, 0.0
            return False, (1 - bucket.tokens) / self.refill_per_sec
