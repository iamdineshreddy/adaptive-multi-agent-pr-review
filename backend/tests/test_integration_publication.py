"""End-to-end publication pipeline test (docs/ARCHITECTURE.md §4.5).

Broker-free integration: webhook ingest -> orchestrator (mock agents) -> ARUM
decision -> publisher -> mock GitHub API -> PUBLISHED. Exercises the full
detection -> decision -> publication path without PostgreSQL/Redis, using the
MemoryOrchestratorStore and an httpx.MockTransport-backed GitHubClient.
"""

from __future__ import annotations

import json
import uuid
from typing import Any

import httpx
import pytest

from app.agents.contract import AgentScope, ChangeKind, FileSlice
from app.config.settings import Settings
from app.github.client import GitHubClient
from app.models.enums import FindingStatus, ReviewStatus
from app.orchestrator.persistence import MemoryOrchestratorStore
from app.orchestrator.service import run_review
from app.publisher.service import publish_review

REVIEW_ID = uuid.uuid4()
REPO_ID = uuid.uuid4()
OWNER_REPO = "acme/authentication"


def _scope() -> AgentScope:
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
                    "     user = fetch_user(request)\n"
                    "+    require_active(user)\n"
                    "+    audit_login(user)\n"
                    "     return token(user)"
                ),
                new_start=1,
                new_end=7,
                change_kind=ChangeKind.MODIFIED,
            ),
        ),
    )


def _seed_store(findings: list[dict] | None = None) -> MemoryOrchestratorStore:
    store = MemoryOrchestratorStore()
    store.repositories[str(REPO_ID)] = {"id": str(REPO_ID), "full_name": OWNER_REPO}
    store.reviews[str(REVIEW_ID)] = {
        "id": str(REVIEW_ID),
        "repository_id": str(REPO_ID),
        "status": ReviewStatus.DECIDING.value,
        "status_history": [],
        "head_sha": "abc123def",
        "pr_number": 42,
        "pr_title": "Add authentication",
        "priority_score": 9.0,
        "supervisor_notes": [],
    }
    for finding in findings or []:
        store.findings.append(finding)
    return store


def _scheduled_finding(
    index: int,
    *,
    line_start: int = 12,
    arum_utility: float = 1.0,
    file_path: str = "app/auth.py",
    severity: str = "HIGH",
) -> dict:
    return {
        "id": f"finding-{index}",
        "review_id": str(REVIEW_ID),
        "repository_id": str(REPO_ID),
        "agent_key": "security",
        "agent_id": "00000000-0000-0000-0000-000000000001",
        "file_path": file_path,
        "line_start": line_start,
        "line_end": line_start,
        "category": "security/xss",
        "severity": severity,
        "confidence": 0.9,
        "title": f"Finding {index}",
        "description": f"Description for finding {index}.",
        "evidence": {},
        "suggested_fix": "Escape output",
        "reason_summary": f"Summary {index}.",
        "duplicate_group": None,
        "publication_status": FindingStatus.SCHEDULED.value,
        "arum_utility": arum_utility,
    }


def _client(handler) -> GitHubClient:
    return GitHubClient(
        base_url="https://api.github.com",
        token="test-token",
        transport=httpx.MockTransport(handler),
    )


def _review_json(review_id: int = 501, comments: list[dict] | None = None) -> dict:
    return {
        "id": review_id,
        "html_url": f"https://github.com/acme/authentication/pull/42#pr-{review_id}",
        "state": "COMMENTED",
        "comments": comments or [],
    }


class _MockRunner:
    """In-process agent runner that returns one finding per agent."""

    async def agent_keys(self) -> list[str]:
        return ["security", "quality", "performance", "architecture", "standards"]

    async def run(self, agent_key: str, scope: AgentScope) -> Any:
        from app.orchestrator.runners import RunnerResult

        findings = []
        for file_slice in scope.changed_files:
            if file_slice.file_path == "app/auth.py":
                findings.append(
                    {
                        "file_path": "app/auth.py",
                        "line_start": 12,
                        "line_end": 12,
                        "category": "security/xss",
                        "severity": "HIGH",
                        "confidence": 0.9,
                        "title": "Potential XSS vulnerability",
                        "description": "User input is rendered without escaping.",
                        "evidence": {"snippet": "render(user_input)"},
                        "suggested_fix": "Escape output before rendering.",
                        "reason_summary": "Unescaped user input in template.",
                        "related_categories": [],
                    }
                )
        return RunnerResult(
            agent_key=agent_key,
            success=True,
            findings=tuple(findings),
            stats={
                "duration_ms": 100,
                "tokens_in": 500,
                "tokens_out": 200,
                "cost_usd": 0.01,
                "findings_raw": len(findings),
                "findings_valid": len(findings),
                "failed_results": 0,
            },
        )


@pytest.fixture
def settings() -> Settings:
    return Settings()


