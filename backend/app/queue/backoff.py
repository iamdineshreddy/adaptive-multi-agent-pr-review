"""Exponential backoff with jitter for task retries (docs/QUEUE.md §4).

    countdown = min(cap, base * 2^attempt) * (1 + uniform(-jitter, jitter))

``attempt`` is the zero-based retry index (``self.request.retries`` in Celery).
The delay is clamped to a configurable cap; jitter defaults to 10%.
"""

from __future__ import annotations

import random

_CAP_DEFAULT: float = 240.0


def backoff_delay(
    attempt: int,
    base_seconds: int = 30,
    cap_seconds: float = _CAP_DEFAULT,
    jitter: float = 0.1,
    rng: random.Random | None = None,
) -> int:
    """Return the countdown (ceil, whole seconds) for a retry.

    ``rng`` is injectable for deterministic tests (``jitter=0`` disables jitter).
    """
    if attempt < 0:
        raise ValueError("attempt must be >= 0")
    if base_seconds < 1:
        raise ValueError("base_seconds must be >= 1")
    if jitter < 0 or jitter > 1:
        raise ValueError("jitter must be within [0, 1]")
    exponential = base_seconds * (2**attempt)
    capped = min(cap_seconds, exponential)
    spread = random if rng is None else rng
    factor = 1.0 + spread.uniform(-jitter, jitter)
    return max(1, int(capped * factor))


def retry_policy(max_retries: int, base_seconds: int) -> dict[str, object]:
    """Celery ``retry_backoff``-style policy derived from the documented values."""
    return {
        "max_retries": max_retries,
        "retry_backoff": base_seconds,
        "retry_backoff_max": int(_CAP_DEFAULT),
        "retry_jitter": True,
    }
