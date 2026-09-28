"""Integration test: iterative review + publisher (docs/ARCHITECTURE.md §4.7).

Verifies the full synchronize flow:
1. First review publishes findings to GitHub
2. Developer modifies code (synchronize event)
3. Iteration engine classifies findings: RESOLVED / STALE / KEEP
4. Only new/updated findings are published (no duplicates)
5. Resolved findings are not republished
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
from app.orchestrator.iteration import DeltaAction, plan_review_delta
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


async def test_iteration_classifies_findings_correctly(settings: Settings) -> None:
    """Verify the iteration engine classifies findings as RESOLVED/STALE/KEEP."""
    previous_findings = [
        {
            "id": "f-1",
            "file_path": "app/auth.py",
            "category": "security/xss",
            "line_start": 5,
            "line_end": 12,
        },
        {
            "id": "f-2",
            "file_path": "app/removed.py",
            "category": "quality/maintainability",
            "line_start": 5,
            "line_end": 8,
        },
        {
            "id": "f-3",
            "file_path": "app/untouched.py",
            "category": "performance/query",
            "line_start": 20,
            "line_end": 25,
        },
    ]

    changed_slices = [
        FileSlice(
            "app/auth.py",
            "@@ -1,5 +1,7 @@\n",
            new_start=1,
            new_end=7,
            change_kind=ChangeKind.MODIFIED,
        ),
        FileSlice(
            "app/removed.py",
            "",
            new_start=None,
            new_end=None,
            change_kind=ChangeKind.REMOVED,
        ),
    ]

    plan = plan_review_delta(previous_findings, changed_slices)

    assert plan.resolutions_by(DeltaAction.RESOLVED)[0].finding_id == "f-2"
    assert plan.resolutions_by(DeltaAction.STALE)[0].finding_id == "f-1"
    assert plan.resolutions_by(DeltaAction.KEEP)[0].finding_id == "f-3"
    assert plan.resolved_count == 1
    assert plan.stale_count == 1
    assert plan.keep_count == 1


async def test_iteration_publisher_no_duplicates(settings: Settings) -> None:
    """Verify that unchanged findings are not republished on iteration."""
    github_calls: list[dict] = []

    def handler(request: httpx.Request) -> httpx.Response:
        body = json.loads(request.content)
        github_calls.append(body)
        comments = [
            {"id": 9000 + i, "path": c["path"], "line": c["line"]}
            for i, c in enumerate(body.get("comments") or [])
        ]
        return httpx.Response(200, json=_review_json(comments=comments))

    # First review: publish findings
    store = _seed_store(findings=[_scheduled_finding(0)])
    result1 = await publish_review(
        store, _client(handler), REVIEW_ID, settings=settings
    )
    assert result1.posted == 1
    assert len(github_calls) == 1

    # Simulate iteration: finding-0 is KEEP (file unchanged)
    # The publisher should NOT republish it — the review is already PUBLISHED
    # so the claim fails and the publisher short-circuits.
    result2 = await publish_review(
        store, _client(handler), REVIEW_ID, settings=settings
    )
    # Already published — no new API call
    assert result2.already_published is True
    assert len(github_calls) == 1  # Still only 1 call


async def test_iteration_publisher_new_findings(settings: Settings) -> None:
    """Verify that new findings from iteration are published."""
    github_calls: list[dict] = []

    def handler(request: httpx.Request) -> httpx.Response:
        body = json.loads(request.content)
        github_calls.append(body)
        comments = [
            {"id": 9000 + i, "path": c["path"], "line": c["line"]}
            for i, c in enumerate(body.get("comments") or [])
        ]
        return httpx.Response(200, json=_review_json(comments=comments))

    # First review: publish finding-0
    store = _seed_store(findings=[_scheduled_finding(0)])
    result1 = await publish_review(
        store, _client(handler), REVIEW_ID, settings=settings
    )
    assert result1.posted == 1
    assert len(github_calls) == 1

    # Simulate iteration: finding-0 is RESOLVED, new finding-1 is STALE
    store.reviews[str(REVIEW_ID)]["status"] = ReviewStatus.DECIDING.value
    store.findings[0]["publication_status"] = FindingStatus.RESOLVED.value
    store.findings.append(_scheduled_finding(1, line_start=20))

    result2 = await publish_review(
        store, _client(handler), REVIEW_ID, settings=settings
    )
    assert result2.posted == 1  # Only finding-1 is new
    assert len(github_calls) == 2  # Second API call for new finding

    # Verify finding-0 is still RESOLVED (not republished)
    assert store.findings[0]["publication_status"] == FindingStatus.RESOLVED.value
    # Verify finding-1 is now PUBLISHED
    assert store.findings[1]["publication_status"] == FindingStatus.PUBLISHED.value


async def test_full_iteration_flow(settings: Settings) -> None:
    """Full flow: first review → publish → synchronize → targeted re-review → publish."""
    github_calls: list[dict] = []

    def handler(request: httpx.Request) -> httpx.Response:
        body = json.loads(request.content)
        github_calls.append(body)
        comments = [
            {"id": 9000 + i, "path": c["path"], "line": c["line"]}
            for i, c in enumerate(body.get("comments") or [])
        ]
        return httpx.Response(200, json=_review_json(comments=comments))

    # Step 1: First review
    store = _seed_store()
    scope = _scope()
    await run_review(
        scope,
        runner=_MockRunner(),
        store=store,
        settings=settings,
    )

    # Publish first review
    result1 = await publish_review(
        store, _client(handler), REVIEW_ID, settings=settings
    )
    assert result1.posted > 0
    first_call_count = len(github_calls)

    # Step 2: Simulate synchronize — new commit changes app/auth.py
    # The iteration engine classifies previous findings
    previous_findings = [
        {
            "id": f["id"],
            "file_path": f["file_path"],
            "category": f["category"],
            "line_start": f["line_start"],
            "line_end": f["line_end"],
        }
        for f in store.findings
    ]

    new_slices = [
        FileSlice(
            "app/auth.py",
            "@@ -1,7 +1,9 @@\n",
            new_start=1,
            new_end=12,
            change_kind=ChangeKind.MODIFIED,
        ),
    ]

    plan = plan_review_delta(previous_findings, new_slices)
    assert plan.stale_count > 0  # Findings in changed regions are STALE

    # Step 3: Apply iteration delta and re-run targeted agents
    await store.apply_iteration_delta(REVIEW_ID, plan.resolutions)

    # Step 4: New review with targeted agents
    store.reviews[str(REVIEW_ID)]["status"] = ReviewStatus.ITERATING.value
    await run_review(
        scope,
        runner=_MockRunner(),
        store=store,
        settings=settings,
    )

    # Step 5: Publish new findings
    result2 = await publish_review(
        store, _client(handler), REVIEW_ID, settings=settings
    )

    # New findings should be published (not duplicates of old ones)
    assert result2.posted > 0
    assert len(github_calls) > first_call_count

    # Review should be PUBLISHED
    record = store.reviews[str(REVIEW_ID)]
    assert record["status"] == ReviewStatus.PUBLISHED.value
