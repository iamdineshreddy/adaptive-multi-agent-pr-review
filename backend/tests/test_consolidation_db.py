"""Database integration tests for Phase 7 consolidation persistence.

Live-DB self-skipping, mirroring ``test_orchestrator_db.py``: exercises the
``SqlOrchestratorStore`` findings / finding_groups / embeddings writes against a
real PostgreSQL + pgvector, including the server-side vector literal insert
(no asyncpg codec needed in Phase 7).
"""

from __future__ import annotations

import uuid

import pytest
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncEngine

from app.config.settings import get_settings
from app.consolidation.datatypes import EmbeddingWrite, FindingGroupWrite, FindingWrite
from app.consolidation.redundancy import REDUNDANCY_METHOD_HYBRID
from app.consolidation.similarity import vector_sql_literal
from app.database import SessionFactory, create_db_engine
from app.models import Base
from app.orchestrator.persistence import SqlOrchestratorStore

REVIEW_ID = uuid.uuid4()
REPO_ID = uuid.uuid4()


def _finding_write(agent_key: str, finding_id: str) -> FindingWrite:
    return FindingWrite(
        id=finding_id,
        agent_key=agent_key,
        agent_id="00000000-0000-4000-8000-000000000001",
        review_id=str(REVIEW_ID),
        repository_id=str(REPO_ID),
        file_path="app/auth.py",
        line_start=10,
        line_end=12,
        category="security/xss",
        severity="HIGH",
        confidence=0.9,
        title="XSS risk",
        description="User input reaches output unescaped.",
        evidence={"snippet": "print(user_input)"},
        suggested_fix="Escape before rendering.",
        reason_summary="Evidence only.",
        duplicate_group=str(uuid.uuid5(uuid.NAMESPACE_OID, "preview-group")),
    )


async def _require_db() -> None:
    engine = create_db_engine()
    try:
        async with engine.connect() as conn:
            await conn.execute(text("SELECT 1"))
    except Exception as exc:  # pragma: no cover - environment dependent
        pytest.skip(
            f"PostgreSQL not reachable at {get_settings().database_url} "
            f"({type(exc).__name__}); skipping consolidation integration tests."
        )
    finally:
        await engine.dispose()


async def _reset_schema(engine: AsyncEngine) -> None:
    async with engine.begin() as conn:
        await conn.execute(text("CREATE EXTENSION IF NOT EXISTS vector"))
        await conn.run_sync(Base.metadata.drop_all)
        await conn.run_sync(Base.metadata.create_all)


async def _seed_repo_pr_review(engine: AsyncEngine) -> None:
    async with engine.begin() as conn:
        await conn.execute(
            text(
                "INSERT INTO repositories (id, github_id, full_name, default_branch) "
                "VALUES (:id, :gid, :name, 'main')"
            ),
            {
                "id": REPO_ID,
                "gid": 9001,
                "name": f"integration/cons-{uuid.uuid4().hex[:8]}",
            },
        )
        await conn.execute(
            text(
                "INSERT INTO pull_requests (id, repository_id, github_pr_id, number, "
                "title, author_login, base_ref, head_ref, head_sha, state, "
                "changed_files, additions, deletions) "
                "VALUES (:id, :rid, 9, 9, 't', 'a', 'main', 'feat', 'sha', "
                "'OPEN', 1, 2, 0)"
            ),
            {"id": uuid.uuid4(), "rid": REPO_ID},
        )
        await conn.execute(
            text(
                "INSERT INTO reviews (id, pull_request_id, repository_id, status, "
                "mode, priority_score, risk_class, github_delivery_id) "
                "VALUES (:id, :rid, :rid, 'DECIDING', 'OPENED', 6.5, 'MEDIUM', :d)"
            ),
            {"id": REVIEW_ID, "rid": REPO_ID, "d": str(uuid.uuid4())},
        )


