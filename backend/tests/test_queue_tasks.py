"""Unit tests for the Celery task wrappers (docs/QUEUE.md §4, Phase 16).

``enqueue_review_task`` and ``pop_and_stage_task`` are thin broker-facing shells
over the injectable cores; this file covers the parts the core tests cannot: the
bounded-retry policy, the dead-letter + ``MaxRetriesExceededError`` exhaustion
path, and the scheduler task's pop-and-dispatch hand-off.
"""

from __future__ import annotations

import types
import uuid
from collections.abc import Callable
from typing import cast

import pytest
from celery.exceptions import MaxRetriesExceededError

from app.queue.deadletter import DeadLetterEntry
from app.queue.priority import PriorityEntry
from app.queue.tasks import enqueue_review_task, pop_and_stage_task

RID = uuid.uuid4()


class _FakeStore:
    """Deterministic in-process stand-in for RedisPriorityStore (task builds it)."""

    def __init__(self) -> None:
        self.reviews: list[tuple[uuid.UUID, float, int]] = []
        self.closed = False

    async def enqueue(self, review_id: uuid.UUID, score: float, ordinal: int) -> None:
        self.reviews.append((review_id, score, ordinal))

    async def pop_highest(self) -> PriorityEntry | None:
        if not self.reviews:
            return None
        entry = PriorityEntry(*self.reviews.pop(0))
        return entry

    async def size(self) -> int:
        return len(self.reviews)

    async def reset(self) -> None:
        self.reviews.clear()

    async def aclose(self) -> None:
        self.closed = True


class _FakeTask:
    """Celery ``self`` stand-in: exposes ``request.retries`` and records retries."""

    def __init__(self, retries: int = 0) -> None:
        self.request = types.SimpleNamespace(retries=retries)
        self.retry_calls: list[dict[str, object]] = []

    def retry(self, **kwargs):  # type: ignore[no-untyped-def]
        self.retry_calls.append(kwargs)
        raise _TaskRetryError()


class _TaskRetryError(Exception):
    """Sentinel raised by the fake ``self.retry`` when a retry is scheduled."""


async def _score_loader(review_id: uuid.UUID) -> float | None:
    return 4.5


def _run_enqueue(
    task: _FakeTask, review_id: str, delivery_id: str
) -> dict[str, object]:
    """Invoke the task body with an injected fake task (bind semantics)."""
    impl = cast(
        Callable[[_FakeTask, str, str], dict[str, object]],
        enqueue_review_task.run.__func__,
    )
    return impl(task, review_id, delivery_id)


class TestEnqueueReviewTask:
    def test_success_stages_review(self, monkeypatch: pytest.MonkeyPatch) -> None:
        store = _FakeStore()
        monkeypatch.setattr("app.queue.tasks.RedisPriorityStore", lambda: store)
        monkeypatch.setattr("app.queue.tasks.load_default_score", _score_loader)
        task = _FakeTask()
        result = _run_enqueue(task, str(RID), "delivery-1")
        assert result["review_id"] == str(RID)
        assert result["priority_score"] == 4.5
        assert result["queue"] == "pr_ingestion_queue"
        assert len(store.reviews) == 1
        assert store.reviews[0][0] == RID
        assert store.reviews[0][1] == 4.5
        assert store.closed is True

    def test_transient_failure_schedules_bounded_retry(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        async def _boom(*args, **kwargs):  # type: ignore[no-untyped-def]
            raise RuntimeError("boom")

        monkeypatch.setattr("app.queue.tasks.RedisPriorityStore", lambda: _FakeStore())
        monkeypatch.setattr("app.queue.tasks.load_default_score", _boom)
        task = _FakeTask(retries=0)
        with pytest.raises(_TaskRetryError):
            _run_enqueue(task, str(RID), "delivery-1")
        assert len(task.retry_calls) == 1
        assert isinstance(task.retry_calls[0]["exc"], RuntimeError)
        countdown = cast(int, task.retry_calls[0]["countdown"])
        assert countdown >= 15  # backoff_delay(0, 30, ...) + jitter

    def test_exhausted_retries_dead_letter(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        dead: list[DeadLetterEntry] = []
        max_retries = 4

        async def _boom(*args, **kwargs):  # type: ignore[no-untyped-def]
            raise RuntimeError("boom")

        def fake_record(entry, log=None):  # type: ignore[no-untyped-def]
            dead.append(entry)

        monkeypatch.setattr("app.queue.tasks.RedisPriorityStore", lambda: _FakeStore())
        monkeypatch.setattr("app.queue.tasks.load_default_score", _boom)
        monkeypatch.setattr("app.queue.tasks.record_dead_letter", fake_record)
        task = _FakeTask(retries=max_retries)
        with pytest.raises(MaxRetriesExceededError):
            _run_enqueue(task, str(RID), "delivery-1")
        assert len(dead) == 1
        entry = dead[0]
        assert entry.review_id == RID
        assert entry.delivery_id == "delivery-1"
        assert entry.queue == "pr_ingestion_queue"
        assert entry.attempts == max_retries + 1
        assert task.retry_calls == []  # exhaustion never schedules another retry


class TestPopAndStageTask:
    def test_pops_highest_and_dispatches(self, monkeypatch: pytest.MonkeyPatch) -> None:
        store = _FakeStore()
        store.reviews.append((RID, 9.0, 1))
        monkeypatch.setattr("app.queue.tasks.RedisPriorityStore", lambda: store)
        dispatched: list[uuid.UUID] = []

        class FakeOrchestrator:
            async def dispatch(self, review_id: uuid.UUID) -> None:
                dispatched.append(review_id)

        monkeypatch.setattr(
            "app.orchestrator.dispatch.OrchestrationDispatcher", FakeOrchestrator
        )
        result = pop_and_stage_task()
        assert result == str(RID)
        assert dispatched == [RID]
        assert store.closed is True

    def test_empty_store_returns_none(self, monkeypatch: pytest.MonkeyPatch) -> None:
        store = _FakeStore()
        monkeypatch.setattr("app.queue.tasks.RedisPriorityStore", lambda: store)
        assert pop_and_stage_task() is None
