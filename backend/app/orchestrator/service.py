"""Orchestration service: run the LangGraph state machine and persist outcomes.

This is the broker-free core of Phase 6. It takes an ``AgentScope`` (already
built from DB/GitHub data), drives the graph, and records everything
(review status transitions, review_tasks, agent_metrics, supervisor notes,
root cause) through an injected :class:`OrchestratorStore`. Production entry
points (the ``orchestrator.run_review`` Celery task) wrap this core.
"""

from __future__ import annotations

import time
import uuid
from dataclasses import dataclass, field
from datetime import UTC, datetime
from typing import Any

import structlog

from app.adaptive import (
    Decision,
    DecisionTrace,
    MemoryDecisionTrace,
    compute_inputs_hash,
    disagreement_variance,
    extract_features,
    load_weights,
    rank_decisions,
    select_decisions,
    utility,
)
from app.adaptive.features import ArumFeatures
from app.adaptive.weights import ArumWeights
from app.agents.contract import AgentScope, scope_validation_error
from app.agents.registry import agent_metadata, queue_for_agent
from app.config.settings import Settings, get_settings
from app.consolidation import (
    build_embeddings_provider,
    consolidate_review,
    normalise_findings,
)
from app.consolidation.datatypes import ConsolidationSummary, FindingWrite
from app.consolidation.embeddings import EmbeddingsProvider
from app.models.enums import ReviewStatus, RiskClass, TaskStatus
from app.orchestrator.graph import build_orchestration_graph
from app.orchestrator.persistence import (
    MemoryOrchestratorStore,
    OrchestratorStore,
    TaskWrite,
    utc_now,
)
from app.orchestrator.runners import AgentRunner, InProcessAgentRunner
from app.orchestrator.state import TERMINAL_FAILED, empty_state

logger = structlog.get_logger(__name__)


@dataclass
class ReviewOutcome:
    """Serialisable result of one orchestration run."""

    review_id: str
    status: str  # ReviewStatus.value persisted on the review row
    agent_keys: tuple[str, ...]
    findings: list[dict[str, Any]]
    tasks: list[dict[str, Any]]
    notes: list[str]
    root_cause: str | None
    duration_ms: int

    def to_dict(self) -> dict[str, Any]:
        return {
            "review_id": self.review_id,
            "status": self.status,
            "agent_keys": list(self.agent_keys),
            "findings": self.findings,
            "tasks": self.tasks,
            "notes": self.notes,
            "root_cause": self.root_cause,
            "duration_ms": self.duration_ms,
        }


@dataclass(frozen=True)
class RunContext:
    runner: AgentRunner
    store: OrchestratorStore
    settings: Settings
    embeddings_provider: EmbeddingsProvider | None = None
    trace: DecisionTrace | None = None
    started_at: datetime = field(default_factory=lambda: utc_now())


async def run_review(
    scope: AgentScope,
    *,
    runner: AgentRunner | None = None,
    store: OrchestratorStore | None = None,
    settings: Settings | None = None,
    embeddings_provider: EmbeddingsProvider | None = None,
    trace: DecisionTrace | None = None,
) -> ReviewOutcome:
    """Execute the orchestration graph over ``scope`` and persist its outcome.

    ``runner`` defaults to the in-process runner (tests/local), ``store`` to
    the PostgreSQL store (production). DB-free unit tests inject a
    :class:`MemoryOrchestratorStore` and a mock-backed runner. Consolidation
    (Phase 7) uses ``embeddings_provider`` (default: built from settings; the
    ``mock`` provider keeps local runs offline and deterministic). ARUM
    decision traces (Phase 8) go to ``trace`` — a file-backed writer in
    production, an in-memory sink by default in tests.
    """
    cfg = settings or get_settings()
    ctx = RunContext(
        runner=runner or InProcessAgentRunner(),
        store=store or MemoryOrchestratorStore(),
        settings=cfg,
        embeddings_provider=embeddings_provider,
        trace=trace,
    )
    review_id = str(uuid.UUID(str(scope.review_id)))
    started = time.monotonic()

    invalid = scope_validation_error(scope)
    if invalid is not None:
        return await _fail_early(ctx, review_id, invalid, started)

    await ctx.store.transition(review_id, ReviewStatus.PROCESSING)
    await ctx.store.transition(review_id, ReviewStatus.AGENTS_RUNNING)

    agent_ids = await ctx.store.sync_agents(agent_metadata())
    graph = build_orchestration_graph(
        ctx.runner,
        max_retries=cfg.orchestrator_max_agent_retries,
        agent_timeout_seconds=cfg.orchestrator_agent_timeout_seconds,
    )
    final = await graph.ainvoke(
        empty_state(review_id, str(scope.repository_id), scope.to_dict())
    )

    duration_ms = int((time.monotonic() - started) * 1000)
    outcome = await _persist(ctx, review_id, scope, agent_ids, final, duration_ms)
    logger.info(
        "orchestration_completed",
        review_id=review_id,
        terminal=final["terminal"],
        persisted_status=outcome.status,
        agents_run=len(outcome.agent_keys),
        findings=len(outcome.findings),
        failed_agents=sorted(
            {
                t["agent_key"]
                for t in outcome.tasks
                if t["status"] == TaskStatus.FAILED.value
            }
        ),
        duration_ms=outcome.duration_ms,
        root_cause=outcome.root_cause,
    )
    return outcome


