"""Persistence boundary for orchestration outcomes (reviews, tasks, metrics).

The orchestrator never talks to SQLAlchemy directly: it relies on
:class:`OrchestratorStore`, which keeps broker/DB-free cores unit-testable
(``MemoryOrchestratorStore``) while ``SqlOrchestratorStore`` is the production
PostgreSQL implementation used by the Celery path. Live-DB behaviour is covered
by the self-skipping integration suite.
"""

from __future__ import annotations

import uuid
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from typing import Any, Protocol

from sqlalchemy import func, select
from sqlalchemy import text as sa_text
from sqlalchemy import update as sa_update

from app.adaptive.feedback import FeedbackWrite, feedback_identity
from app.adaptive.memory import FeedbackEvent
from app.adaptive.rag import EmbeddingRow, RagHit, top_k_similar
from app.adaptive.scoring import Decision
from app.config.settings import get_settings
from app.consolidation.datatypes import EmbeddingWrite, FindingGroupWrite, FindingWrite
from app.consolidation.similarity import vector_sql_literal
from app.database import SessionFactory
from app.models import (
    Agent,
    AgentMetric,
    CodingStandard,
    DeveloperFeedback,
    Embedding,
    Finding,
    FindingGroup,
    RepositoryMemory,
    Review,
    ReviewIteration,
    ReviewTask,
)
from app.models.enums import (
    FeedbackOutcome,
    FindingStatus,
    ReviewStatus,
    RiskClass,
    Severity,
    TaskStatus,
)
from app.orchestrator.iteration import DeltaAction, Resolution


@dataclass(frozen=True)
class TaskWrite:
    """A ``review_tasks`` row the orchestrator wants recorded."""

    agent_key: str
    agent_id: str
    queue_name: str
    status: TaskStatus
    retry_count: int
    last_error: str | None
    scope: dict[str, Any]
    celery_task_id: str | None = None
    started_at: datetime | None = None
    completed_at: datetime | None = None


def utc_now() -> datetime:
    return datetime.now(UTC)


def _embedding_row(
    row: Mapping[str, Any], findings: Sequence[Mapping[str, Any]]
) -> EmbeddingRow:
    """Project an ``embeddings`` record into an :class:`EmbeddingRow`.

    Finding-backed rows peek at their finding to expose ``file_path`` / status so
    ``top_k_similar`` can restrict context relevance to the same path. Standard /
    decision rows carry their text for traceability when it was stored.
    """
    file_path: str | None = None
    status: str | None = None
    finding_id = row.get("finding_id")
    if finding_id is not None:
        for finding in findings:
            if str(finding.get("id")) == str(finding_id):
                file_path = finding.get("file_path")
                status = finding.get("publication_status")
                break
    return EmbeddingRow(
        id=str(row.get("id", "")),
        repository_id=str(row.get("repository_id", "")),
        resource_type=str(row.get("resource_type", "finding")),
        content_hash=str(row.get("content_hash", "")),
        vector=tuple(row.get("vector") or ()),
        model=str(row.get("model", "")),
        finding_id=None if finding_id is None else str(finding_id),
        file_path=file_path,
        status=status,
        content_text=row.get("content_text"),
    )


