"""End-to-end unit tests for the orchestration service (Phase 6).

The service is exercised broker-free: a scripted runner replaces agent
execution and a MemoryOrchestratorStore records the persistence contract. This
proves the graph → persistence mapping (status transitions, review_tasks,
agent_metrics, supervisor notes, root cause) without PostgreSQL/GitHub.
"""

from __future__ import annotations

import uuid

import pytest

from app.agents.contract import AgentScope, ChangeKind, FileSlice
from app.config.settings import Settings
from app.models.enums import ReviewStatus, TaskStatus
from app.orchestrator.persistence import MemoryOrchestratorStore
from app.orchestrator.runners import RunnerResult
from app.orchestrator.service import run_review

REVIEW_ID = uuid.uuid4()
REPO_ID = uuid.uuid4()


class ScriptedRunner:
    name = "scripted"

    def __init__(self, script: dict[str, list[RunnerResult]]) -> None:
        self._script = {k: list(v) for k, v in script.items()}

    async def agent_keys(self) -> tuple[str, ...]:
        return tuple(self._script)

    async def run(self, agent_key: str, scope: AgentScope) -> RunnerResult:
        return self._script[agent_key].pop(0)


def _success(key: str, *, findings=()) -> RunnerResult:
    return RunnerResult(key, success=True, findings=findings, stats={"duration_ms": 3})


def _finding(
    *,
    file_path: str = "app/auth.py",
    category: str = "security/xss",
    severity: str = "HIGH",
    confidence: float = 0.9,
    line_start: int | None = None,
    line_end: int | None = None,
) -> dict:
    """A fully contract-valid finding dict (docs/AGENTS.md §4)."""
    return {
        "file_path": file_path,
        "category": category,
        "severity": severity,
        "confidence": confidence,
        "title": "XSS risk in login view",
        "description": "User-controlled input reaches the response unescaped.",
        "reason_summary": "Evidence-only summary.",
        "line_start": line_start,
        "line_end": line_end,
    }


@pytest.fixture
def valid_scope() -> AgentScope:
    return AgentScope(
        review_id=REVIEW_ID,
        repository_id=REPO_ID,
        pr_number=42,
        pr_title="Add authentication",
        pr_description="Implements login flow.",
        base_ref="main",
        head_ref="feat/auth",
        language="python",
        changed_files=(
            FileSlice(
                file_path="app/auth.py",
                patch=(
                    "@@ -1,5 +1,7 @@\n"
                    " def login():\n"
                    "+    require_active(user)\n"
                    "     return token(user)"
                ),
                new_start=1,
                new_end=7,
                change_kind=ChangeKind.MODIFIED,
            ),
        ),
    )


@pytest.fixture
def settings() -> Settings:
    return Settings(orchestrator_max_agent_retries=2)


async def test_success_with_findings_lands_on_deciding_checkpoint(
    valid_scope: AgentScope, settings: Settings
) -> None:
    runner = ScriptedRunner(
        {
            "security": [_success("security", findings=(_finding(),))],
            "quality": [_success("quality")],
        }
    )
    store = MemoryOrchestratorStore()
    outcome = await run_review(
        valid_scope, runner=runner, store=store, settings=settings
    )

    assert outcome.status == ReviewStatus.DECIDING.value
    assert len(outcome.findings) == 1
    assert sorted(outcome.agent_keys) == ["quality", "security"]

    record = store.reviews[str(REVIEW_ID)]
    assert record["status"] == "DECIDING"
    statuses = [h["status"] for h in record["status_history"]]
    assert statuses == ["PROCESSING", "AGENTS_RUNNING", "DECIDING"]
    assert len(store.tasks) == 2
    assert all(t["status"] == TaskStatus.SUCCESS.value for t in store.tasks)
    assert len(store.metrics) == 2
    assert "completed_at" not in store.reviews[str(REVIEW_ID)]  # checkpoint: not done

    # Phase 7: the candidate is consolidated into the store, embedded, and the
    # review is handed to the decision layer only after that succeeded.
    assert len(store.findings) == 1
    assert store.findings[0]["publication_status"] == "CANDIDATE"
    assert store.findings[0]["duplicate_group"] is None  # singleton: no group
    assert len(store.finding_groups) == 0
    assert len(store.embeddings) == 1
    assert store.embeddings[0]["finding_id"] == store.findings[0]["id"]
    assert store.embeddings[0]["model"] == "mock/text-embedding"
    supervisor_notes = record["supervisor_notes"]
    assert any("consolidated 1 findings" in note for note in supervisor_notes)
    assert any("handed to decision" in note for note in supervisor_notes)


