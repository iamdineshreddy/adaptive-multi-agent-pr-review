"""Phase 8 + Phase 9 service wiring: ARUM scoring + budget/gate selection run
inside ``run_review``.

Proves the decision contract through the orchestrator: a successful review with
findings lands on ``DECIDING`` with every candidate scored, features/utility
persisted on the findings, budget selection applied (``SCHEDULED`` /
``SUPPRESSED``), the review's ``arum_version`` + ``budget_cap`` recorded, and a
ranked reproducibility trace produced. Decision-layer failure diagnoses the
review as ``FAILED``.
"""

from __future__ import annotations

import uuid

import pytest

from app.adaptive import MemoryDecisionTrace
from app.agents.contract import AgentScope, ChangeKind, FileSlice
from app.config.settings import Settings
from app.models.enums import ReviewStatus, RiskClass
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
    severity: str = "HIGH",
    confidence: float = 0.9,
    category: str = "security/xss",
    **overrides: object,
) -> dict:
    raw: dict[str, object] = {
        "file_path": "app/auth.py",
        "category": category,
        "severity": severity,
        "confidence": confidence,
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


async def test_success_scores_candidates_and_records_decision(
    valid_scope: AgentScope, settings: Settings
) -> None:
    runner = ScriptedRunner(
        {
            "security": [_success("security", findings=(_finding(),))],
            "quality": [_success("quality")],
        }
    )
    store = MemoryOrchestratorStore()
    trace = MemoryDecisionTrace()
    outcome = await run_review(
        valid_scope, runner=runner, store=store, settings=settings, trace=trace
    )

    assert outcome.status == ReviewStatus.DECIDING.value
    record = store.reviews[str(REVIEW_ID)]
    assert record["arum_version"] == "v1"
    assert record["budget_cap"] == settings.review_budget_high  # no risk metadata

    assert len(store.findings) == 1
    scored = store.findings[0]
    # HIGH severity + 0.9 confidence trips the protected gate -> selected.
    assert scored["publication_status"] == "SCHEDULED"
    assert scored["arum_version"] == "v1"
    assert scored["arum_features"]["severity"] == 0.8  # HIGH mapped
    assert scored["arum_features"]["agent_agreement"] == 0.5  # single agent
    assert isinstance(scored["arum_utility"], float)

    rows = trace.read_all()
    assert len(rows) == 1  # one candidate, one decision
    assert rows[0]["finding_id"] == scored["id"]
    assert rows[0]["utility"] == scored["arum_utility"]
    assert rows[0]["weights"]["version"] == "v1"
    assert rows[0]["inputs_hash"]
    assert rows[0]["budget_cap"] == settings.review_budget_high
    assert rows[0]["safety_gate"] == "high_confidence_high_severity"
    assert rows[0]["selected"] is True

    notes = record["supervisor_notes"]
    assert any("ARUM selected 1 of 1" in n for n in notes)


async def test_cross_agent_group_scores_agreement(
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
    trace = MemoryDecisionTrace()
    await run_review(
        valid_scope, runner=runner, store=store, settings=settings, trace=trace
    )

    assert len(store.finding_groups) == 1
    assert len(store.findings) == 2
    assert len(trace.read_all()) == 1  # one redundancy group -> one decision
    representative = next(row for row in store.findings if "arum_features" in row)
    group_row = representative["duplicate_group"]
    assert group_row is not None  # consolidated into the group
    features = representative["arum_features"]
    assert features["severity"] == 0.8
    assert features["agent_agreement"] == 1.0  # two distinct agents agree
    assert features["redundancy"] > 0.0  # group_size > 1
    assert representative["publication_status"] == "SCHEDULED"
    assert trace.read_all()[0]["finding_id"] == representative["id"]
    assert trace.read_all()[0]["group_id"] == group_row
    non_representative = next(
        row for row in store.findings if row["id"] != representative["id"]
    )
    assert non_representative.get("arum_features") is None
    # The duplicate member is absorbed into the representative's decision.
    assert non_representative["publication_status"] == "SUPPRESSED"


async def test_decision_layer_change_diagnoses_failed(
    valid_scope: AgentScope, settings: Settings, monkeypatch: pytest.MonkeyPatch
) -> None:
    from app.orchestrator import service as service_module

    async def _stuck(  # type: ignore[no-untyped-def]
        ctx, review_id, summary
    ) -> str:
        return "ARUM decision failed: decision kernel unavailable"

    monkeypatch.setattr(service_module, "_decide_findings", _stuck)
    runner = ScriptedRunner(
        {"security": [_success("security", findings=(_finding(),))]}
    )
    store = MemoryOrchestratorStore()
    outcome = await run_review(
        valid_scope, runner=runner, store=store, settings=settings
    )

    assert outcome.status == ReviewStatus.FAILED.value
    assert outcome.root_cause and "ARUM decision failed" in outcome.root_cause
    record = store.reviews[str(REVIEW_ID)]
    assert record["failure_reason"] == outcome.root_cause
    # Candidates are still persisted even though the decision layer failed.
    assert len(store.findings) == 1


async def test_no_findings_never_scores(
    valid_scope: AgentScope, settings: Settings
) -> None:
    runner = ScriptedRunner(
        {"security": [_success("security")], "quality": [_success("quality")]}
    )
    store = MemoryOrchestratorStore()
    trace = MemoryDecisionTrace()
    outcome = await run_review(
        valid_scope, runner=runner, store=store, settings=settings, trace=trace
    )
    assert outcome.status == ReviewStatus.COMPLETED.value
    assert trace.read_all() == []
    assert store.reviews[str(REVIEW_ID)].get("arum_version") is None


async def test_budget_cap_truncates_after_rank(
    valid_scope: AgentScope, settings: Settings
) -> None:
    """A LOW-risk review (cap 5) with six low-value singletons truncates one."""
    findings = tuple(
        _finding(
            severity="LOW",
            confidence=0.2,
            file_path=f"app/module{i}.py",
        )
        for i in range(6)
    )
    runner = ScriptedRunner({"security": [_success("security", findings=findings)]})
    store = MemoryOrchestratorStore()
    store.reviews[str(REVIEW_ID)] = {"risk_class": RiskClass.LOW}
    trace = MemoryDecisionTrace()
    await run_review(
        valid_scope, runner=runner, store=store, settings=settings, trace=trace
    )

    record = store.reviews[str(REVIEW_ID)]
    assert record["budget_cap"] == settings.review_budget_low  # 5
    assert record["selected_count"] == 5
    assert record["suppressed_count"] == 1

    scheduled = [f for f in store.findings if f["publication_status"] == "SCHEDULED"]
    suppressed = [f for f in store.findings if f["publication_status"] == "SUPPRESSED"]
    assert len(scheduled) == 5
    assert len(suppressed) == 1
    assert all(f.get("safety_gate") is None for f in suppressed)

    rows = trace.read_all()
    assert len(rows) == 6
    assert sum(1 for r in rows if r["selected"]) == 5
    assert sum(1 for r in rows if not r["selected"]) == 1
    assert {r["budget_cap"] for r in rows} == {settings.review_budget_low}

    notes = record["supervisor_notes"]
    assert any("ARUM selected 5 of 6" in n for n in notes)
    assert any("budget-truncated" in n for n in notes)