class OrchestratorStore(Protocol):
    """Boundary over review/task/metric persistence used by the orchestrator."""

    async def transition(
        self,
        review_id: object,
        status: ReviewStatus,
        *,
        failure_reason: str | None = None,
        root_cause: str | None = None,
        completed_at: datetime | None = None,
        at: datetime | None = None,
    ) -> None: ...

    async def append_note(self, review_id: object, note: str) -> None: ...

    async def sync_agents(
        self, rows: Sequence[Mapping[str, str]]
    ) -> dict[str, str]: ...

    async def create_review_task(self, review_id: object, task: TaskWrite) -> None: ...

    async def create_agent_metric(
        self,
        review_id: object,
        *,
        agent_key: str,
        agent_id: str,
        stats: Mapping[str, Any],
    ) -> None: ...

    async def create_findings(
        self, review_id: object, rows: Sequence[FindingWrite]
    ) -> None: ...

    async def create_finding_group(
        self, review_id: object, group: FindingGroupWrite
    ) -> None: ...

    async def create_embeddings(
        self, review_id: object | None, rows: Sequence[EmbeddingWrite]
    ) -> None: ...

    async def update_finding_duplicates(
        self,
        review_id: object,
        updates: Sequence[tuple[str, str]],
    ) -> None: ...

    async def review_risk_class(self, review_id: object) -> RiskClass | None: ...

    async def record_decision(
        self,
        review_id: object,
        *,
        arum_version: str,
        budget_cap: int,
        selected_count: int,
        suppressed_count: int,
    ) -> None: ...

    async def apply_decision(self, review_id: object, decision: Decision) -> None: ...

    # --- Phase 10: repository memory + RAG -----------------------------------
    async def get_repository_memory(
        self, repository_id: object
    ) -> dict[str, Any] | None: ...

    async def upsert_repository_memory(
        self,
        repository_id: object,
        snapshot: dict[str, Any],
        decay_params: dict[str, Any],
    ) -> int: ...

    async def repository_feedback_events(
        self,
        repository_id: object,
        *,
        max_age_days: float,
        now: datetime | None = None,
    ) -> Sequence[FeedbackEvent]: ...

    async def retrieve_rag(
        self,
        repository_id: object,
        query_vector: Sequence[float],
        *,
        k: int,
        similarity_threshold: float = 0.0,
        resource_types: Sequence[str] | None = None,
        file_path: str | None = None,
        statuses: Sequence[str] | None = None,
    ) -> Sequence[RagHit]: ...

    async def active_standards(self, repository_id: object) -> list[dict[str, Any]]: ...

    async def embedding_hashes(
        self,
        repository_id: object,
        *,
        resource_types: Sequence[str],
    ) -> set[str]: ...

    # --- Phase 11: developer feedback + weight learning ----------------------
    async def create_feedback(self, feedback: FeedbackWrite) -> str: ...

    async def resolve_feedback_finding(
        self, review_id: object, finding_id: object
    ) -> tuple[str, str] | None: ...

    async def save_weight_candidate(
        self, repository_id: object, candidate: dict[str, Any]
    ) -> None: ...

    async def repository_learned_weights(
        self, repository_id: object
    ) -> dict[str, Any] | None: ...

    # --- Phase 12: iterative review engine -----------------------------------
    async def apply_iteration_delta(
        self, review_id: object, resolutions: Sequence[Resolution]
    ) -> None: ...

    async def record_iteration(
        self,
        review_id: object,
        *,
        base_sha: str,
        head_sha: str,
        diff_stats: Mapping[str, Any],
        agents_invoked: Mapping[str, Any],
        published_count: int,
        resolved_count: int,
        stale_count: int,
    ) -> None: ...