async def _persist(
    ctx: RunContext,
    review_id: str,
    scope: AgentScope,
    agent_ids: dict[str, str],
    final: dict[str, Any],
    duration_ms: int,
) -> ReviewOutcome:
    scope_data = scope.to_dict()
    now: datetime = utc_now()
    tasks: list[dict[str, Any]] = []
    findings: list[dict[str, Any]] = []
    agent_inputs: list[tuple[str, str, dict[str, Any]]] = []
    notes = list(final.get("notes") or [])

    for key in final["plan"].get("agent_keys") or []:
        success = next(
            (r for r in final.get("results", []) if r["agent_key"] == key), None
        )
        failed = next(
            (f for f in final.get("failed", []) if f["agent_key"] == key), None
        )
        status = TaskStatus.SUCCESS if success is not None else TaskStatus.FAILED
        retry_count = int(final.get("retries", {}).get(key, 0))
        await ctx.store.create_review_task(
            review_id,
            TaskWrite(
                agent_key=key,
                agent_id=agent_ids[key],
                queue_name=queue_for_agent(key),
                status=status,
                retry_count=retry_count,
                last_error=failed["error"] if failed else None,
                scope=scope_data,
                started_at=now,
                completed_at=now,
            ),
        )
        tasks.append(
            {
                "agent_key": key,
                "status": status.value,
                "retry_count": retry_count,
                "last_error": failed["error"] if failed else None,
            }
        )
        if success is not None:
            for finding in success["findings"] or []:
                if isinstance(finding, dict):
                    agent_inputs.append((key, agent_ids[key], finding))
            findings.extend(success["findings"] or [])
            if success.get("stats"):
                await ctx.store.create_agent_metric(
                    review_id,
                    agent_key=key,
                    agent_id=agent_ids[key],
                    stats=success["stats"],
                )

    for note in notes:
        await ctx.store.append_note(review_id, note)

    consolidation_error: str | None = None
    consolidated = False
    summary: ConsolidationSummary | None = None
    if agent_inputs:
        consolidated, consolidation_error, summary = await _consolidate_findings(
            ctx, review_id, scope, agent_inputs
        )

    decision_error: str | None = None
    terminal = final.get("terminal")
    permanent_failures = bool(final.get("failed"))
    root_cause = final.get("root_cause")
    if (
        consolidated
        and summary is not None
        and consolidation_error is None
        and not permanent_failures
        and terminal != TERMINAL_FAILED
    ):
        decision_error = await _decide_findings(ctx, review_id, summary)

    if consolidation_error is not None:
        review_status = ReviewStatus.FAILED
        await ctx.store.transition(
            review_id,
            review_status,
            failure_reason=consolidation_error,
            root_cause=consolidation_error,
            completed_at=now,
        )
    elif decision_error is not None:
        review_status = ReviewStatus.FAILED
        await ctx.store.transition(
            review_id,
            review_status,
            failure_reason=decision_error,
            root_cause=decision_error,
            completed_at=now,
        )
    elif permanent_failures or terminal == TERMINAL_FAILED:
        review_status = ReviewStatus.FAILED
        await ctx.store.transition(
            review_id,
            review_status,
            failure_reason=root_cause,
            root_cause=root_cause,
            completed_at=now,
        )
    elif consolidated:
        review_status = ReviewStatus.DECIDING  # checkpoint: not published yet
        await ctx.store.transition(review_id, review_status)
    else:
        review_status = ReviewStatus.COMPLETED
        await ctx.store.transition(review_id, review_status, completed_at=now)

    return ReviewOutcome(
        review_id=review_id,
        status=review_status.value,
        agent_keys=tuple(final["plan"].get("agent_keys") or []),
        findings=findings,
        tasks=tasks,
        notes=notes,
        root_cause=(
            root_cause
            if (permanent_failures or terminal == TERMINAL_FAILED)
            else (consolidation_error or decision_error)
        ),
        duration_ms=duration_ms,
    )


