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
from datetime import UTC, datetime
from typing import Any, Protocol

from app.config.settings import get_settings
from app.database import SessionFactory
from app.models import Agent, AgentMetric, Review, ReviewTask
from app.models.enums import ReviewStatus, TaskStatus


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


class MemoryOrchestratorStore:
    """In-process, deterministic store for unit tests and local debugging."""

    def __init__(self) -> None:
        self.reviews: dict[str, dict[str, Any]] = {}
        self.agents: dict[str, str] = {}
        self.tasks: list[dict[str, Any]] = []
        self.metrics: list[dict[str, Any]] = []

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
        from sqlalchemy import select

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
