"""Database integration tests for the OrchestratorStore (skipped without PG).

Follows the pattern in ``test_db_integration.py``: these tests self-skip when
no PostgreSQL is reachable and exercise the SQL store's persistence contract,
including the new ``supervisor_notes``/``status_history`` JSONB columns
(migration 0004_orchestrator_notes).
"""

from __future__ import annotations

import uuid

import pytest
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncEngine

from app.config.settings import get_settings
from app.database import SessionFactory, create_db_engine
from app.models import Base
from app.models.enums import ReviewStatus, TaskStatus
from app.orchestrator.persistence import SqlOrchestratorStore, TaskWrite


async def _require_db() -> None:
    engine = create_db_engine()
    try:
        async with engine.connect() as conn:
            await conn.execute(text("SELECT 1"))
    except Exception as exc:  # pragma: no cover - environment dependent
        pytest.skip(
            f"PostgreSQL not reachable at {get_settings().database_url} "
            f"({type(exc).__name__}); skipping orchestrator integration tests."
        )
    finally:
        await engine.dispose()


async def _reset_schema(engine: AsyncEngine) -> None:
    async with engine.begin() as conn:
        await conn.execute(text("CREATE EXTENSION IF NOT EXISTS vector"))
        await conn.run_sync(Base.metadata.drop_all)
        await conn.run_sync(Base.metadata.create_all)


async def _seed_review(engine: AsyncEngine) -> tuple[uuid.UUID, uuid.UUID]:
    """Create a repository + PR + review; return (review_id, repository_id)."""
    repo_id = uuid.uuid4()
    pr_id = uuid.uuid4()
    review_id = uuid.uuid4()
    async with engine.begin() as conn:
        await conn.execute(
            text(
                "INSERT INTO repositories (id, github_id, full_name, default_branch) "
                "VALUES (:id, :gid, :name, 'main')"
            ),
            {
                "id": repo_id,
                "gid": 8001,
                "name": f"integration/ors-{uuid.uuid4().hex[:8]}",
            },
        )
        await conn.execute(
            text(
                "INSERT INTO pull_requests (id, repository_id, github_pr_id, number, "
                "title, author_login, base_ref, head_ref, head_sha, state, "
                "changed_files, additions, deletions) "
                "VALUES (:id, :rid, 8, 8, 't', 'a', 'main', 'feat', 'sha', "
                "'OPEN', 1, 2, 0)"
            ),
            {"id": pr_id, "rid": repo_id},
        )
        await conn.execute(
            text(
                "INSERT INTO reviews (id, pull_request_id, repository_id, status, "
                "mode, priority_score, risk_class, github_delivery_id) "
                "VALUES (:id, :prid, :rid, 'PROCESSING', 'OPENED', 6.5, 'MEDIUM', :d)"
            ),
            {"id": review_id, "prid": pr_id, "rid": repo_id, "d": str(uuid.uuid4())},
        )
    return review_id, repo_id


async def test_sql_store_persists_transitions_and_notes() -> None:
    await _require_db()
    engine = create_db_engine()
    try:
        await _reset_schema(engine)
        review_id, _ = await _seed_review(engine)
        store = SqlOrchestratorStore()

        await store.transition(review_id, ReviewStatus.AGENTS_RUNNING)
        await store.append_note(review_id, "retrying security")
        await store.append_note(review_id, "diagnosed FAILED")
        await store.transition(
            review_id,
            ReviewStatus.FAILED,
            failure_reason="quality exhausted retries",
            root_cause="quality exhausted retries",
        )

        async with SessionFactory() as session:
            row = (
                await session.execute(
                    text(
                        "SELECT status, failure_reason, root_cause, "
                        "supervisor_notes, status_history FROM reviews WHERE id = :id"
                    ),
                    {"id": review_id},
                )
            ).one()
        assert row.status == "FAILED"
        assert row.failure_reason == "quality exhausted retries"
        assert "quality" in row.root_cause
        assert row.supervisor_notes == ["retrying security", "diagnosed FAILED"]
        statuses = [h["status"] for h in row.status_history]
        assert statuses == ["AGENTS_RUNNING", "FAILED"]
        assert row.status_history[1]["from"] == "AGENTS_RUNNING"
    finally:
        await engine.dispose()


async def test_sql_store_syncs_agents_tasks_and_metrics() -> None:
    await _require_db()
    engine = create_db_engine()
    try:
        await _reset_schema(engine)
        review_id, repo_id = await _seed_review(engine)
        store = SqlOrchestratorStore()

        rows = [
            {"key": "security", "name": "Security Agent", "version": "1.0"},
            {"key": "quality", "name": "Quality Agent", "version": "1.0"},
        ]
        agent_ids = await store.sync_agents(rows)
        assert set(agent_ids) == {"security", "quality"}

        await store.create_review_task(
            review_id,
            TaskWrite(
                agent_key="security",
                agent_id=agent_ids["security"],
                queue_name="security_queue",
                status=TaskStatus.SUCCESS,
                retry_count=1,
                last_error=None,
                scope={"review_id": str(review_id), "changed_files": []},
            ),
        )
        await store.create_agent_metric(
            review_id,
            agent_key="security",
            agent_id=agent_ids["security"],
            stats={
                "duration_ms": 15,
                "tokens_in": 10,
                "tokens_out": 4,
                "cost_usd": 0.01,
            },
        )

        async with SessionFactory() as session:
            tasks = (
                await session.execute(
                    text("SELECT queue_name, status, retry_count FROM review_tasks")
                )
            ).all()
            metrics = (
                await session.execute(
                    text("SELECT duration_ms, tokens_in, cost_usd FROM agent_metrics")
                )
            ).all()
        assert tasks == [("security_queue", "SUCCESS", 1)]
        assert metrics[0].duration_ms == 15
        assert metrics[0].cost_usd == 0.01
    finally:
        await engine.dispose()