async def test_full_pipeline_webhook_to_published(settings: Settings) -> None:
    """Webhook -> orchestrator -> ARUM -> publisher -> GitHub -> PUBLISHED."""
    github_calls: list[dict] = []

    def handler(request: httpx.Request) -> httpx.Response:
        body = json.loads(request.content)
        github_calls.append(body)
        comments = [
            {"id": 9000 + i, "path": c["path"], "line": c["line"]}
            for i, c in enumerate(body.get("comments") or [])
        ]
        return httpx.Response(200, json=_review_json(comments=comments))

    store = _seed_store()
    scope = _scope()

    # Step 1: Orchestrator runs agents, consolidates, ARUM selects
    await run_review(
        scope,
        runner=_MockRunner(),
        store=store,
        settings=settings,
    )

    # Review should be DECIDING with scheduled findings
    record = store.reviews[str(REVIEW_ID)]
    assert record["status"] == ReviewStatus.DECIDING.value
    scheduled = [
        f
        for f in store.findings
        if f["publication_status"] == FindingStatus.SCHEDULED.value
    ]
    assert len(scheduled) > 0

    # Step 2: Publisher posts to GitHub
    outcome_result = await publish_review(
        store, _client(handler), REVIEW_ID, settings=settings
    )

    assert outcome_result.posted > 0
    assert outcome_result.github_review_id == 501

    # Review is now PUBLISHED
    assert record["status"] == ReviewStatus.PUBLISHED.value
    assert record["github_review_id"] == 501
    assert record.get("published_at") is not None

    # Findings are PUBLISHED
    published = [
        f
        for f in store.findings
        if f["publication_status"] == FindingStatus.PUBLISHED.value
    ]
    assert len(published) == len(scheduled)

    # GitHub was called exactly once
    assert len(github_calls) == 1


async def test_publisher_idempotent_on_rerun(settings: Settings) -> None:
    """Publishing the same review twice does not create duplicate comments."""
    github_calls: list[dict] = []

    def handler(request: httpx.Request) -> httpx.Response:
        body = json.loads(request.content)
        github_calls.append(body)
        comments = [
            {"id": 9000 + i, "path": c["path"], "line": c["line"]}
            for i, c in enumerate(body.get("comments") or [])
        ]
        return httpx.Response(200, json=_review_json(comments=comments))

    store = _seed_store(findings=[_scheduled_finding(0)])

    # First publish
    result1 = await publish_review(
        store, _client(handler), REVIEW_ID, settings=settings
    )
    assert result1.posted == 1
    assert result1.already_published is False

    # Second publish (idempotent)
    result2 = await publish_review(
        store, _client(handler), REVIEW_ID, settings=settings
    )
    assert result2.already_published is True
    assert result2.posted == 0

    # GitHub was called exactly once
    assert len(github_calls) == 1


async def test_publisher_degrades_invalid_line_to_body(settings: Settings) -> None:
    """Findings with no valid line are folded into the review body."""
    github_calls: list[dict] = []

    def handler(request: httpx.Request) -> httpx.Response:
        body = json.loads(request.content)
        github_calls.append(body)
        return httpx.Response(200, json=_review_json(comments=[]))

    store = _seed_store(
        findings=[
            _scheduled_finding(0, line_start=None),
            _scheduled_finding(1, line_start=None),
        ]
    )

    result = await publish_review(store, _client(handler), REVIEW_ID, settings=settings)

    assert result.posted == 0
    assert result.degraded == 2
    assert result.github_review_id == 501

    # All findings still marked PUBLISHED (degraded into body)
    published = [
        f
        for f in store.findings
        if f["publication_status"] == FindingStatus.PUBLISHED.value
    ]
    assert len(published) == 2
    assert all(f["github_comment_id"] is None for f in published)

    # Review is PUBLISHED
    record = store.reviews[str(REVIEW_ID)]
    assert record["status"] == ReviewStatus.PUBLISHED.value


async def test_publisher_respects_comment_cap(settings: Settings) -> None:
    """Findings beyond the cap are folded into the review body."""
    github_calls: list[dict] = []

    def handler(request: httpx.Request) -> httpx.Response:
        body = json.loads(request.content)
        github_calls.append(body)
        comments = [
            {"id": 9000 + i, "path": c["path"], "line": c["line"]}
            for i, c in enumerate(body.get("comments") or [])
        ]
        return httpx.Response(200, json=_review_json(comments=comments))

    capped = Settings(publisher_max_comments_per_review=1)
    store = _seed_store(
        findings=[
            _scheduled_finding(0, line_start=12),
            _scheduled_finding(1, line_start=20),
        ]
    )

    result = await publish_review(store, _client(handler), REVIEW_ID, settings=capped)

    assert result.posted == 1
    assert result.degraded == 1

    # Only 1 inline comment in the GitHub call
    assert len(github_calls[0]["comments"]) == 1
