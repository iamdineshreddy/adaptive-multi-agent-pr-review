"""Demonstration: full review pipeline from webhook to published GitHub review.

Runs the complete flow using the Memory store and mock GitHub client:
webhook → priority scoring → orchestrator → agents → consolidation → ARUM →
publisher → GitHub

Usage: python demo_run.py
"""

from __future__ import annotations

import asyncio
import json
import uuid

import httpx

from app.agents.contract import AgentScope, ChangeKind, FileSlice
from app.config.settings import Settings
from app.github.client import GitHubClient
from app.models.enums import FindingStatus, ReviewStatus
from app.orchestrator.persistence import MemoryOrchestratorStore
from app.orchestrator.runners import RunnerResult
from app.orchestrator.service import run_review
from app.publisher.service import publish_review
from app.webhooks.priority import PriorityFactors, assemble_score, risk_class_for

# --- Test data ---------------------------------------------------------------

REVIEW_ID = uuid.uuid4()
REPO_ID = uuid.uuid4()
OWNER_REPO = "acme/authentication"


def _scope() -> AgentScope:
    return AgentScope(
        review_id=REVIEW_ID,
        repository_id=REPO_ID,
        pr_number=42,
        pr_title="Add authentication",
        pr_description="Implements login flow with session management.",
        base_ref="main",
        head_ref="feat/auth",
        language="python",
        changed_files=(
            FileSlice(
                file_path="app/auth.py",
                patch=(
                    "@@ -1,5 +1,12 @@\n"
                    " def login():\n"
                    "     user = fetch_user(request)\n"
                    "+    token = generate_token(user)\n"
                    "+    session['user_id'] = user.id\n"
                    "+    session['token'] = token\n"
                    "+    return redirect('/dashboard')\n"
                    "+\n"
                    " def logout():\n"
                    "+    session.clear()\n"
                    "     return redirect('/login')"
                ),
                new_start=1,
                new_end=12,
                change_kind=ChangeKind.MODIFIED,
            ),
            FileSlice(
                file_path="app/queries.py",
                patch=(
                    "@@ -1,3 +1,8 @@\n"
                    " def get_user(user_id):\n"
                    '+    query = f"SELECT * FROM users WHERE id = {user_id}"\n'
                    "+    return db.execute(query)\n"
                    "+\n"
                    " def get_orders(user_id):\n"
                    '+    query = f"SELECT * FROM orders WHERE user_id = {user_id}"\n'
                    "+    return db.execute(query)"
                ),
                new_start=1,
                new_end=8,
                change_kind=ChangeKind.MODIFIED,
            ),
        ),
    )


