"""Unit tests for the Phase 4 queue layer (docs/QUEUE.md).

Broker/DB-free: task cores and helpers are exercised with deterministic fakes;
live Redis/PostgreSQL behaviour self-skips in the integration files.
"""

from __future__ import annotations

import random
import uuid

import pytest

from app.config.settings import Settings
from app.queue import backoff as backoff_mod
from app.queue import deadletter as deadletter_mod
from app.queue.backoff import backoff_delay
from app.queue.celery_app import QUEUES, TASK_ROUTES, create_celery
from app.queue.deadletter import (
    DeadLetterEntry,
    encode_entry,
    record_dead_letter,
)
from app.queue.dispatch import CeleryDispatcher
from app.queue.priority import (
    PriorityEntry,
    make_member,
    member_review_id,
)
from app.queue.tasks import drain_and_stage, stage_review_to_priority

RID_A = uuid.UUID("aaaaaaaa-aaaa-aaaa-aaaa-aaaaaaaaaaaa")
RID_B = uuid.UUID("bbbbbbbb-bbbb-bbbb-bbbb-bbbbbbbbbbbb")
RID_C = uuid.UUID("cccccccc-cccc-cccc-cccc-cccccccccccc")


class MemoryPriorityStore:
    """Deterministic in-process stand-in for the Redis sorted set."""

    def __init__(
        self, entries: list[tuple[uuid.UUID, float, int]] | None = None
    ) -> None:
        self._entries: list[tuple[uuid.UUID, float, int]] = list(entries or [])

    async def enqueue(self, review_id: uuid.UUID, score: float, ordinal: int) -> None:
        self._entries.append((review_id, score, ordinal))

    async def pop_highest(self) -> PriorityEntry | None:
        if not self._entries:
            return None
        highest = max(self._entries, key=lambda e: (e[1], -e[2]))
        self._entries.remove(highest)
        return PriorityEntry(review_id=highest[0], score=highest[1], ordinal=highest[2])

    async def size(self) -> int:
        return len(self._entries)

    async def reset(self) -> None:
        self._entries.clear()


# --- backoff -------------------------------------------------------------------


class TestBackoff:
    def test_exponential_growth(self) -> None:
        assert backoff_delay(0, 30, 240, 0) == 30
        assert backoff_delay(1, 30, 240, 0) == 60
        assert backoff_delay(2, 30, 240, 0) == 120

    def test_capped(self) -> None:
        assert backoff_delay(3, 30, 240, 0) == 240
        assert backoff_delay(10, 30, 240, 0) == 240

    def test_minimum_delay_is_one_second(self) -> None:
        for _ in range(50):
            assert backoff_delay(0, 1, 240, 0.5, random.Random()) >= 1

    def test_invalid_arguments_rejected(self) -> None:
        with pytest.raises(ValueError):
            backoff_delay(-1)
        with pytest.raises(ValueError):
            backoff_delay(0, base_seconds=0)
        with pytest.raises(ValueError):
            backoff_delay(0, jitter=1.5)

    def test_policy_matches_documented_values(self) -> None:
        settings = Settings()
        policy = backoff_mod.retry_policy(
            settings.celery_max_retries, settings.celery_retry_backoff_seconds
        )
        assert policy["max_retries"] == 4
        assert policy["retry_backoff"] == 30
        assert policy["retry_jitter"] is True


# --- priority zset helpers ------------------------------------------------------


class TestPriorityMember:
    def test_encoding_roundtrip(self) -> None:
        member = make_member(5, RID_A)
        assert member == "00000000000000000005:" + str(RID_A)
        assert member_review_id(member) == RID_A

    def test_fifo_ordinal_zero_survives(self) -> None:
        assert make_member(0, RID_B).startswith("0" * 20 + ":")

    def test_negative_ordinal_rejected(self) -> None:
        with pytest.raises(ValueError):
            make_member(-1, RID_A)


# --- task cores -----------------------------------------------------------------


