"""Dispatch contracts shared by the webhook and queue layers.

Neutral module so neither layer depends on the other (avoids import cycles).
The protocol is implemented by ``LoggingDispatcher`` (Phase 3 fallback) and
``CeleryDispatcher`` (Phase 4 default).
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Protocol

_PR_INGESTION_QUEUE = "pr_ingestion_queue"


@dataclass(frozen=True)
class DispatchResult:
    dispatched: bool
    note: str


class ReviewDispatcher(Protocol):
    """Boundary over the queue: the webhook returns before any AI work."""

    async def dispatch(self, review_id: object, delivery_id: str) -> DispatchResult: ...