async def test_success_without_findings_completes(
    valid_scope: AgentScope, settings: Settings
) -> None:
    runner = ScriptedRunner(
        {"security": [_success("security")], "quality": [_success("quality")]}
    )
    store = MemoryOrchestratorStore()
    outcome = await run_review(
        valid_scope, runner=runner, store=store, settings=settings
    )

    assert outcome.status == ReviewStatus.COMPLETED.value
    assert outcome.findings == []
    assert store.reviews[str(REVIEW_ID)]["status"] == "COMPLETED"
    assert "completed_at" in store.reviews[str(REVIEW_ID)]


async def test_permanent_failure_diagnoses_failed_and_keeps_partial_findings(
    valid_scope: AgentScope, settings: Settings
) -> None:
    runner = ScriptedRunner(
        {
            "security": [_success("security", findings=(_finding(severity="MEDIUM"),))],
            "quality": [
                RunnerResult(
                    "quality", success=False, retryable=False, error="hard boom"
                )
            ],
        }
    )
    store = MemoryOrchestratorStore()
    outcome = await run_review(
        valid_scope, runner=runner, store=store, settings=settings
    )

    assert outcome.status == ReviewStatus.FAILED.value
    assert outcome.root_cause and "quality" in outcome.root_cause
    assert len(outcome.findings) == 1  # partial findings survive
    record = store.reviews[str(REVIEW_ID)]
    assert record["failure_reason"] == outcome.root_cause
    statuses = [h["status"] for h in record["status_history"]]
    assert statuses[-1] == "FAILED"

    tasks_by_key = {t["agent_key"]: t for t in store.tasks}
    assert tasks_by_key["security"]["status"] == TaskStatus.SUCCESS.value
    assert tasks_by_key["quality"]["status"] == TaskStatus.FAILED.value
    assert tasks_by_key["quality"]["last_error"] == "hard boom"

    # The partial valid finding is consolidated before the review is diagnosed
    # FAILED (docs/AGENTS.md §2: partial valid findings still flow to the
    # consolidation layer).
    assert len(store.findings) == 1
    assert store.findings[0]["severity"] == "MEDIUM"
    assert len(store.embeddings) == 1


async def test_retry_exhaustion_records_retries_and_note(
    valid_scope: AgentScope, settings: Settings
) -> None:
    runner = ScriptedRunner(
        {
            "security": [
                RunnerResult(
                    "security", success=False, retryable=True, error="transient"
                ),
                RunnerResult(
                    "security", success=False, retryable=True, error="transient"
                ),
            ]
        }
    )
    store = MemoryOrchestratorStore()
    outcome = await run_review(
        valid_scope, runner=runner, store=store, settings=settings
    )

    assert outcome.status == ReviewStatus.FAILED.value
    task = store.tasks[0]
    assert task["status"] == TaskStatus.FAILED.value
    assert task["retry_count"] == 2
    assert any("partial findings still flow" in n for n in outcome.notes)
    assert store.reviews[str(REVIEW_ID)]["status"] == "FAILED"


async def test_invalid_scope_fails_before_fan_out(
    valid_scope: AgentScope, settings: Settings
) -> None:
    empty_scope = AgentScope(
        review_id=REVIEW_ID,
        repository_id=REPO_ID,
        pr_number=42,
        pr_title="t",
        pr_description="",
        base_ref="main",
        head_ref="feat/auth",
    )
    store = MemoryOrchestratorStore()
    outcome = await run_review(
        empty_scope, runner=ScriptedRunner({}), store=store, settings=settings
    )
    assert outcome.status == ReviewStatus.FAILED.value
    assert outcome.agent_keys == ()
    assert outcome.tasks == []
    assert store.reviews[str(REVIEW_ID)]["status"] == "FAILED"
    assert any("before agent fan-out" in n for n in outcome.notes)


async def test_outcome_is_json_serialisable(
    valid_scope: AgentScope, settings: Settings
) -> None:
    import json

    runner = ScriptedRunner(
        {"security": [_success("security", findings=(_finding(),))]}
    )
    outcome = await run_review(
        valid_scope,
        runner=runner,
        store=MemoryOrchestratorStore(),
        settings=settings,
    )
    payload = outcome.to_dict()
    json.dumps(payload)  # must not raise
    assert payload["status"] == ReviewStatus.DECIDING.value