class TestStageReviewToPriority:
    async def test_stages_review_with_score(self) -> None:
        store = MemoryPriorityStore()

        async def load(review_id: uuid.UUID) -> float | None:
            return 7.5

        result = await stage_review_to_priority(
            RID_A, "delivery-1", store, load, ordinal=10
        )
        assert result["review_id"] == str(RID_A)
        assert result["delivery_id"] == "delivery-1"
        assert result["priority_score"] == 7.5
        assert store._entries == [(RID_A, 7.5, 10)]

    async def test_ordinal_zero_is_not_replaced(self) -> None:
        store = MemoryPriorityStore()

        async def load(review_id: uuid.UUID) -> float | None:
            return 1.0

        await stage_review_to_priority(RID_B, "d2", store, load, ordinal=0)
        assert store._entries == [(RID_B, 1.0, 0)]

    async def test_missing_score_raises(self) -> None:
        store = MemoryPriorityStore()

        async def load(review_id: uuid.UUID) -> float | None:
            return None

        with pytest.raises(ValueError):
            await stage_review_to_priority(RID_C, "d3", store, load)


class TestDrainAndStage:
    async def test_pops_highest_score(self) -> None:
        store = MemoryPriorityStore([(RID_A, 3.0, 1), (RID_B, 8.5, 2), (RID_C, 5.0, 3)])
        entry = await drain_and_stage(store)
        assert entry is not None
        assert entry.review_id == RID_B
        assert entry.score == 8.5
        assert await store.size() == 2

    async def test_empty_store_returns_none(self) -> None:
        assert await drain_and_stage(MemoryPriorityStore()) is None


# --- dead-letter ----------------------------------------------------------------


class TestDeadLetter:
    def test_encode_entry_is_deterministic(self) -> None:
        entry = DeadLetterEntry(RID_A, "d1", "pr_ingestion_queue", "boom", 5)
        encoded = encode_entry(entry)
        assert encoded.count("|") == 4
        assert str(RID_A) in encoded
        assert encoded.rsplit("|", 1)[1] == "5"

    def test_record_appends_to_log(self) -> None:
        log = deadletter_mod.MemoryDeadLetterLog()
        record_dead_letter(
            DeadLetterEntry(RID_A, "d1", "pr_ingestion_queue", "exhausted", 4),
            log=log,
        )
        assert len(log.entries) == 1
        assert log.entries[0].error == "exhausted"


# --- celery app config ----------------------------------------------------------


class TestCeleryApp:
    def test_broker_from_settings(self) -> None:
        app = create_celery(Settings(redis_url="redis://example:6379/2"))
        assert app.conf.broker_url == "redis://example:6379/2"
        assert app.conf.result_backend == "redis://example:6379/2"

    def test_reliable_worker_defaults(self) -> None:
        app = create_celery()
        assert app.conf.task_acks_late is True
        assert app.conf.task_track_started is True
        assert app.conf.worker_prefetch_multiplier == 1

    def test_routing_matches_queue_spec(self) -> None:
        assert QUEUES["pr_ingestion"] == "pr_ingestion_queue"
        assert TASK_ROUTES["queue.enqueue_review"]["queue"] == "pr_ingestion_queue"
        assert TASK_ROUTES["queue.pop_and_stage"]["queue"] == "default"
        assert set(QUEUES.values()) == {
            "pr_ingestion_queue",
            "review_task_queue",
            "security_queue",
            "quality_queue",
            "performance_queue",
            "architecture_queue",
            "standards_queue",
            "decision_queue",
            "publisher_queue",
            "feedback_queue",
            "default",
        }


class TestCeleryDispatcher:
    async def test_dispatch_publishes_to_pr_ingestion_queue(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        captured: dict = {}

        class FakeResult:
            id = "task-42"

        def fake_send_task(name: str, args: list, queue: str) -> FakeResult:
            captured["name"] = name
            captured["args"] = args
            captured["queue"] = queue
            return FakeResult()

        monkeypatch.setattr("app.queue.dispatch.celery_app.send_task", fake_send_task)
        dispatcher = CeleryDispatcher()
        result = await dispatcher.dispatch(RID_A, "delivery-1")
        assert captured == {
            "name": "queue.enqueue_review",
            "args": [str(RID_A), "delivery-1"],
            "queue": "pr_ingestion_queue",
        }
        assert result.dispatched is True
        assert "task-42" in result.note