class MemoryOrchestratorStore:
    """In-process, deterministic store for unit tests and local debugging."""

    def __init__(self) -> None:
        self.reviews: dict[str, dict[str, Any]] = {}
        self.agents: dict[str, str] = {}
        self.tasks: list[dict[str, Any]] = []
        self.metrics: list[dict[str, Any]] = []
        self.findings: list[dict[str, Any]] = []
        self.finding_groups: list[dict[str, Any]] = []
        self.embeddings: list[dict[str, Any]] = []
        self.memory: dict[str, dict[str, Any]] = {}
        self.feedback: list[dict[str, Any]] = []
        self.standards: dict[str, list[dict[str, Any]]] = {}
        self.iterations: list[dict[str, Any]] = []

    async def transition(
        self,
        review_id: object,
        status: ReviewStatus,
        *,
        failure_reason: str | None = None,
        root_cause: str | None = None,
        completed_at: datetime | None = None,
        at: datetime | None = None,
    ) -> None:
        key = str(review_id)
        record = self.reviews.setdefault(key, {})
        prev = record.get("status")
        record["status"] = status.value
        if failure_reason is not None:
            record["failure_reason"] = failure_reason
        if root_cause is not None:
            record["root_cause"] = root_cause
        if completed_at is not None:
            record["completed_at"] = completed_at.isoformat()
        history = record.setdefault("status_history", [])
        history.append(
            {
                "status": status.value,
                "from": prev,
                "at": (at or utc_now()).isoformat(),
            }
        )

    async def append_note(self, review_id: object, note: str) -> None:
        record = self.reviews.setdefault(str(review_id), {})
        record.setdefault("supervisor_notes", []).append(note)

    async def sync_agents(self, rows: Sequence[Mapping[str, str]]) -> dict[str, str]:
        for row in rows:
            key = str(row["key"])
            self.agents.setdefault(key, str(uuid.uuid4()))
        return dict(self.agents)

    async def create_review_task(self, review_id: object, task: TaskWrite) -> None:
        self.tasks.append(
            {
                "review_id": str(review_id),
                "agent_key": task.agent_key,
                "agent_id": task.agent_id,
                "queue_name": task.queue_name,
                "status": task.status.value,
                "retry_count": task.retry_count,
                "last_error": task.last_error,
                "scope": task.scope,
                "celery_task_id": task.celery_task_id,
            }
        )

    async def create_agent_metric(
        self,
        review_id: object,
        *,
        agent_key: str,
        agent_id: str,
        stats: Mapping[str, Any],
    ) -> None:
        self.metrics.append(
            {
                "review_id": str(review_id),
                "agent_key": agent_key,
                "agent_id": agent_id,
                "stats": dict(stats),
            }
        )

    async def create_findings(
        self, review_id: object, rows: Sequence[FindingWrite]
    ) -> None:
        for row in rows:
            self.findings.append(
                {
                    "id": row.id,
                    "review_id": str(review_id),
                    "repository_id": row.repository_id,
                    "agent_key": row.agent_key,
                    "agent_id": row.agent_id,
                    "file_path": row.file_path,
                    "line_start": row.line_start,
                    "line_end": row.line_end,
                    "category": row.category,
                    "severity": row.severity,
                    "confidence": row.confidence,
                    "title": row.title,
                    "description": row.description,
                    "evidence": dict(row.evidence),
                    "suggested_fix": row.suggested_fix,
                    "reason_summary": row.reason_summary,
                    "duplicate_group": None,
                    "publication_status": FindingStatus.CANDIDATE.value,
                }
            )

    async def create_finding_group(
        self, review_id: object, group: FindingGroupWrite
    ) -> None:
        self.finding_groups.append(
            {
                "id": group.id,
                "review_id": str(review_id),
                "representative_finding_id": group.representative_finding_id,
                "member_count": group.member_count,
                "redundancy_method": group.redundancy_method,
                "max_pairwise_similarity": group.max_pairwise_similarity,
            }
        )

    async def create_embeddings(
        self, review_id: object | None, rows: Sequence[EmbeddingWrite]
    ) -> None:
        for row in rows:
            self.embeddings.append(
                {
                    "id": str(uuid.uuid4()),
                    "review_id": None if review_id is None else str(review_id),
                    "repository_id": row.repository_id,
                    "finding_id": row.finding_id,
                    "resource_type": row.resource_type,
                    "content_hash": row.content_hash,
                    "vector": list(row.vector),
                    "model": row.model,
                    "content_text": row.content_text,
                }
            )

    async def update_finding_duplicates(
        self,
        review_id: object,
        updates: Sequence[tuple[str, str]],
    ) -> None:
        for finding_id, group_id in updates:
            for row in self.findings:
                if row["id"] == finding_id and row["review_id"] == str(review_id):
                    row["duplicate_group"] = group_id
                    break

    async def review_risk_class(self, review_id: object) -> RiskClass | None:
        record = self.reviews.get(str(review_id), {})
        value = record.get("risk_class")
        if value is None:
            return None
        return value if isinstance(value, RiskClass) else RiskClass(value)

    async def record_decision(
        self,
        review_id: object,
        *,
        arum_version: str,
        budget_cap: int,
        selected_count: int,
        suppressed_count: int,
    ) -> None:
        review = self.reviews.get(str(review_id))
        if review is not None:
            review["arum_version"] = arum_version
            review["budget_cap"] = budget_cap
            review["selected_count"] = selected_count
            review["suppressed_count"] = suppressed_count

    async def apply_decision(self, review_id: object, decision: Decision) -> None:
        target = next(
            (row for row in self.findings if row["id"] == decision.finding_id),
            None,
        )
        if target is None:
            return
        target["arum_features"] = decision.features.as_dict()
        target["arum_utility"] = decision.utility
        target["arum_version"] = decision.arum_version
        if decision.selected is not None:
            target["publication_status"] = (
                FindingStatus.SCHEDULED.value
                if decision.selected
                else FindingStatus.SUPPRESSED.value
            )
        if decision.selected is not None and decision.group_id:
            for row in self.findings:
                if (
                    row["duplicate_group"] == decision.group_id
                    and row["id"] != decision.finding_id
                ):
                    row["publication_status"] = FindingStatus.SUPPRESSED.value

    async def get_repository_memory(
        self, repository_id: object
    ) -> dict[str, Any] | None:
        record = self.memory.get(str(repository_id))
        return dict(record["snapshot"]) if record else None

    async def upsert_repository_memory(
        self,
        repository_id: object,
        snapshot: dict[str, Any],
        decay_params: dict[str, Any],
    ) -> int:
        key = str(repository_id)
        record = self.memory.setdefault(
            key,
            {
                "snapshot": dict(snapshot),
                "decay_params": dict(decay_params),
                "version": 0,
            },
        )
        record["snapshot"] = dict(snapshot)
        record["decay_params"] = dict(decay_params)
        record["version"] = int(record.get("version", 0)) + 1
        return int(record["version"])

    async def repository_feedback_events(
        self,
        repository_id: object,
        *,
        max_age_days: float,
        now: datetime | None = None,
    ) -> list[FeedbackEvent]:
        now_ts = now or utc_now()
        events: list[FeedbackEvent] = []
        for row in self.feedback:
            if str(row.get("repository_id")) != str(repository_id):
                continue
            created = row.get("created_at")
            if created is None:
                continue
            created_ts = (
                created
                if isinstance(created, datetime)
                else datetime.fromisoformat(created)
            )
            age_days = (now_ts - created_ts).total_seconds() / 86400.0
            if age_days < 0.0 or age_days >= max_age_days:
                continue
            events.append(
                FeedbackEvent(
                    category=str(row.get("category", "")),
                    outcome=FeedbackOutcome(row["outcome"]),
                    age_days=age_days,
                    finding_id=(
                        str(row["finding_id"]) if row.get("finding_id") else None
                    ),
                )
            )
        return events

    async def retrieve_rag(
        self,
        repository_id: object,
        query_vector: Sequence[float],
        *,
        k: int,
        similarity_threshold: float = 0.0,
        resource_types: Sequence[str] | None = None,
        file_path: str | None = None,
        statuses: Sequence[str] | None = None,
    ) -> list[RagHit]:
        rows = [
            _embedding_row(row, self.findings)
            for row in self.embeddings
            if str(row.get("repository_id")) == str(repository_id)
        ]
        return top_k_similar(
            query_vector,
            rows,
            k=k,
            similarity_threshold=similarity_threshold,
            resource_types=resource_types,
            file_path=file_path,
            statuses=statuses,
        )

    async def active_standards(self, repository_id: object) -> list[dict[str, Any]]:
        return [
            dict(standard) for standard in self.standards.get(str(repository_id), [])
        ]

    async def embedding_hashes(
        self,
        repository_id: object,
        *,
        resource_types: Sequence[str],
    ) -> set[str]:
        kinds = set(resource_types)
        return {
            str(row["content_hash"])
            for row in self.embeddings
            if str(row["repository_id"]) == str(repository_id)
            and row["resource_type"] in kinds
        }

    async def create_feedback(self, feedback: FeedbackWrite) -> str:
        feedback_id = feedback_identity(feedback)
        existing = next(
            (row for row in self.feedback if row.get("id") == feedback_id), None
        )
        if existing is None:
            self.feedback.append(feedback.to_row(feedback_id, utc_now().isoformat()))
        return feedback_id

    async def resolve_feedback_finding(
        self, review_id: object, finding_id: object
    ) -> tuple[str, str] | None:
        rid = str(review_id)
        fid = str(finding_id)
        for row in self.findings:
            if row["id"] == fid and str(row.get("review_id")) == rid:
                return (
                    str(row["repository_id"]),
                    str(row.get("category", "")),
                )
        return None

    async def save_weight_candidate(
        self, repository_id: object, candidate: dict[str, Any]
    ) -> None:
        key = str(repository_id)
        record = self.memory.setdefault(
            key, {"snapshot": {}, "decay_params": {}, "version": 0}
        )
        record["learned_weights"] = dict(candidate)

    async def repository_learned_weights(
        self, repository_id: object
    ) -> dict[str, Any] | None:
        record = self.memory.get(str(repository_id))
        if not record or "learned_weights" not in record:
            return None
        learned = record["learned_weights"]
        return dict(learned) if isinstance(learned, dict) else None

    async def apply_iteration_delta(
        self, review_id: object, resolutions: Sequence[Resolution]
    ) -> None:
        rid = str(review_id)
        by_id = {row["id"]: row for row in self.findings}
        for resolution in resolutions:
            if resolution.action is DeltaAction.KEEP:
                continue
            row = by_id.get(resolution.finding_id)
            if row is None:
                continue
            row["publication_status"] = resolution.action.value
        stale = sum(1 for r in resolutions if r.action is DeltaAction.STALE)
        resolved = sum(1 for r in resolutions if r.action is DeltaAction.RESOLVED)
        if resolutions:
            await self.append_note(
                rid,
                f"supervisor: iteration delta {resolved} resolved, "
                f"{stale} stale, "
                f"{len(resolutions) - resolved - stale} kept",
            )

    async def record_iteration(
        self,
        review_id: object,
        *,
        base_sha: str,
        head_sha: str,
        diff_stats: Mapping[str, Any],
        agents_invoked: Mapping[str, Any],
        published_count: int,
        resolved_count: int,
        stale_count: int,
    ) -> None:
        rid = str(review_id)
        current = max(
            (
                int(row["iteration"])
                for row in self.iterations
                if row["review_id"] == rid
            ),
            default=0,
        )
        self.iterations.append(
            {
                "review_id": rid,
                "iteration": current + 1,
                "base_sha": base_sha,
                "head_sha": head_sha,
                "diff_stats": dict(diff_stats),
                "agents_invoked": dict(agents_invoked),
                "published_count": published_count,
                "resolved_count": resolved_count,
                "stale_count": stale_count,
            }
        )