async def _decide_findings(
    ctx: RunContext,
    review_id: str,
    summary: ConsolidationSummary,
) -> str | None:
    """Score consolidated candidates with ARUM, then apply budget + safety gates.

    Returns ``None`` on success or an error string that diagnoses the review
    ``FAILED`` (consistent with the consolidation policy: nothing is silently
    dropped from the decision trail). Each candidate is annotated with the
    budget cap and gate reason it ran under, sorted by ARUM rank, and appended
    to the reproducibility trace (ARUM.md §10). Selected candidates become
    ``SCHEDULED``; gated/budget-truncated ones become ``SUPPRESSED`` with their
    duplicate-group members suppressed alongside the representative. The review
    stays on ``DECIDING`` — actually posting findings is the publisher step
    (a later phase), so nothing here pretends publication happened.
    """
    try:
        weights = load_weights(version=ctx.settings.arum_weights_version)
        decisions = _score_candidates(review_id, summary, weights)
        ranked = rank_decisions(decisions)
        risk_class = await ctx.store.review_risk_class(review_id)
        cap = _budget_cap(risk_class, ctx.settings)
        selection = select_decisions(
            ranked,
            cap=cap,
            high_confidence=ctx.settings.arum_gate_high_confidence,
            low_confidence=ctx.settings.arum_gate_low_confidence,
            high_redundancy=ctx.settings.arum_gate_high_redundancy,
        )
        trace = ctx.trace or MemoryDecisionTrace()
        for decision in selection.decisions:
            trace.append(decision)
            await ctx.store.apply_decision(review_id, decision)
        suppressed_count = len(selection.decisions) - selection.selected_count
        await ctx.store.record_decision(
            review_id,
            arum_version=weights.version,
            budget_cap=cap,
            selected_count=selection.selected_count,
            suppressed_count=suppressed_count,
        )
        note = (
            "supervisor: ARUM selected "
            f"{selection.selected_count} of {len(selection.decisions)} "
            f"candidates (version={weights.version}, budget cap {cap}; "
            f"{selection.mandatory_count} critical-mandatory, "
            f"{selection.protected_count} protected, "
            f"{selection.suppressed_by_gate} gate-suppressed, "
            f"{selection.truncated_by_budget} budget-truncated); "
            "publication itself is a later phase."
        )
        if risk_class is None:
            note += (
                " risk_class metadata missing on the review; "
                "used the high cap rather than truncating a valid selection."
            )
        await ctx.store.append_note(review_id, note)
        return None
    except Exception as exc:  # noqa: BLE001 - any decision failure must surface
        return f"ARUM decision failed: {exc}"


def _budget_cap(risk_class: RiskClass | None, cfg: Settings) -> int:
    """Resolve the per-review budget cap from the PR risk class (ARUM.md §7)."""
    if risk_class == RiskClass.LOW:
        return cfg.review_budget_low
    if risk_class == RiskClass.MEDIUM:
        return cfg.review_budget_medium
    return cfg.review_budget_high  # HIGH, and unknown/missing -> most permissive


def _score_candidates(
    review_id: str,
    summary: ConsolidationSummary,
    weights: ArumWeights,
) -> list[Decision]:
    """Build one :class:`Decision` per candidate from the merged summary.

    Singletons are scored alone (member_count 1); grouped findings use the
    redundancy group's member set for agent agreement + disagreement variance.
    """
    decisions: list[Decision] = []
    for group in summary.groups:
        members = [row for row in summary.findings if row.duplicate_group == group.id]
        if not members:
            raise RuntimeError(f"redundancy group {group.id} has no persisted members")
        representative = next(
            (row for row in members if row.id == group.representative_finding_id),
            members[0],
        )
        distinct_agents = len({row.agent_key for row in members})
        variance = disagreement_variance(members)
        features = extract_features(
            representative,
            member_count=group.member_count,
            distinct_agents=distinct_agents,
            variance=variance,
        )
        decisions.append(
            _decision(review_id, representative, group.id, features, weights)
        )
    for row in summary.findings:
        if row.duplicate_group is None:
            features = extract_features(
                row, member_count=1, distinct_agents=1, variance=0.0
            )
            decisions.append(_decision(review_id, row, None, features, weights))
    return decisions