async def test_sql_store_persists_consolidated_findings_groups_and_embeddings() -> None:
    await _require_db()
    engine = create_db_engine()
    try:
        await _reset_schema(engine)
        await _seed_repo_pr_review(engine)
        store = SqlOrchestratorStore()

        agent_ids = await store.sync_agents(
            [
                {"key": "security", "name": "Security", "version": "1.0"},
                {"key": "quality", "name": "Quality", "version": "1.0"},
            ]
        )
        # Use the real synced agent ids in the writes.
        security = _finding_write("security", str(uuid.uuid5(uuid.NAMESPACE_OID, "f1")))
        quality = _finding_write("quality", str(uuid.uuid5(uuid.NAMESPACE_OID, "f2")))
        security = _but(security, agent_id=agent_ids["security"])
        quality = _but(quality, agent_id=agent_ids["quality"])
        destination_group = str(uuid.uuid5(uuid.NAMESPACE_OID, "preview-group"))

        await store.create_findings(str(REVIEW_ID), [security, quality])
        await store.create_finding_group(
            str(REVIEW_ID),
            FindingGroupWrite(
                id=destination_group,
                representative_finding_id=security.id,
                member_count=2,
                redundancy_method=REDUNDANCY_METHOD_HYBRID,
                max_pairwise_similarity=0.99,
            ),
        )
        await store.update_finding_duplicates(
            str(REVIEW_ID),
            [
                (security.id, destination_group),
                (quality.id, destination_group),
            ],
        )
        await store.create_embeddings(
            str(REVIEW_ID),
            [
                EmbeddingWrite(
                    finding_id=security.id,
                    repository_id=str(REPO_ID),
                    content_hash="abc123",
                    vector=(0.1, 0.2, 0.3),
                    model="mock/text-embedding",
                )
            ],
        )

        async with SessionFactory() as session:
            findings = (
                await session.execute(
                    text(
                        "SELECT publication_status, agent_id, duplicate_group, "
                        "category FROM findings ORDER BY id"
                    )
                )
            ).all()
            groups = (
                await session.execute(
                    text(
                        "SELECT member_count, redundancy_method, "
                        "representative_finding_id FROM finding_groups"
                    )
                )
            ).all()
            embeds = (
                await session.execute(
                    text(
                        "SELECT content_hash, model, vector::text AS vector_text "
                        "FROM embeddings"
                    )
                )
            ).all()

        assert len(findings) == 2
        assert {row.publication_status for row in findings} == {"CANDIDATE"}
        assert {row.agent_id for row in findings} == set(agent_ids.values())
        assert {row.duplicate_group for row in findings} == {destination_group}
        assert {row.category for row in findings} == {"security/xss"}

        assert len(groups) == 1
        assert groups[0].member_count == 2
        assert groups[0].redundancy_method == REDUNDANCY_METHOD_HYBRID
        assert groups[0].representative_finding_id == security.id

        assert len(embeds) == 1
        assert embeds[0].content_hash == "abc123"
        assert embeds[0].model == "mock/text-embedding"
        assert embeds[0].vector_text == vector_sql_literal((0.1, 0.2, 0.3))
    finally:
        await engine.dispose()


def _but(finding: FindingWrite, **kwargs: object) -> FindingWrite:
    return FindingWrite(  # type: ignore[call-overload]
        **{  # type: ignore[arg-type]
            **{
                "id": finding.id,
                "agent_key": finding.agent_key,
                "agent_id": finding.agent_id,
                "review_id": finding.review_id,
                "repository_id": finding.repository_id,
                "file_path": finding.file_path,
                "line_start": finding.line_start,
                "line_end": finding.line_end,
                "category": finding.category,
                "severity": finding.severity,
                "confidence": finding.confidence,
                "title": finding.title,
                "description": finding.description,
                "evidence": finding.evidence,
                "suggested_fix": finding.suggested_fix,
                "reason_summary": finding.reason_summary,
                "duplicate_group": finding.duplicate_group,
            },
            **kwargs,
        }
    )
