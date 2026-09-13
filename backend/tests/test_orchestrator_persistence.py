"""Unit tests for the orchestration persistence boundary (Phase 6)."""

from __future__ import annotations

import uuid

from app.models.enums import ReviewStatus, TaskStatus
from app.orchestrator.persistence import MemoryOrchestratorStore, TaskWrite

RID = uuid.uuid4()


class TestMemoryOrchestratorStore:
    async def test_transition_records_status_history(self) -> None:
        store = MemoryOrchestratorStore()
        await store.transition(RID, ReviewStatus.AGENTS_RUNNING)
        await store.transition(RID, ReviewStatus.CONSOLIDATING)
        record = store.reviews[str(RID)]
        assert record["status"] == "CONSOLIDATING"
        statuses = [h["status"] for h in record["status_history"]]
        assert statuses == ["AGENTS_RUNNING", "CONSOLIDATING"]
        assert record["status_history"][1]["from"] == "AGENTS_RUNNING"

    async def test_transition_carries_failure_fields(self) -> None:
        store = MemoryOrchestratorStore()
        await store.transition(
            RID,
            ReviewStatus.FAILED,
            failure_reason="one agent died",
            root_cause="quality exhausted retries",
        )
        record = store.reviews[str(RID)]
        assert record["failure_reason"] == "one agent died"
        assert record["root_cause"] == "quality exhausted retries"

    async def test_append_note_collects_supervisor_notes(self) -> None:
        store = MemoryOrchestratorStore()
        await store.append_note(RID, "retrying security")
        await store.append_note(RID, "diagnosed FAILED")
        assert store.reviews[str(RID)]["supervisor_notes"] == [
            "retrying security",
            "diagnosed FAILED",
        ]

    async def test_sync_agents_stable_keys(self) -> None:
        store = MemoryOrchestratorStore()
        rows = [
            {"key": "security", "name": "Security Agent", "version": "1"},
            {"key": "quality", "name": "Quality Agent", "version": "2"},
        ]
        first = await store.sync_agents(rows)
        second = await store.sync_agents(rows)
        assert first == second

    async def test_review_task_and_metric_recorded(self) -> None:
        store = MemoryOrchestratorStore()
        await store.create_review_task(
            RID,
            TaskWrite(
                agent_key="security",
                agent_id="agent-1",
                queue_name="security_queue",
                status=TaskStatus.SUCCESS,
                retry_count=1,
                last_error=None,
                scope={"review_id": str(RID)},
            ),
        )
        await store.create_agent_metric(
            RID, agent_key="security", agent_id="agent-1", stats={"duration_ms": 42}
        )
        assert store.tasks[0]["status"] == "SUCCESS"
        assert store.tasks[0]["retry_count"] == 1
        assert store.metrics[0]["stats"] == {"duration_ms": 42}
