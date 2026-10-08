"""Publication layer tests (docs/ARCHITECTURE.md §4.5, component J).

Broker-free: the MemoryOrchestratorStore seeds a DECIDING review with scheduled
findings and an ``httpx.MockTransport``-backed GitHubClient plays the API, so the
whole PUBLISH step (claim -> post -> mark) is proven without PostgreSQL/Redis.
"""

from __future__ import annotations

import json
import uuid
from datetime import timedelta

import httpx
import pytest

from app.config.settings import Settings
from app.github.client import GitHubClient
from app.models.enums import FindingStatus, ReviewStatus
from app.orchestrator.persistence import MemoryOrchestratorStore, utc_now
from app.publisher.service import PublishError, publish_review

REVIEW_ID = uuid.uuid4()
REPO_ID = uuid.uuid4()
OWNER_REPO = "acme/authentication"


def _scheduled_finding(
    index: int,
    *,
    line_start: int | None = None,
    arum_utility: float = 1.0,
    file_path: str = "app/auth.py",
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
        "severity": "HIGH",
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


def _seed(
    *,
    findings: list[dict] | None = None,
    status: ReviewStatus = ReviewStatus.DECIDING,
    claimed_at: str | None = None,
    priority_score: float = 9.0,
) -> MemoryOrchestratorStore:
    store = MemoryOrchestratorStore()
    store.reviews[str(REVIEW_ID)] = {
        "id": str(REVIEW_ID),
        "repository_id": str(REPO_ID),
        "status": status.value,
        "status_history": [],
        "head_sha": "abc123def",
        "pr_number": 42,
        "pr_title": "Add authentication",
        "priority_score": priority_score,
        "supervisor_notes": [],
    }
    if claimed_at is not None:
        store.reviews[str(REVIEW_ID)]["claimed_at"] = claimed_at
    store.repositories[str(REPO_ID)] = {"id": str(REPO_ID), "full_name": OWNER_REPO}
    for finding in findings or []:
        store.findings.append(finding)
    return store


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


@pytest.fixture
def settings() -> Settings:
    return Settings()


async def test_publish_posts_inline_findings_and_marks_published(
    settings: Settings,
) -> None:
    calls: list[dict[str, object]] = []

    def handler(request: httpx.Request) -> httpx.Response:
        comments = [
            {
                "id": 9000 + i,
                "path": c["path"],
                "line": c["line"],
                "body": c["body"],
            }
            for i, c in enumerate(json.loads(request.content).get("comments") or [])
        ]
        calls.append({"paths": [c["path"] for c in comments]})
        return httpx.Response(200, json=_review_json(comments=comments))

    store = _seed(
        findings=[
            _scheduled_finding(0, line_start=12, arum_utility=0.3),
            _scheduled_finding(1, line_start=20, arum_utility=0.9),
        ]
    )
    outcome = await publish_review(
        store, _client(handler), REVIEW_ID, settings=settings
    )

    assert len(calls) == 1
    assert outcome.posted == 2
    assert outcome.degraded == 0
    assert outcome.already_published is False
    assert outcome.github_review_id == 501

    record = store.reviews[str(REVIEW_ID)]
    assert record["status"] == ReviewStatus.PUBLISHED.value
    assert record["github_review_id"] == 501
    assert record.get("published_at") is not None

    published = [
        row
        for row in store.findings
        if row["publication_status"] == FindingStatus.PUBLISHED.value
    ]
    assert len(published) == 2
    assert {row["github_comment_id"] for row in published} == {9000, 9001}


async def test_publish_orders_inline_comments_by_arum_utility(
    settings: Settings,
) -> None:
    paths: list[str] = []

    def handler(request: httpx.Request) -> httpx.Response:
        paths.extend(
            comment["path"]
            for comment in json.loads(request.content).get("comments") or []
        )
        return httpx.Response(200, json=_review_json(review_id=502))

    # Highest arum first: finding-1 (0.9) must be posted before finding-0 (0.2).
    store = _seed(
        findings=[
            _scheduled_finding(
                0, line_start=12, arum_utility=0.2, file_path="app/a.py"
            ),
            _scheduled_finding(
                1, line_start=20, arum_utility=0.9, file_path="app/b.py"
            ),
        ]
    )
    await publish_review(store, _client(handler), REVIEW_ID, settings=settings)

    assert len(paths) == 2
    # The publisher sorts by arum_utility desc: finding-1 (0.9) posts first.
    assert paths == ["app/b.py", "app/a.py"]


async def test_publish_degrades_off_diff_findings_to_body(settings: Settings) -> None:
    calls: list[list[str]] = []

    def handler(request: httpx.Request) -> httpx.Response:
        comments = list(json.loads(request.content).get("comments") or [])
        calls.append([c["path"] for c in comments])
        return httpx.Response(200, json=_review_json(review_id=503, comments=[]))

    # No line numbers -> every finding folds into the review body (no inline call).
    store = _seed(
        findings=[
            _scheduled_finding(0, line_start=None),
            _scheduled_finding(1, line_start=None),
        ]
    )
    outcome = await publish_review(
        store, _client(handler), REVIEW_ID, settings=settings
    )

    assert calls == [[]]
    assert outcome.posted == 0
    assert outcome.degraded == 2
    assert store.reviews[str(REVIEW_ID)]["status"] == ReviewStatus.PUBLISHED.value
    assert all(
        row["github_comment_id"] is None
        for row in store.findings
        if row["publication_status"] == FindingStatus.PUBLISHED.value
    )


async def test_publish_degrades_rejected_inline_to_body(settings: Settings) -> None:
    attempts: list[int] = []

    def handler(request: httpx.Request) -> httpx.Response:
        attempts.append(len(json.loads(request.content).get("comments") or []))
        if len(attempts) == 1:
            return httpx.Response(422, json={"message": "comments must be on the diff"})
        return httpx.Response(200, json=_review_json(review_id=504, comments=[]))

    store = _seed(
        findings=[
            _scheduled_finding(0, line_start=12),
            _scheduled_finding(1, line_start=20),
        ]
    )
    outcome = await publish_review(
        store, _client(handler), REVIEW_ID, settings=settings
    )

    assert attempts == [2, 0]  # inline attempt rejected -> body-only re-post
    assert outcome.posted == 0
    assert outcome.degraded == 2
    record = store.reviews[str(REVIEW_ID)]
    assert record["status"] == ReviewStatus.PUBLISHED.value
    assert record["github_review_id"] == 504
    assert all(
        row["publication_status"] == FindingStatus.PUBLISHED.value
        for row in store.findings
    )


async def test_publish_transient_error_releases_claim(settings: Settings) -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(503, json={"message": "try again later"})

    store = _seed(findings=[_scheduled_finding(0, line_start=12)])
    with pytest.raises(PublishError) as excinfo:
        await publish_review(store, _client(handler), REVIEW_ID, settings=settings)

    assert excinfo.value.retryable is True
    record = store.reviews[str(REVIEW_ID)]
    assert record["status"] == ReviewStatus.DECIDING.value  # released, not stuck
    assert "claimed_at" not in record


async def test_publish_nothing_selected_completes_without_post(
    settings: Settings,
) -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        raise AssertionError("no API call expected")

    store = _seed(findings=[])
    outcome = await publish_review(
        store, _client(handler), REVIEW_ID, settings=settings
    )

    assert outcome.posted == 0
    assert outcome.github_review_id is None
    record = store.reviews[str(REVIEW_ID)]
    assert record["status"] == ReviewStatus.PUBLISHED.value


async def test_publish_skips_already_published(settings: Settings) -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        raise AssertionError("no API call expected")

    store = _seed(findings=[], status=ReviewStatus.PUBLISHED)
    outcome = await publish_review(
        store, _client(handler), REVIEW_ID, settings=settings
    )

    assert outcome.already_published is True
    assert store.reviews[str(REVIEW_ID)]["status"] == ReviewStatus.PUBLISHED.value


async def test_publish_respects_comments_per_review_cap(settings: Settings) -> None:
    comments_payload: list[list[dict]] = []

    def handler(request: httpx.Request) -> httpx.Response:
        comments_payload.append(list(json.loads(request.content).get("comments") or []))
        return httpx.Response(200, json=_review_json(review_id=505))

    capped = Settings(publisher_max_comments_per_review=1)
    store = _seed(
        findings=[
            _scheduled_finding(0, line_start=12),
            _scheduled_finding(1, line_start=20),
        ]
    )
    outcome = await publish_review(store, _client(handler), REVIEW_ID, settings=capped)

    assert len(comments_payload[0]) == 1  # second finding folded into the body
    assert outcome.posted == 1
    assert outcome.degraded == 1


async def test_claim_is_atomic_and_only_one_claimer_wins(settings: Settings) -> None:
    store = _seed(findings=[])
    now = utc_now()
    first = await store.claim_review_for_publication(
        REVIEW_ID, claim_stale_before=now - timedelta(seconds=1)
    )
    second = await store.claim_review_for_publication(
        REVIEW_ID, claim_stale_before=now - timedelta(seconds=1)
    )
    assert first is True  # first claimer wins
    assert second is False  # already claimed (not stale)

    record = store.reviews[str(REVIEW_ID)]
    assert record["status"] == ReviewStatus.PUBLISHING.value
    assert "claimed_at" in record


async def test_claim_reclaims_crashed_publishing_review(settings: Settings) -> None:
    now = utc_now()
    store = _seed(
        findings=[],
        status=ReviewStatus.PUBLISHING,
        claimed_at=(now - timedelta(minutes=10)).isoformat(),
    )
    claimed = await store.claim_review_for_publication(
        REVIEW_ID, claim_stale_before=now - timedelta(seconds=5)
    )
    assert claimed is True  # stale claim from a dead worker is reclaimed

    fresh = _seed(
        findings=[],
        status=ReviewStatus.PUBLISHING,
        claimed_at=now.isoformat(),
    )
    not_reclaimed = await fresh.claim_review_for_publication(
        REVIEW_ID, claim_stale_before=now - timedelta(seconds=5)
    )
    assert not_reclaimed is False  # not yet stale


async def test_release_returns_claimed_review_to_deciding(settings: Settings) -> None:
    store = _seed(findings=[])
    assert await store.claim_review_for_publication(
        REVIEW_ID, claim_stale_before=utc_now()
    )
    await store.release_review_from_publication(REVIEW_ID)

    record = store.reviews[str(REVIEW_ID)]
    assert record["status"] == ReviewStatus.DECIDING.value
    assert "claimed_at" not in record


async def test_list_pending_orders_by_priority_and_excludes_terminal() -> None:
    store = _seed(findings=[], status=ReviewStatus.DECIDING, priority_score=1.0)
    low_id = uuid.uuid4()
    store.reviews[str(low_id)] = {
        "id": str(low_id),
        "repository_id": str(REPO_ID),
        "status": ReviewStatus.ITERATING.value,
        "status_history": [],
        "priority_score": 5.0,
    }
    done = uuid.uuid4()
    store.reviews[str(done)] = {
        "id": str(done),
        "repository_id": str(REPO_ID),
        "status": ReviewStatus.PUBLISHED.value,
        "status_history": [],
    }

    pending = await store.list_reviews_pending_publication(
        limit=10, claim_stale_before=utc_now()
    )
    assert str(done) not in pending
    assert pending == [str(low_id), str(REVIEW_ID)]
