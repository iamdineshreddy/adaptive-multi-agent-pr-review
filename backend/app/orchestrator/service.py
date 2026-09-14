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
from collections.abc import Mapping, Sequence
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
from app.adaptive.memory import build_memory_snapshot
from app.adaptive.rag import max_relevance
from app.adaptive.weights import ArumWeights
from app.agents.contract import AgentScope, scope_validation_error
from app.agents.registry import agent_keys, agent_metadata, queue_for_agent
from app.config.settings import Settings, get_settings
from app.consolidation import (
    build_embeddings_provider,
    consolidate_review,
    content_hash,
    embedding_text,
    normalise_findings,
)
from app.consolidation.datatypes import (
    ConsolidationSummary,
    EmbeddingWrite,
    FindingWrite,
)
from app.consolidation.embeddings import EmbeddingsProvider
from app.models.enums import ReviewMode, ReviewStatus, RiskClass, TaskStatus
from app.orchestrator.graph import build_orchestration_graph
from app.orchestrator.iteration import IterationPlan, plan_review_delta
from app.orchestrator.persistence import (
    MemoryOrchestratorStore,
    OrchestratorStore,
    TaskWrite,
    utc_now,
)
from app.orchestrator.runners import (
    AgentRunner,
    InProcessAgentRunner,
    TargetedAgentRunner,
)
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
    mode: ReviewMode = ReviewMode.OPENED,
    previous_findings: Sequence[Mapping[str, Any]] | None = None,
    head_sha: str | None = None,
) -> ReviewOutcome:
    """Execute the orchestration graph over ``scope`` and persist its outcome.

    ``runner`` defaults to the in-process runner (tests/local), ``store`` to
    the PostgreSQL store (production). DB-free unit tests inject a
    :class:`MemoryOrchestratorStore` and a mock-backed runner. Consolidation
    (Phase 7) uses ``embeddings_provider`` (default: built from settings; the
    ``mock`` provider keeps local runs offline and deterministic). ARUM
    decision traces (Phase 8) go to ``trace`` — a file-backed writer in
    production, an in-memory sink by default in tests.

    Iterative reviews (Phase 12, FR-6): with ``mode == SYNCHRONIZE`` and
    ``previous_findings`` supplied, the diff against the last-reviewed state is
    classified before agent fan-out — resolved/stale dispositions are persisted,
    the review enters ``ITERATING``, and only the agents owning the touched
    regions are run (falling back to the full agent set when the delta has no
    prior findings to steer it). ``head_sha`` is recorded on the iteration row
    when known (the delta-fetch seam is a later wiring pass).
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

    iteration_plan: IterationPlan | None = None
    if mode is ReviewMode.SYNCHRONIZE and previous_findings is not None:
        iteration_plan = plan_review_delta(
            previous_findings,
            scope.changed_files,
            line_gap=cfg.iteration_line_gap,
        )
        if not iteration_plan.resolutions:
            iteration_plan = None  # first sync: nothing previous to re-check
        elif not iteration_plan.changed_paths:
            return await _no_op_iteration(
                ctx,
                review_id,
                scope,
                started,
                head_sha,
                plan=iteration_plan,
            )
        else:
            await ctx.store.transition(review_id, ReviewStatus.ITERATING)
            await ctx.store.apply_iteration_delta(review_id, iteration_plan.resolutions)
            targets = iteration_plan.targeted_agents or tuple(agent_keys())
            ctx = RunContext(
                runner=TargetedAgentRunner(ctx.runner, targets),
                store=ctx.store,
                settings=ctx.settings,
                embeddings_provider=ctx.embeddings_provider,
                trace=ctx.trace,
                started_at=ctx.started_at,
            )
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
    outcome = await _persist(
        ctx,
        review_id,
        scope,
        agent_ids,
        final,
        duration_ms,
        mode=mode,
        iteration_plan=iteration_plan,
        head_sha=head_sha,
    )
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
    *,
    mode: ReviewMode = ReviewMode.OPENED,
    iteration_plan: IterationPlan | None = None,
    head_sha: str | None = None,
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
    selected_count = 0
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
        decision_error, selected_count = await _decide_findings(ctx, review_id, summary)

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

    if iteration_plan is not None and not permanent_failures:
        invoked = tuple(final["plan"].get("agent_keys") or [])
        await ctx.store.record_iteration(
            review_id,
            base_sha=scope.base_ref,
            head_sha=head_sha or scope.head_ref,
            diff_stats=iteration_plan.diff_stats(),
            agents_invoked={
                "targeted": iteration_plan.targeted_agents or [],
                "planned": list(invoked),
            },
            published_count=selected_count,
            resolved_count=iteration_plan.resolved_count,
            stale_count=iteration_plan.stale_count,
        )
        if review_status not in (ReviewStatus.FAILED, ReviewStatus.COMPLETED):
            review_status = ReviewStatus.ITERATING
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


async def _no_op_iteration(
    ctx: RunContext,
    review_id: str,
    scope: AgentScope,
    started: float,
    head_sha: str | None,
    *,
    plan: IterationPlan,
) -> ReviewOutcome:
    """A synchronize with no re-checkable regions: record + complete, no agents.

    Reached when the delta's ``changed_paths`` is empty — every previous finding
    was either resolved (file removed) or carried forward untouched, so there is
    nothing for the targeted agents to re-scan. The iteration is recorded with
    zero agents invoked (FR-6.3 keeps unchanged rounds cost-free) and the review
    completes without fan-out.
    """
    now = utc_now()
    await ctx.store.record_iteration(
        review_id,
        base_sha=scope.base_ref,
        head_sha=head_sha or scope.head_ref,
        diff_stats=plan.diff_stats(),
        agents_invoked={"targeted": list(plan.targeted_agents), "planned": []},
        published_count=0,
        resolved_count=plan.resolved_count,
        stale_count=plan.stale_count,
    )
    await ctx.store.transition(review_id, ReviewStatus.COMPLETED, completed_at=now)
    await ctx.store.append_note(
        review_id,
        "supervisor: synchronize had no changed regions — "
        f"{plan.resolved_count} resolved, {plan.stale_count} stale, "
        f"{plan.keep_count} carried forward; no agents invoked.",
    )
    logger.info(
        "orchestration_noop_iteration",
        review_id=review_id,
        resolved=plan.resolved_count,
        stale=plan.stale_count,
        kept=plan.keep_count,
    )
    return ReviewOutcome(
        review_id=review_id,
        status=ReviewStatus.COMPLETED.value,
        agent_keys=(),
        findings=[],
        tasks=[],
        notes=[
            "supervisor: synchronize had no changed regions — "
            f"{plan.resolved_count} resolved, {plan.stale_count} stale, "
            f"{plan.keep_count} carried forward; no agents invoked."
        ],
        root_cause=None,
        duration_ms=int((time.monotonic() - started) * 1000),
    )


async def _decide_findings(
    ctx: RunContext,
    review_id: str,
    summary: ConsolidationSummary,
) -> tuple[str | None, int]:
    """Score consolidated candidates with ARUM, then apply budget + safety gates.

    Returns ``(None, selected_count)`` on success or ``(error, 0)`` that
    diagnoses the review ``FAILED`` (consistent with the consolidation policy:
    nothing is silently dropped from the decision trail). Each candidate is
    annotated with the budget cap and gate reason it ran under, sorted by ARUM
    rank, and appended to the reproducibility trace (ARUM.md §10). Selected
    candidates become ``SCHEDULED``; gated/budget-truncated ones become
    ``SUPPRESSED`` with their duplicate-group members suppressed alongside the
    representative. The review stays on ``DECIDING`` — actually posting findings
    is the publisher step (a later phase), so nothing here pretends publication
    happened.
    """
    try:
        weights = load_weights(version=ctx.settings.arum_weights_version)
        repository_id = summary.findings[0].repository_id if summary.findings else None
        snapshot = (
            await _refresh_repository_memory(ctx, repository_id)
            if repository_id
            else None
        )
        provider = build_embeddings_provider(ctx.settings)
        if repository_id:
            await _sync_repository_context(ctx, repository_id, provider)
        memories = await _candidate_memories(
            ctx, summary, snapshot, provider, repository_id=repository_id
        )
        decisions = _score_candidates(review_id, summary, weights, memories)
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
        if repository_id is not None and snapshot is None:
            note += (
                " no repository memory yet — historical actionability/rejection "
                "and RAG relevance scored as 0.0 (cold start); the memory "
                "worker rebuilds from developer feedback."
            )
        await ctx.store.append_note(review_id, note)
        return None, selection.selected_count
    except Exception as exc:  # noqa: BLE001 - any decision failure must surface
        return f"ARUM decision failed: {exc}", 0


def _budget_cap(risk_class: RiskClass | None, cfg: Settings) -> int:
    """Resolve the per-review budget cap from the PR risk class (ARUM.md §7)."""
    if risk_class == RiskClass.LOW:
        return cfg.review_budget_low
    if risk_class == RiskClass.MEDIUM:
        return cfg.review_budget_medium
    return cfg.review_budget_high  # HIGH, and unknown/missing -> most permissive


async def _refresh_repository_memory(
    ctx: RunContext, repository_id: str
) -> dict[str, Any] | None:
    """Load the decayed memory snapshot, building it lazily on first contact.

    The snapshot aggregates ``developer_feedback`` (Phase 10: ARUM.md §2/§6; the
    Phase 11 worker is the dedicated writer). With no evidence yet it stays
    ``None`` so the cold-start note is honest and the next review retries.
    """
    snapshot = await ctx.store.get_repository_memory(repository_id)
    if snapshot is not None:
        return snapshot
    tau = ctx.settings.arum_temporal_decay_days
    lam = ctx.settings.arum_decay_lambda
    events = await ctx.store.repository_feedback_events(
        repository_id, max_age_days=3.0 * tau
    )
    if not events:
        return None
    built = build_memory_snapshot(events, tau_days=tau, lam=lam)
    await ctx.store.upsert_repository_memory(
        repository_id, built, decay_params=built["decay_params"]
    )
    return built


async def _sync_repository_context(
    ctx: RunContext, repository_id: str, provider: EmbeddingsProvider
) -> None:
    """Embed the repository's active coding standards for RAG (FR-5.4).

    Dedupe by ``(resource_type, content_hash)`` so a re-sync does not re-embed
    standards already in vector storage; the embeddings table's unique
    constraint makes the write idempotent on the SQL path too.
    """
    standards = await ctx.store.active_standards(repository_id)
    if not standards:
        return
    existing = await ctx.store.embedding_hashes(
        repository_id, resource_types=("standard",)
    )
    missing = [
        standard
        for standard in standards
        if content_hash(_standard_text(standard)) not in existing
    ]
    if not missing:
        return
    texts = [_standard_text(standard) for standard in missing]
    vectors = await provider.embed_texts(texts)
    rows = [
        EmbeddingWrite(
            finding_id=None,
            repository_id=str(repository_id),
            content_hash=content_hash(text),
            vector=vector,
            model=provider.model,
            resource_type="standard",
            content_text=text,
        )
        for text, vector in zip(texts, vectors, strict=True)
    ]
    await ctx.store.create_embeddings(None, rows)


def _standard_text(standard: Mapping[str, Any]) -> str:
    """The deterministic string embedded for a coded standard (RAG indexing)."""
    return f"{standard.get('rule_key', '')} | {standard.get('description', '')}"


async def _candidate_memories(
    ctx: RunContext,
    summary: ConsolidationSummary,
    snapshot: Mapping[str, Any] | None,
    provider: EmbeddingsProvider,
    *,
    repository_id: str | None,
) -> dict[str, CandidateMemory]:
    """RAG relevance + cached shares for every candidate (ARUM.md §2).

    Each candidate's representative is embedded once and retrieved twice:
    repository relevance against the repo's coded standards, context relevance
    against previously *resolved* findings in the same file path (re-raising an
    already-closed issue is penalised unless new evidence shows up). Both float
    in ``[0, 1]`` so ``extract_features`` can consume them directly.
    """
    representatives: dict[str, FindingWrite] = {}
    for group in summary.groups:
        members = [row for row in summary.findings if row.duplicate_group == group.id]
        if not members:
            raise RuntimeError(f"redundancy group {group.id} has no persisted members")
        representative = next(
            (row for row in members if row.id == group.representative_finding_id),
            members[0],
        )
        representatives[representative.id] = representative
    for row in summary.findings:
        if row.duplicate_group is None:
            representatives[row.id] = row
    if not representatives:
        return {}

    texts = {
        finding_id: embedding_text(finding)
        for finding_id, finding in representatives.items()
    }
    vectors = await provider.embed_texts([texts[finding_id] for finding_id in texts])
    k = ctx.settings.arum_rag_top_k
    min_similarity = ctx.settings.arum_rag_min_similarity
    memories: dict[str, CandidateMemory] = {}
    for finding_id, vector in zip(texts, vectors, strict=True):
        repo_hits = await ctx.store.retrieve_rag(
            repository_id=repository_id,
            query_vector=vector,
            k=k,
            similarity_threshold=min_similarity,
            resource_types=("standard",),
        )
        context_hits = await ctx.store.retrieve_rag(
            repository_id=repository_id,
            query_vector=vector,
            k=k,
            similarity_threshold=min_similarity,
            resource_types=("finding",),
            file_path=representatives[finding_id].file_path,
            statuses=("RESOLVED",),
        )
        memories[finding_id] = CandidateMemory(
            snapshot=snapshot,
            repository_relevance=max_relevance(repo_hits),
            context_relevance=max_relevance(context_hits),
        )
    return memories


@dataclass(frozen=True)
class CandidateMemory:
    """Memory + RAG inputs gathered for one candidate (ARUM.md §2)."""

    snapshot: Mapping[str, Any] | None
    repository_relevance: float
    context_relevance: float

    def feature_memory(self) -> dict[str, object]:
        """The mapping ``features.extract_features`` reads per candidate."""
        return {
            "categories": (self.snapshot or {}).get("categories") or {},
            "repository_relevance": self.repository_relevance,
            "context_relevance": self.context_relevance,
        }


def _score_candidates(
    review_id: str,
    summary: ConsolidationSummary,
    weights: ArumWeights,
    memories: Mapping[str, CandidateMemory] | None = None,
) -> list[Decision]:
    """Build one :class:`Decision` per candidate from the merged summary.

    Singletons are scored alone (member_count 1); grouped findings use the
    redundancy group's member set for agent agreement + disagreement variance.
    ``memories`` (Phase 10) feeds the four memory/RAG features; verified
    candidates without an entry keep the documented zeros.
    """
    memories = memories or {}
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
        candidate = memories.get(representative.id)
        features = extract_features(
            representative,
            member_count=group.member_count,
            distinct_agents=distinct_agents,
            variance=variance,
            memory=None if candidate is None else candidate.feature_memory(),
        )
        decisions.append(
            _decision(review_id, representative, group.id, features, weights)
        )
    for row in summary.findings:
        if row.duplicate_group is None:
            candidate = memories.get(row.id)
            features = extract_features(
                row,
                member_count=1,
                distinct_agents=1,
                variance=0.0,
                memory=None if candidate is None else candidate.feature_memory(),
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
