"""Simple in-memory rate limiter for completion requests."""

from __future__ import annotations

import time
from collections import defaultdict, deque
from threading import Lock


class CompletionRateLimiter:
    def __init__(self, max_per_minute: int = 120) -> None:
        self.max_per_minute = max(1, int(max_per_minute))
        self._events: dict[str, deque[float]] = defaultdict(deque)
        self._lock = Lock()

    def allow(self, key: str) -> bool:
        now = time.monotonic()
        window_start = now - 60.0
        with self._lock:
            bucket = self._events[key]
            while bucket and bucket[0] < window_start:
                bucket.popleft()
            if len(bucket) >= self.max_per_minute:
                return False
            bucket.append(now)
            return True
