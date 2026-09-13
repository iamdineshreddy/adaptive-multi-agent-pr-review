"""Phase 7 service wiring: consolidation runs inside ``run_review``.

Proves the end-to-end contract through the orchestrator service — cross-agent
redundancy produces a persisted group, provider/persistence failures diagnose
a FAILED review, and partial findings always flow to consolidation.
"""

from __future__ import annotations

import uuid
from collections.abc import Sequence

import pytest

from app.agents.contract import AgentScope, ChangeKind, FileSlice
from app.config.settings import Settings
from app.consolidation.embeddings import EmbeddingsError
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


class FailingEmbeddingsProvider:
    name = "failing"
    model = "failing"

    async def embed_texts(self, texts: Sequence[str]) -> list[tuple[float, ...]]:
        raise EmbeddingsError("embeddings API is down")


def _success(key: str, *, findings=()) -> RunnerResult:
    return RunnerResult(key, success=True, findings=findings, stats={"duration_ms": 3})


def _finding(**overrides: object) -> dict:
    raw: dict[str, object] = {
        "file_path": "app/auth.py",
        "category": "security/xss",
        "severity": "HIGH",
        "confidence": 0.9,
        "title": "XSS risk in login view",
        "description": "User-controlled input is rendered unescaped.",
        "reason_summary": "Evidence-only summary.",
    }
    raw.update(overrides)
    return raw


@pytest.fixture
def settings() -> Settings:
    return Settings(orchestrator_max_agent_retries=2)


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
                patch="@@ -1,5 +1,7 @@",
                new_start=1,
                new_end=7,
                change_kind=ChangeKind.MODIFIED,
            ),
        ),
    )


async def test_cross_agent_redundancy_group_persisted(
    valid_scope: AgentScope, settings: Settings
) -> None:
    finding = _finding()
    runner = ScriptedRunner(
        {
            "security": [_success("security", findings=(finding,))],
            "quality": [_success("quality", findings=(finding,))],
        }
    )
    store = MemoryOrchestratorStore()
    outcome = await run_review(
        valid_scope, runner=runner, store=store, settings=settings
    )

    assert outcome.status == ReviewStatus.DECIDING.value
    assert len(store.findings) == 2  # exact cross-agent dup, not dropped
    assert len(store.finding_groups) == 1
    group = store.finding_groups[0]
    assert group["member_count"] == 2
    expected_method = "hybrid: embedding cosine + category + proximity"
    assert group["redundancy_method"] == expected_method
    assert group["max_pairwise_similarity"] == pytest.approx(1.0)

    grouped_ids = {f["duplicate_group"] for f in store.findings}
    assert grouped_ids == {group["id"]}
    assert {f["agent_key"] for f in store.findings} == {"security", "quality"}
    assert all(f["publication_status"] == "CANDIDATE" for f in store.findings)

    notes = store.reviews[str(REVIEW_ID)]["supervisor_notes"]
    assert any("redundancy groups" in n for n in notes)


async def test_embedding_provider_failure_diagnoses_failed_review(
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
        embeddings_provider=FailingEmbeddingsProvider(),
    )

    assert outcome.status == ReviewStatus.FAILED.value
    assert outcome.root_cause and "redundancy detection failed" in outcome.root_cause
    assert store.reviews[str(REVIEW_ID)]["failure_reason"] == outcome.root_cause
    statuses = [h["status"] for h in store.reviews[str(REVIEW_ID)]["status_history"]]
    assert statuses[-1] == "FAILED"


async def test_invalid_findings_all_dropped_completes(
    valid_scope: AgentScope, settings: Settings
) -> None:
    malformed = {"file_path": "app/auth.py", "severity": "HIGH"}
    runner = ScriptedRunner({"security": [_success("security", findings=(malformed,))]})
    store = MemoryOrchestratorStore()
    outcome = await run_review(
        valid_scope, runner=runner, store=store, settings=settings
    )

    assert outcome.status == ReviewStatus.COMPLETED.value
    assert store.findings == []
    assert store.finding_groups == []
    assert store.embeddings == []
    notes = store.reviews[str(REVIEW_ID)]["supervisor_notes"]
    assert any("dropped invalid" in n for n in notes)


async def test_day_one_professional_checkpoints(
    valid_scope: AgentScope, settings: Settings
) -> None:
    """Regression guard: a successful review with findings must never reach a
    terminal 'done' before the decision layer exists (Phase 8)."""
    runner = ScriptedRunner(
        {"security": [_success("security", findings=(_finding(),))]}
    )
    store = MemoryOrchestratorStore()
    outcome = await run_review(
        valid_scope, runner=runner, store=store, settings=settings
    )
    assert outcome.status == ReviewStatus.DECIDING.value
    assert "completed_at" not in store.reviews[str(REVIEW_ID)]
    # Tasks still reflect per-agent outcomes, decision layer not involved.
    assert {t["status"] for t in store.tasks} == {TaskStatus.SUCCESS.value}
