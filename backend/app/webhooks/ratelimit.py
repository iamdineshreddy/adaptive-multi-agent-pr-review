"""In-memory rate limiter for the public webhook surface.

Single-process sliding-window limiter (docs/SECURITY.md §6). It is deliberately
process-local: multi-process/Redis-backed limiting is introduced in Phase 4/14.
Keys are namespaced so one limiter instance can enforce both the per-IP and the
per-repository burst caps.
"""

from __future__ import annotations

import time
from collections import defaultdict
from collections.abc import Iterable


class MemoryRateLimiter:
    """Sliding-window counter keyed by (bucket, key) pairs.

    ``allow()`` returns True while every requested bucket is under its cap within
    the window; on success the current hit is recorded. Buckets are pruned lazily.
    """

    def __init__(
        self,
        per_ip_per_minute: int = 120,
        per_repo_burst: int = 30,
        window_seconds: int = 60,
    ) -> None:
        self._window = window_seconds
        self._caps: dict[str, int] = {
            "ip": per_ip_per_minute,
            "repo": per_repo_burst,
        }
        self._hits: dict[tuple[str, str], list[float]] = defaultdict(list)

    def allow(self, buckets: Iterable[tuple[str, str | int]]) -> bool:
        """All (bucket, key) pairs must be under their caps for the call to pass."""
        now = time.monotonic()
        for bucket, key in buckets:
            events = self._hits[(bucket, str(key))]
            while events and events[0] <= now - self._window:
                events.pop(0)
        approved = all(
            len(self._hits[(bucket, str(key))]) < self._caps[bucket]
            for bucket, key in buckets
        )
        if approved:
            for bucket, key in buckets:
                self._hits[(bucket, str(key))].append(now)
        return approved

    def reset(self) -> None:
        self._hits.clear()