class _MockRunner:
    """In-process agent runner that returns realistic findings."""

    async def agent_keys(self) -> list[str]:
        return ["security", "quality", "performance", "architecture", "standards"]

    async def run(self, agent_key: str, scope: AgentScope) -> RunnerResult:
        findings = []
        for file_slice in scope.changed_files:
            if file_slice.file_path == "app/auth.py":
                if agent_key == "security":
                    findings.append(
                        {
                            "file_path": "app/auth.py",
                            "line_start": 3,
                            "line_end": 3,
                            "category": "security/session",
                            "severity": "HIGH",
                            "confidence": 0.85,
                            "title": "Session token stored in client-side session",
                            "description": (
                                "The authentication token is stored in a "
                                "client-side session without HttpOnly flag."
                            ),
                            "evidence": {"snippet": "session['token'] = token"},
                            "suggested_fix": (
                                "Set session cookies with HttpOnly and Secure flags."
                            ),
                            "reason_summary": "Token accessible via XSS.",
                            "related_categories": [],
                        }
                    )
                elif agent_key == "quality":
                    findings.append(
                        {
                            "file_path": "app/auth.py",
                            "line_start": 1,
                            "line_end": 12,
                            "category": "quality/maintainability",
                            "severity": "LOW",
                            "confidence": 0.6,
                            "title": "Login function lacks input validation",
                            "description": (
                                "No validation on user input before processing."
                            ),
                            "evidence": {"snippet": "def login():"},
                            "suggested_fix": (
                                "Add input validation for user credentials."
                            ),
                            "reason_summary": "Missing input validation.",
                            "related_categories": [],
                        }
                    )
            elif file_slice.file_path == "app/queries.py":
                if agent_key == "security":
                    findings.append(
                        {
                            "file_path": "app/queries.py",
                            "line_start": 2,
                            "line_end": 2,
                            "category": "security/sql-injection",
                            "severity": "CRITICAL",
                            "confidence": 0.95,
                            "title": "SQL injection vulnerability",
                            "description": (
                                "User input is directly interpolated into "
                                "SQL query string."
                            ),
                            "evidence": {
                                "snippet": 'f"SELECT * FROM users WHERE id = {user_id}"'
                            },
                            "suggested_fix": (
                                "Use parameterized queries: db.execute("
                                "'SELECT * FROM users WHERE id = %s', (user_id,))"
                            ),
                            "reason_summary": "Direct string interpolation in SQL.",
                            "related_categories": [],
                        }
                    )
                elif agent_key == "performance":
                    findings.append(
                        {
                            "file_path": "app/queries.py",
                            "line_start": 2,
                            "line_end": 2,
                            "category": "performance/query",
                            "severity": "MEDIUM",
                            "confidence": 0.7,
                            "title": "Repeated query pattern",
                            "description": (
                                "Similar query pattern repeated for different tables."
                            ),
                            "evidence": {"snippet": "db.execute(query)"},
                            "suggested_fix": (
                                "Consider a generic query helper function."
                            ),
                            "reason_summary": "Code duplication in query patterns.",
                            "related_categories": [],
                        }
                    )
        return RunnerResult(
            agent_key=agent_key,
            success=True,
            findings=tuple(findings),
            stats={
                "duration_ms": 150,
                "tokens_in": 800,
                "tokens_out": 300,
                "cost_usd": 0.02,
                "findings_raw": len(findings),
                "findings_valid": len(findings),
                "failed_results": 0,
            },
        )


def _print_header(text: str) -> None:
    print(f"\n{'=' * 70}")
    print(f"  {text}")
    print(f"{'=' * 70}")


def _print_step(step: int, text: str) -> None:
    print(f"\n[{step}] {text}")


