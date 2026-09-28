"""Publisher Celery task tests (docs/ARCHITECTURE.md §4.5, component J).

Covers the task-level concerns the broker-free service tests cannot:
- ``publish_review_task``: bounded retry countdown, dead-letter on exhaustion,
  idempotent re-run after a transient failure.
- ``dequeue_pending_task``: scan -> dispatch hand-off to ``publisher_queue``.
- Beat schedule wiring for the publisher scan.
"""

from __future__ import annotations

import types
import uuid
from collections.abc import Callable
from typing import cast

import httpx
import pytest
from celery.exceptions import MaxRetriesExceededError

from app.config.settings import Settings
from app.github.client import GitHubClient
from app.models.enums import FindingStatus, ReviewStatus
from app.orchestrator.persistence import MemoryOrchestratorStore
from app.publisher.tasks import _PUBLISH_QUEUE, publish_review_task, scan_and_dispatch

REVIEW_ID = uuid.uuid4()
REPO_ID = uuid.uuid4()
OWNER_REPO = "acme/authentication"


def _scheduled_finding(
    index: int,
    *,
    line_start: int | None = 12,
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


class _FakeTask:
    """Celery ``self`` stand-in: exposes ``request.retries`` and records retries."""

    def __init__(self, retries: int = 0) -> None:
        self.request = types.SimpleNamespace(retries=retries)
        self.retry_calls: list[dict[str, object]] = []

    def retry(self, **kwargs):  # type: ignore[no-untyped-def]
        self.retry_calls.append(kwargs)
        raise _TaskRetryError()


class _TaskRetryError(Exception):
    """Sentinel raised by the fake ``self.retry`` when a retry is scheduled."""


def _run_publish_task(
    task: _FakeTask, review_id: str
) -> dict[str, object]:
    """Invoke the task body with an injected fake task (bind semantics)."""
    impl = cast(
        Callable[[_FakeTask, str], dict[str, object]],
        publish_review_task.run.__func__,
    )
    return impl(task, review_id)


@pytest.fixture
def settings() -> Settings:
    return Settings()


class TestPublishReviewTask:
    def test_success_returns_outcome(
        self, monkeypatch: pytest.MonkeyPatch, settings: Settings
    ) -> None:
        store = _seed(findings=[_scheduled_finding(0)])

        def handler(request: httpx.Request) -> httpx.Response:
            return httpx.Response(
                200,
                json=_review_json(
                    comments=[{"id": 9000, "path": "app/auth.py", "line": 12}]
                ),
            )

        monkeypatch.setattr(
            "app.orchestrator.persistence.SqlOrchestratorStore", lambda: store
        )
        monkeypatch.setattr(
            "app.publisher.tasks._github_client", lambda: _client(handler)
        )
        task = _FakeTask()
        result = _run_publish_task(task, str(REVIEW_ID))
        assert result["github_review_id"] == 501
        assert result["posted"] == 1
        assert result["degraded"] == 0
        assert result["already_published"] is False

    def test_transient_error_schedules_bounded_retry(
        self, monkeypatch: pytest.MonkeyPatch, settings: Settings
    ) -> None:
        store = _seed(findings=[_scheduled_finding(0)])

        def handler(request: httpx.Request) -> httpx.Response:
            return httpx.Response(503, json={"message": "try again later"})

        monkeypatch.setattr(
            "app.orchestrator.persistence.SqlOrchestratorStore", lambda: store
        )
        monkeypatch.setattr(
            "app.publisher.tasks._github_client", lambda: _client(handler)
        )
        monkeypatch.setattr("app.publisher.tasks.get_settings", lambda: settings)
        task = _FakeTask(retries=0)
        with pytest.raises(_TaskRetryError):
            _run_publish_task(task, str(REVIEW_ID))
        assert len(task.retry_calls) == 1
        countdown = cast(int, task.retry_calls[0]["countdown"])
        assert countdown >= 15  # backoff_delay(0, 30, ...) + jitter

    def test_permanent_error_dead_letters(
        self, monkeypatch: pytest.MonkeyPatch, settings: Settings
    ) -> None:
        store = _seed(findings=[_scheduled_finding(0)])

        def handler(request: httpx.Request) -> httpx.Response:
            return httpx.Response(401, json={"message": "Bad credentials"})

        monkeypatch.setattr(
            "app.orchestrator.persistence.SqlOrchestratorStore", lambda: store
        )
        monkeypatch.setattr(
            "app.publisher.tasks._github_client", lambda: _client(handler)
        )
        monkeypatch.setattr("app.publisher.tasks.get_settings", lambda: settings)

        dead: list = []

        def fake_record(entry, log=None):  # type: ignore[no-untyped-def]
            dead.append(entry)

        monkeypatch.setattr("app.publisher.tasks.record_dead_letter", fake_record)
        task = _FakeTask(retries=settings.celery_max_retries)
        with pytest.raises(MaxRetriesExceededError):
            _run_publish_task(task, str(REVIEW_ID))
        assert len(dead) == 1
        assert dead[0].review_id == REVIEW_ID
        assert dead[0].queue == _PUBLISH_QUEUE

    def test_idempotent_rerun_after_transient_failure(
        self, monkeypatch: pytest.MonkeyPatch, settings: Settings
    ) -> None:
        """First call fails transiently; second call succeeds and publishes."""
        store = _seed(findings=[_scheduled_finding(0)])
        calls: list[int] = []

        def handler(request: httpx.Request) -> httpx.Response:
            calls.append(1)
            if len(calls) == 1:
                return httpx.Response(500, json={"message": "server error"})
            return httpx.Response(
                200,
                json=_review_json(
                    comments=[{"id": 9000, "path": "app/auth.py", "line": 12}]
                ),
            )

        monkeypatch.setattr(
            "app.orchestrator.persistence.SqlOrchestratorStore", lambda: store
        )
        monkeypatch.setattr(
            "app.publisher.tasks._github_client", lambda: _client(handler)
        )
        monkeypatch.setattr("app.publisher.tasks.get_settings", lambda: settings)

        task = _FakeTask(retries=0)
        with pytest.raises(_TaskRetryError):
            _run_publish_task(task, str(REVIEW_ID))

        # Second attempt succeeds
        task2 = _FakeTask(retries=1)
        result = _run_publish_task(task2, str(REVIEW_ID))
        assert result["posted"] == 1
        assert len(calls) == 2  # exactly one retry, no duplicates

    def test_already_published_skips_without_api_call(
        self, monkeypatch: pytest.MonkeyPatch, settings: Settings
    ) -> None:
        store = _seed(findings=[], status=ReviewStatus.PUBLISHED)

        def handler(request: httpx.Request) -> httpx.Response:
            raise AssertionError("no API call expected")

        monkeypatch.setattr(
            "app.orchestrator.persistence.SqlOrchestratorStore", lambda: store
        )
        monkeypatch.setattr(
            "app.publisher.tasks._github_client", lambda: _client(handler)
        )
        task = _FakeTask()
        result = _run_publish_task(task, str(REVIEW_ID))
        assert result["already_published"] is True


class TestDequeuePending:
    def test_scan_dispatches_pending_reviews(
        self, monkeypatch: pytest.MonkeyPatch, settings: Settings
    ) -> None:
        store = _seed(findings=[_scheduled_finding(0)])
        dispatched: list[uuid.UUID] = []

        async def fake_dispatch(review_id: uuid.UUID) -> None:
            dispatched.append(review_id)

        handed = _run_scan(store, fake_dispatch)
        assert handed == [str(REVIEW_ID)]
        assert dispatched == [REVIEW_ID]

    def test_scan_excludes_terminal_reviews(
        self, monkeypatch: pytest.MonkeyPatch, settings: Settings
    ) -> None:
        store = _seed(findings=[], status=ReviewStatus.PUBLISHED)
        dispatched: list[uuid.UUID] = []

        async def fake_dispatch(review_id: uuid.UUID) -> None:
            dispatched.append(review_id)

        handed = _run_scan(store, fake_dispatch)
        assert handed == []
        assert dispatched == []


def _run_scan(
    store: MemoryOrchestratorStore, dispatch: Callable
) -> list[str]:
    """Invoke scan_and_dispatch with an injected store."""
    import asyncio

    return asyncio.run(scan_and_dispatch(store, dispatch))


class TestBeatSchedule:
    def test_publisher_scan_on_default_queue(self) -> None:
        from app.queue.celery_app import celery_app

        schedule = celery_app.conf.beat_schedule
        assert "publisher-dequeue-pending" in schedule
        assert (
            schedule["publisher-dequeue-pending"]["task"]
            == "publisher.dequeue_pending"
        )
        assert schedule["publisher-dequeue-pending"]["schedule"] > 0

    def test_publish_task_routed_to_publisher_queue(self) -> None:
        from app.queue.celery_app import celery_app

        assert (
            celery_app.conf.task_routes["publisher.publish_review"]["queue"]
            == _PUBLISH_QUEUE
        )