def _decision(
    review_id: str,
    finding: FindingWrite,
    group_id: str | None,
    features: ArumFeatures,
    weights: ArumWeights,
) -> Decision:
    score = utility(features, weights)
    inputs_hash = compute_inputs_hash(
        "finding", review_id, finding.id, features.as_vector(), weights.as_vector()
    )
    return Decision(
        review_id=review_id,
        finding_id=finding.id,
        group_id=group_id,
        arum_version=weights.version,
        features=features,
        utility=score,
        weights=weights,
        inputs_hash=inputs_hash,
    )


async def _consolidate_findings(
    ctx: RunContext,
    review_id: str,
    scope: AgentScope,
    agent_inputs: list[tuple[str, str, dict[str, Any]]],
) -> tuple[bool, str | None, ConsolidationSummary | None]:
    """Consolidate + de-duplicate agent findings and persist the candidates.

    Returns ``(any_persisted, error, summary)``. Any failure returns an error
    string so the review is diagnosed ``FAILED`` instead of silently completed;
    partial valid findings still reach the decision layer on failure
    (AGENTS.md §2/§6).
    """
    try:
        provider = ctx.embeddings_provider or build_embeddings_provider(ctx.settings)
    except Exception as exc:  # noqa: BLE001 - provider construction failure
        return False, f"consolidation provider failed: {exc}", None

    normalised = normalise_findings(
        str(scope.review_id), str(scope.repository_id), agent_inputs
    )
    if normalised.errors:
        await ctx.store.append_note(
            review_id,
            "supervisor: consolidation dropped "
            f"{len(normalised.errors)} invalid/duplicate items "
            f"(first: {normalised.errors[0]}).",
        )
    if not normalised.findings:
        return False, None, None

    try:
        summary = await consolidate_review(
            str(scope.review_id),
            normalised.findings,
            provider,
            similarity_threshold=ctx.settings.consolidation_similarity_threshold,
            max_line_gap=ctx.settings.consolidation_max_line_gap,
        )
    except Exception as exc:  # noqa: BLE001 - provider/vector failures surface
        return False, f"redundancy detection failed: {exc}", None

    try:
        await ctx.store.create_findings(review_id, summary.findings)
        for group in summary.groups:
            await ctx.store.create_finding_group(review_id, group)
        grouped_members = [
            (row.id, row.duplicate_group)
            for row in summary.findings
            if row.duplicate_group
        ]
        if grouped_members:
            await ctx.store.update_finding_duplicates(review_id, grouped_members)
        if summary.embeddings:
            await ctx.store.create_embeddings(review_id, summary.embeddings)
    except Exception as exc:  # noqa: BLE001 - persistence failures surface
        return False, f"persisting consolidated findings failed: {exc}", None

    await ctx.store.append_note(
        review_id,
        "supervisor: consolidated "
        f"{len(summary.findings)} findings into "
        f"{len(summary.groups)} redundancy groups "
        f"(dropped={normalised.dropped}); handed to decision (Phase 8).",
    )
    return True, None, summary


async def _fail_early(
    ctx: RunContext, review_id: str, reason: str, started: float
) -> ReviewOutcome:
    """A scope that cannot be reviewed: diagnosed FAILED without agent fan-out."""
    now = datetime.now(UTC)
    await ctx.store.transition(
        review_id,
        ReviewStatus.FAILED,
        failure_reason=f"scope unusable: {reason}",
        root_cause=reason,
        completed_at=now,
    )
    await ctx.store.append_note(
        review_id, f"supervisor: review failed before agent fan-out ({reason})."
    )
    logger.warning(
        "orchestration_scope_invalid",
        review_id=review_id,
        reason=reason,
    )
    return ReviewOutcome(
        review_id=review_id,
        status=ReviewStatus.FAILED.value,
        agent_keys=(),
        findings=[],
        tasks=[],
        notes=[f"supervisor: review failed before agent fan-out ({reason})."],
        root_cause=reason,
        duration_ms=int((time.monotonic() - started) * 1000),
    )