class SqlOrchestratorStore:
    """PostgreSQL implementation via SQLAlchemy (production path)."""

    def __init__(self) -> None:
        self._settings = get_settings()
        self._session_factory = SessionFactory

    async def transition(
        self,
        review_id: object,
        status: ReviewStatus,
        *,
        failure_reason: str | None = None,
        root_cause: str | None = None,
        completed_at: datetime | None = None,
        at: datetime | None = None,
    ) -> None:
        rid = uuid.UUID(str(review_id))
        now = at or utc_now()
        async with self._session_factory() as session, session.begin():
            review = await session.get(Review, rid)
            if review is None:
                return
            prev = review.status
            review.status = status
            if failure_reason is not None:
                review.failure_reason = failure_reason
            if root_cause is not None:
                review.root_cause = root_cause
            if completed_at is not None:
                review.completed_at = completed_at
            history = list(review.status_history or [])
            history.append(
                {
                    "status": status.value,
                    "from": prev.value if prev else None,
                    "at": now.isoformat(),
                }
            )
            review.status_history = history

    async def append_note(self, review_id: object, note: str) -> None:
        rid = uuid.UUID(str(review_id))
        async with self._session_factory() as session, session.begin():
            review = await session.get(Review, rid)
            if review is None:
                return
            notes = list(review.supervisor_notes or [])
            notes.append(note)
            review.supervisor_notes = notes

    async def sync_agents(self, rows: Sequence[Mapping[str, str]]) -> dict[str, str]:
        ids: dict[str, str] = {}
        async with self._session_factory() as session, session.begin():
            for row in rows:
                key = str(row["key"])
                existing = await session.scalar(select(Agent).where(Agent.key == key))
                if existing is not None:
                    ids[key] = str(existing.id)
                    continue
                agent = Agent(
                    key=key,
                    name=str(row.get("name", key)),
                    version=str(row.get("version", "unknown")),
                    is_active=True,
                )
                session.add(agent)
                await session.flush()
                ids[key] = str(agent.id)
        return ids

    async def create_review_task(self, review_id: object, task: TaskWrite) -> None:
        rid = uuid.UUID(str(review_id))
        async with self._session_factory() as session, session.begin():
            session.add(
                ReviewTask(
                    review_id=rid,
                    agent_id=uuid.UUID(task.agent_id),
                    queue_name=task.queue_name,
                    status=task.status,
                    scope=dict(task.scope),
                    retry_count=task.retry_count,
                    last_error=task.last_error,
                    celery_task_id=task.celery_task_id,
                    started_at=task.started_at,
                    completed_at=task.completed_at,
                )
            )

    async def create_agent_metric(
        self,
        review_id: object,
        *,
        agent_key: str,
        agent_id: str,
        stats: Mapping[str, Any],
    ) -> None:
        rid = uuid.UUID(str(review_id))
        async with self._session_factory() as session, session.begin():
            session.add(
                AgentMetric(
                    review_id=rid,
                    agent_id=uuid.UUID(agent_id),
                    duration_ms=self._int(stats.get("duration_ms")),
                    tokens_in=self._int(stats.get("tokens_in")),
                    tokens_out=self._int(stats.get("tokens_out")),
                    cost_usd=self._float(stats.get("cost_usd")),
                    findings_raw=self._int(stats.get("findings_raw")),
                    findings_valid=self._int(stats.get("findings_valid")),
                    failed_results=self._int(stats.get("failed_results")),
                    error=stats.get("error"),
                )
            )

    async def create_findings(
        self, review_id: object, rows: Sequence[FindingWrite]
    ) -> None:
        rid = uuid.UUID(str(review_id))
        async with self._session_factory() as session, session.begin():
            for row in rows:
                session.add(
                    Finding(
                        id=uuid.UUID(row.id),
                        review_id=rid,
                        repository_id=uuid.UUID(row.repository_id),
                        agent_id=uuid.UUID(row.agent_id),
                        file_path=row.file_path,
                        line_start=row.line_start,
                        line_end=row.line_end,
                        category=row.category,
                        severity=Severity(row.severity),
                        confidence=row.confidence,
                        title=row.title,
                        description=row.description,
                        evidence=dict(row.evidence),
                        suggested_fix=row.suggested_fix,
                        agent_reasoning_summary=row.reason_summary,
                        publication_status=FindingStatus.CANDIDATE,
                        feedback_labelled=False,
                        temporal_weight=1.0,
                    )
                )

    async def create_finding_group(
        self, review_id: object, group: FindingGroupWrite
    ) -> None:
        rid = uuid.UUID(str(review_id))
        async with self._session_factory() as session, session.begin():
            session.add(
                FindingGroup(
                    id=uuid.UUID(group.id),
                    review_id=rid,
                    representative_finding_id=uuid.UUID(
                        group.representative_finding_id
                    ),
                    member_count=group.member_count,
                    redundancy_method=group.redundancy_method,
                    max_pairwise_similarity=group.max_pairwise_similarity,
                )
            )

    async def create_embeddings(
        self, review_id: object | None, rows: Sequence[EmbeddingWrite]
    ) -> None:
        # The vector is written as a pgvector text literal cast server-side. This
        # deliberately avoids the pgvector asyncpg codec (registered in Phase 14,
        # see ``app.database``); the floats are provider-produced, so interpolating
        # them is safe and keeps the SQL store fully unit-testable offline. The
        # (resource_type, content_hash) unique constraint dedupes Phase 10 RAG
        # standard embeddings on re-sync.
        async with self._session_factory() as session, session.begin():
            for row in rows:
                vector_cast = f"'{vector_sql_literal(row.vector)}'::vector"
                statement = sa_text(
                    "INSERT INTO embeddings "
                    "(id, repository_id, finding_id, resource_type, content_hash, "
                    "vector, model) VALUES (:id, :repository_id, :finding_id, "
                    ":resource_type, :content_hash, " + vector_cast + ", :model)"
                    " ON CONFLICT (resource_type, content_hash) DO NOTHING"
                )
                await session.execute(
                    statement,
                    {
                        "id": uuid.uuid4(),
                        "repository_id": uuid.UUID(row.repository_id),
                        "finding_id": (
                            None
                            if row.finding_id is None
                            else uuid.UUID(row.finding_id)
                        ),
                        "resource_type": row.resource_type,
                        "content_hash": row.content_hash,
                        "model": row.model,
                    },
                )

    async def update_finding_duplicates(
        self,
        review_id: object,
        updates: Sequence[tuple[str, str]],
    ) -> None:
        # The findings <=> finding_groups foreign keys are bi-directional, so the
        # group id is written back here *after* the group rows exist.
        rid = uuid.UUID(str(review_id))
        async with self._session_factory() as session, session.begin():
            for finding_id, group_id in updates:
                await session.execute(
                    sa_text(
                        "UPDATE findings SET duplicate_group = :group_id "
                        "WHERE id = :finding_id AND review_id = :review_id"
                    ),
                    {
                        "group_id": uuid.UUID(group_id),
                        "finding_id": uuid.UUID(finding_id),
                        "review_id": rid,
                    },
                )

    async def review_risk_class(self, review_id: object) -> RiskClass | None:
        rid = uuid.UUID(str(review_id))
        async with self._session_factory() as session:
            review = await session.get(Review, rid)
            return review.risk_class if review is not None else None

    async def record_decision(
        self,
        review_id: object,
        *,
        arum_version: str,
        budget_cap: int,
        selected_count: int,
        suppressed_count: int,
    ) -> None:
        rid = uuid.UUID(str(review_id))
        async with self._session_factory() as session, session.begin():
            review = await session.get(Review, rid)
            if review is not None:
                review.arum_version = arum_version
                review.budget_cap = budget_cap

    async def apply_decision(self, review_id: object, decision: Decision) -> None:
        rid = uuid.UUID(str(review_id))
        finding_id = uuid.UUID(decision.finding_id)
        async with self._session_factory() as session, session.begin():
            finding = await session.get(Finding, finding_id)
            if finding is None or finding.review_id != rid:
                return
            finding.arum_features = decision.features.as_dict()
            finding.arum_utility = decision.utility
            finding.arum_version = decision.arum_version
            if decision.selected is not None:
                finding.publication_status = (
                    FindingStatus.SCHEDULED
                    if decision.selected
                    else FindingStatus.SUPPRESSED
                )
            if decision.selected is not None and decision.group_id:
                await session.execute(
                    sa_update(Finding)
                    .where(
                        Finding.duplicate_group == uuid.UUID(decision.group_id),
                        Finding.id != finding_id,
                    )
                    .values(publication_status=FindingStatus.SUPPRESSED)
                )

    async def get_repository_memory(
        self, repository_id: object
    ) -> dict[str, Any] | None:
        rid = uuid.UUID(str(repository_id))
        async with self._session_factory() as session:
            row = await session.scalar(
                select(RepositoryMemory).where(RepositoryMemory.repository_id == rid)
            )
            return dict(row.snapshot) if row is not None else None

    async def upsert_repository_memory(
        self,
        repository_id: object,
        snapshot: dict[str, Any],
        decay_params: dict[str, Any],
    ) -> int:
        rid = uuid.UUID(str(repository_id))
        async with self._session_factory() as session, session.begin():
            row = await session.scalar(
                select(RepositoryMemory).where(RepositoryMemory.repository_id == rid)
            )
            if row is None:
                row = RepositoryMemory(
                    repository_id=rid,
                    snapshot=dict(snapshot),
                    decay_params=dict(decay_params),
                    version=1,
                )
                session.add(row)
            else:
                row.snapshot = dict(snapshot)
                row.decay_params = dict(decay_params)
                row.version = int(row.version or 0) + 1
            await session.flush()
            return int(row.version)

    async def repository_feedback_events(
        self,
        repository_id: object,
        *,
        max_age_days: float,
        now: datetime | None = None,
    ) -> list[FeedbackEvent]:
        rid = uuid.UUID(str(repository_id))
        now_ts = now or utc_now()
        threshold = now_ts - timedelta(days=max_age_days)
        events: list[FeedbackEvent] = []
        async with self._session_factory() as session:
            rows = await session.execute(
                select(DeveloperFeedback, Finding.category)
                .join(Finding, DeveloperFeedback.finding_id == Finding.id)
                .where(
                    DeveloperFeedback.repository_id == rid,
                    DeveloperFeedback.created_at >= threshold,
                )
            )
            for feedback, category in rows:
                age_days = (now_ts - feedback.created_at).total_seconds() / 86400.0
                events.append(
                    FeedbackEvent(
                        category=category or "",
                        outcome=feedback.outcome,
                        age_days=age_days,
                        finding_id=(
                            str(feedback.finding_id)
                            if feedback.finding_id is not None
                            else None
                        ),
                    )
                )
        return events

    async def retrieve_rag(
        self,
        repository_id: object,
        query_vector: Sequence[float],
        *,
        k: int,
        similarity_threshold: float = 0.0,
        resource_types: Sequence[str] | None = None,
        file_path: str | None = None,
        statuses: Sequence[str] | None = None,
    ) -> list[RagHit]:
        rid = uuid.UUID(str(repository_id))
        kinds = resource_types or ("finding", "standard", "decision", "comment")
        kind_sql = ", ".join(f"'{kind.replace(chr(39), '')}'" for kind in kinds)
        query_literal = vector_sql_literal(query_vector)
        cosine_expr = f"1.0 - (e.vector <=> '{query_literal}'::vector)"
        status_filter = ""
        if statuses:
            status_sql = ", ".join(
                f"'{status.replace(chr(39), '')}'" for status in statuses
            )
            status_filter = f"AND f.publication_status IN ({status_sql}) "
        file_filter = "AND f.file_path = :file_path " if file_path else ""
        statement = sa_text(
            f"SELECT e.finding_id, e.resource_type, {cosine_expr} AS cosine "
            "FROM embeddings e "
            "LEFT JOIN findings f ON f.id = e.finding_id "
            "WHERE e.repository_id = :repository_id "
            f"AND e.resource_type IN ({kind_sql}) "
            f"{file_filter}{status_filter} "
            f"AND {cosine_expr} >= :min_similarity "
            "ORDER BY cosine DESC "
            "LIMIT :k"
        )
        params: dict[str, Any] = {
            "repository_id": rid,
            "min_similarity": similarity_threshold,
            "k": k,
        }
        if file_path:
            params["file_path"] = file_path
        hits: list[RagHit] = []
        async with self._session_factory() as session:
            rows = await session.execute(statement, params)
            for finding_id, resource_type, cosine in rows:
                hits.append(
                    RagHit(
                        repository_id=str(repository_id),
                        resource_type=resource_type,
                        cosine=cosine,
                        finding_id=None if finding_id is None else str(finding_id),
                    )
                )
        return hits

    async def active_standards(self, repository_id: object) -> list[dict[str, Any]]:
        rid = uuid.UUID(str(repository_id))
        async with self._session_factory() as session:
            standards = await session.scalars(
                select(CodingStandard).where(
                    CodingStandard.repository_id == rid,
                    CodingStandard.is_active.is_(True),
                )
            )
            return [
                {
                    "rule_key": standard.rule_key,
                    "description": standard.description,
                    "enforcement_level": standard.enforcement_level,
                }
                for standard in standards
            ]

    async def embedding_hashes(
        self,
        repository_id: object,
        *,
        resource_types: Sequence[str],
    ) -> set[str]:
        rid = uuid.UUID(str(repository_id))
        async with self._session_factory() as session:
            hashes = await session.scalars(
                select(Embedding.content_hash).where(
                    Embedding.repository_id == rid,
                    Embedding.resource_type.in_(set(resource_types)),
                )
            )
            return set(hashes)

    async def create_feedback(self, feedback: FeedbackWrite) -> str:
        feedback_id = feedback_identity(feedback)
        async with self._session_factory() as session, session.begin():
            existing = await session.get(DeveloperFeedback, uuid.UUID(feedback_id))
            if existing is None:
                session.add(
                    DeveloperFeedback(
                        id=uuid.UUID(feedback_id),
                        finding_id=uuid.UUID(feedback.finding_id),
                        review_id=uuid.UUID(feedback.review_id),
                        repository_id=uuid.UUID(feedback.repository_id),
                        outcome=feedback.outcome,
                        source=feedback.source,
                        author_login=feedback.author_login,
                        commit_sha=feedback.commit_sha,
                        details=feedback.details,
                    )
                )
        return feedback_id

    async def resolve_feedback_finding(
        self, review_id: object, finding_id: object
    ) -> tuple[str, str] | None:
        async with self._session_factory() as session:
            finding = await session.scalar(
                select(Finding).where(
                    Finding.id == uuid.UUID(str(finding_id)),
                    Finding.review_id == uuid.UUID(str(review_id)),
                )
            )
            if finding is None:
                return None
            return (str(finding.repository_id), finding.category or "")

    async def save_weight_candidate(
        self, repository_id: object, candidate: dict[str, Any]
    ) -> None:
        rid = uuid.UUID(str(repository_id))
        async with self._session_factory() as session, session.begin():
            row = await session.scalar(
                select(RepositoryMemory).where(RepositoryMemory.repository_id == rid)
            )
            if row is None:
                row = RepositoryMemory(repository_id=rid, snapshot={}, decay_params={})
                session.add(row)
            row.learned_weights = dict(candidate)

    async def repository_learned_weights(
        self, repository_id: object
    ) -> dict[str, Any] | None:
        rid = uuid.UUID(str(repository_id))
        async with self._session_factory() as session:
            row = await session.scalar(
                select(RepositoryMemory).where(RepositoryMemory.repository_id == rid)
            )
            if row is None or row.learned_weights is None:
                return None
            return dict(row.learned_weights)

    async def apply_iteration_delta(
        self, review_id: object, resolutions: Sequence[Resolution]
    ) -> None:
        rid = uuid.UUID(str(review_id))
        stale = 0
        resolved = 0
        async with self._session_factory() as session, session.begin():
            for resolution in resolutions:
                if resolution.action is DeltaAction.KEEP:
                    continue
                if resolution.action is DeltaAction.STALE:
                    stale += 1
                else:
                    resolved += 1
                await session.execute(
                    sa_update(Finding)
                    .where(
                        Finding.id == uuid.UUID(resolution.finding_id),
                        Finding.review_id == rid,
                    )
                    .values(publication_status=resolution.action.value)
                )
        if resolutions:
            await self.append_note(
                rid,
                f"supervisor: iteration delta {resolved} resolved, "
                f"{stale} stale, "
                f"{len(resolutions) - resolved - stale} kept",
            )

    async def record_iteration(
        self,
        review_id: object,
        *,
        base_sha: str,
        head_sha: str,
        diff_stats: Mapping[str, Any],
        agents_invoked: Mapping[str, Any],
        published_count: int,
        resolved_count: int,
        stale_count: int,
    ) -> None:
        rid = uuid.UUID(str(review_id))
        async with self._session_factory() as session, session.begin():
            next_round = (
                await session.scalar(
                    select(func.max(ReviewIteration.iteration)).where(
                        ReviewIteration.review_id == rid
                    )
                )
                or 0
            ) + 1
            session.add(
                ReviewIteration(
                    review_id=rid,
                    iteration=next_round,
                    base_sha=base_sha,
                    head_sha=head_sha,
                    diff_stats=dict(diff_stats),
                    agents_invoked=dict(agents_invoked),
                    published_count=published_count,
                    resolved_count=resolved_count,
                    stale_count=stale_count,
                )
            )

    @staticmethod
    def _int(value: object) -> int:
        if isinstance(value, bool) or not isinstance(value, (int, float, str)):
            return 0
        try:
            return int(value)
        except (TypeError, ValueError):
            return 0

    @staticmethod
    def _float(value: object) -> float | None:
        if isinstance(value, bool) or not isinstance(value, (int, float, str)):
            return None
        try:
            return float(value)
        except (TypeError, ValueError):
            return None
