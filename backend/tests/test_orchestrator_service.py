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
from app.agents.registry import agent_keys as _registered_keys
from app.config.settings import Settings
from app.models.enums import ReviewMode, ReviewStatus, TaskStatus
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


def _previous_finding(
    *,
    finding_id: str | None = None,
    file_path: str = "app/auth.py",
    category: str = "security/xss",
    line_start: int | None = 1,
    line_end: int | None = 7,
) -> dict:
    """A previously-persisted finding row shaped for ``plan_review_delta``."""
    return {
        "id": finding_id or str(uuid.uuid4()),
        "file_path": file_path,
        "category": category,
        "line_start": line_start,
        "line_end": line_end,
    }


async def test_synchronize_targets_only_stale_agents_and_records_iteration(
    valid_scope: AgentScope, settings: Settings
) -> None:
    # A previous security finding sits exactly inside the changed region -> stale.
    previous = [_previous_finding(line_start=1, line_end=7)]
    runner = ScriptedRunner(
        {
            "security": [_success("security", findings=(_finding(),))],
            "quality": [
                _success("quality", findings=(_finding(category="quality/x"),))
            ],
        }
    )
    store = MemoryOrchestratorStore()
    outcome = await run_review(
        valid_scope,
        runner=runner,
        store=store,
        settings=settings,
        mode=ReviewMode.SYNCHRONIZE,
        previous_findings=previous,
        head_sha="abc123def",
    )

    # Only the targeted agent ran; quality was outside the target set.
    assert sorted(outcome.agent_keys) == ["security"]
    assert [t["agent_key"] for t in store.tasks] == ["security"]
    assert outcome.status == ReviewStatus.ITERATING.value

    record = store.reviews[str(REVIEW_ID)]
    statuses = [h["status"] for h in record["status_history"]]
    assert "ITERATING" in statuses
    assert statuses.index("ITERATING") < statuses.index("AGENTS_RUNNING")
    assert statuses[-1] == "ITERATING"

    # The iteration row captures the round's disposition + cost envelope.
    assert len(store.iterations) == 1
    row = store.iterations[0]
    assert row["review_id"] == str(REVIEW_ID)
    assert row["iteration"] == 1
    assert row["base_sha"] == "main"
    assert row["head_sha"] == "abc123def"
    assert row["resolved_count"] == 0
    assert row["stale_count"] == 1
    assert row["published_count"] == 1  # the single ARUM-selected candidate
    assert row["agents_invoked"]["targeted"] == ("security",)

    # The stale disposition was persisted on the previous finding.
    other = store.findings[0]
    assert other["publication_status"] == "SCHEDULED"
    assert any("iteration delta" in note for note in record["supervisor_notes"])


async def test_synchronize_all_resolved_records_iteration_and_completes(
    valid_scope: AgentScope, settings: Settings
) -> None:
    # The only previous finding was in a file the new commit removed -> resolved,
    # and no agent owns the removed region, so the full agent set is the fallback.
    valid = AgentScope(
        review_id=REVIEW_ID,
        repository_id=REPO_ID,
        pr_number=42,
        pr_title="Remove dead code",
        pr_description="",
        base_ref="main",
        head_ref="feat/cleanup",
        changed_files=(
            FileSlice(
                file_path="app/dead.py",
                patch="@@ -1,2 +0,0 @@\n",
                new_start=None,
                new_end=None,
                change_kind=ChangeKind.REMOVED,
            ),
        ),
    )
    previous = [_previous_finding(file_path="app/dead.py")]
    runner = ScriptedRunner({k: [_success(k)] for k in _registered_keys()})
    store = MemoryOrchestratorStore()
    outcome = await run_review(
        valid,
        runner=runner,
        store=store,
        settings=settings,
        mode=ReviewMode.SYNCHRONIZE,
        previous_findings=previous,
        head_sha="ffffffff",
    )

    # Stale category set is empty -> full agent set fallback runs all agents.
    assert sorted(outcome.agent_keys) == sorted(_registered_keys())
    assert len(store.iterations) == 1
    row = store.iterations[0]
    assert row["resolved_count"] == 1
    assert row["stale_count"] == 0
    assert row["head_sha"] == "ffffffff"
    # No consolidated findings -> completed, not iterating.
    assert outcome.status == ReviewStatus.COMPLETED.value
    assert store.reviews[str(REVIEW_ID)]["status"] == "COMPLETED"


async def test_synchronize_without_previous_findings_is_a_plain_review(
    valid_scope: AgentScope, settings: Settings
) -> None:
    runner = ScriptedRunner(
        {"security": [_success("security", findings=(_finding(),))]}
    )
    store = MemoryOrchestratorStore()
    outcome = await run_review(
        valid_scope,
        runner=runner,
        store=store,
        settings=settings,
        mode=ReviewMode.SYNCHRONIZE,
        previous_findings=[],
    )

    assert outcome.status == ReviewStatus.DECIDING.value
    assert len(store.iterations) == 0  # first sync: full review, no iteration
    assert "ITERATING" not in [
        h["status"] for h in store.reviews[str(REVIEW_ID)]["status_history"]
    ]


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
    # Phase 8/9: the candidate is scored by ARUM and selected under the budget
    # (HIGH + high confidence -> protected gate) -> scheduled for publication.
    assert store.findings[0]["publication_status"] == "SCHEDULED"
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