async def main() -> None:
    settings = Settings()
    store = MemoryOrchestratorStore()
    store.repositories[str(REPO_ID)] = {"id": str(REPO_ID), "full_name": OWNER_REPO}

    # Track GitHub API calls
    github_calls: list[dict] = []

    def github_handler(request: httpx.Request) -> httpx.Response:
        body = json.loads(request.content)
        github_calls.append(body)
        comments = [
            {"id": 9000 + i, "path": c["path"], "line": c["line"]}
            for i, c in enumerate(body.get("comments") or [])
        ]
        return httpx.Response(
            200,
            json={
                "id": 501,
                "html_url": f"https://github.com/{OWNER_REPO}/pull/42#pr-501",
                "state": "COMMENTED",
                "comments": comments,
            },
        )

    _print_header("ADAPTIVE MULTI-AGENT PR REVIEW — LIVE DEMONSTRATION")

    # Step 1: Webhook ingestion
    _print_step(1, "WEBHOOK INGESTION")
    scope = _scope()
    factors = PriorityFactors(
        security_risk=0.8,  # auth + SQL injection surface
        change_impact=0.6,  # 2 files, ~20 lines
        historical_risk=0.0,
        component_criticality=0.5,  # auth component
        dependency_risk=0.0,
        repo_priority=0.0,
    )
    score = assemble_score(factors, settings)
    risk = risk_class_for(score, settings)
    print(f"  Priority score: {score:.2f}")
    print(f"  Risk class: {risk.value}")
    print(f"  Changed files: {len(scope.changed_files)}")
    for f in scope.changed_files:
        print(f"    - {f.file_path} ({f.new_end - f.new_start + 1} lines)")

    # Step 2: Orchestrator runs agents
    _print_step(2, "ORCHESTRATOR — AGENT FAN-OUT")
    print(f"  Review ID: {REVIEW_ID}")
    print("  Agents: security, quality, performance, architecture, standards")

    # Ensure the review record has repository_id set (needed by publisher)
    store.reviews[str(REVIEW_ID)] = {
        "id": str(REVIEW_ID),
        "repository_id": str(REPO_ID),
        "status": ReviewStatus.DECIDING.value,
        "status_history": [],
        "head_sha": "abc123def",
        "pr_number": 42,
        "pr_title": "Add authentication",
        "priority_score": 4.65,
        "supervisor_notes": [],
    }

    outcome = await run_review(
        scope,
        runner=_MockRunner(),
        store=store,
        settings=settings,
    )

    print(f"  Agents completed: {len(outcome.agent_keys)}")
    print(f"  Raw findings: {len(outcome.findings)}")
    print(f"  Review status: {outcome.status}")

    # Step 3: Consolidation + ARUM
    _print_step(3, "CONSOLIDATION + ARUM DECISION")
    record = store.reviews[str(REVIEW_ID)]
    print(f"  Review status: {record['status']}")
    print(f"  ARUM version: {record.get('arum_version', 'N/A')}")
    print(f"  Budget cap: {record.get('budget_cap', 'N/A')}")
    print(f"  Selected: {record.get('selected_count', 0)}")
    print(f"  Suppressed: {record.get('suppressed_count', 0)}")

    scheduled = [
        f
        for f in store.findings
        if f["publication_status"] == FindingStatus.SCHEDULED.value
    ]
    suppressed = [
        f
        for f in store.findings
        if f["publication_status"] == FindingStatus.SUPPRESSED.value
    ]
    print("\n  Findings by status:")
    print(f"    SCHEDULED: {len(scheduled)}")
    for f in scheduled:
        utility = f.get("arum_utility", 0)
        print(f"      - [{f['severity']}] {f['title']} (utility: {utility:.3f})")
    print(f"    SUPPRESSED: {len(suppressed)}")
    for f in suppressed:
        print(f"      - [{f['severity']}] {f['title']}")

    # Step 4: Publisher
    _print_step(4, "PUBLISHER — POST TO GITHUB")
    client = GitHubClient(
        base_url="https://api.github.com",
        token="demo-token",
        transport=httpx.MockTransport(github_handler),
    )

    result = await publish_review(store, client, REVIEW_ID, settings=settings)

    print(f"  GitHub review ID: {result.github_review_id}")
    print(f"  Comments posted: {result.posted}")
    print(f"  Degraded to body: {result.degraded}")
    print(f"  Already published: {result.already_published}")

    # Step 5: Final state
    _print_step(5, "FINAL STATE")
    record = store.reviews[str(REVIEW_ID)]
    print(f"  Review status: {record['status']}")
    print(f"  GitHub review ID: {record.get('github_review_id')}")
    print(f"  Published at: {record.get('published_at')}")

    published = [
        f
        for f in store.findings
        if f["publication_status"] == FindingStatus.PUBLISHED.value
    ]
    print(f"\n  Published findings: {len(published)}")
    for f in published:
        comment_id = f.get("github_comment_id")
        print(f"    - [{f['severity']}] {f['title']}")
        print(f"      File: {f['file_path']}:{f['line_start']}")
        print(f"      GitHub comment: {comment_id}")

    # Step 6: Idempotency check
    _print_step(6, "IDEMPOTENCY CHECK — RE-PUBLISH")
    result2 = await publish_review(store, client, REVIEW_ID, settings=settings)
    print(f"  Already published: {result2.already_published}")
    print(f"  New API calls: {len(github_calls) - 1} (should be 0)")

    _print_header("DEMONSTRATION COMPLETE")
    print(f"  Total GitHub API calls: {len(github_calls)}")
    print(f"  Review status: {record['status']}")
    print(f"  Findings published: {len(published)}")
    print(f"  Idempotent: {result2.already_published}")
    print()


if __name__ == "__main__":
    asyncio.run(main())
