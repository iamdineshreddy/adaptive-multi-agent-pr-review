"""Unit tests for the agent-execution boundaries (docs/AGENTS.md §2-3)."""

from __future__ import annotations

import uuid

import pytest
from celery.exceptions import TimeoutError as CeleryTimeoutError

from app.agents.contract import AgentScope
from app.orchestrator.runners import CeleryFanOutRunner, _payload_to_result

SCOPE = AgentScope(
    review_id=uuid.uuid4(),
    repository_id=uuid.uuid4(),
    pr_number=1,
    pr_title="t",
    pr_description="",
    base_ref="main",
    head_ref="feat",
    changed_files=(),
)


class FakeTask:
    def __init__(self, payload) -> None:
        self._payload = payload

    id = "task-1"

    def get(self, *, timeout=None, interval=None, propagate=True):
        if isinstance(self._payload, Exception):
            raise self._payload
        return self._payload


def _runner_with(monkeypatch: pytest.MonkeyPatch, task: FakeTask) -> CeleryFanOutRunner:
    captured: dict = {}

    def fake_send_task(name: str, args: list, queue: str) -> FakeTask:
        captured["name"] = name
        captured["args"] = args
        captured["queue"] = queue
        return task

    monkeypatch.setattr("app.orchestrator.runners.celery_app.send_task", fake_send_task)
    runner = CeleryFanOutRunner()
    runner._sent = captured  # type: ignore[attr-defined]
    return runner


class TestCeleryFanOutRunner:
    async def test_dispatches_to_agent_queue_and_joins_payload(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        payload = {
            "review_id": str(SCOPE.review_id),
            "agent_key": "security",
            "findings": [{"file_path": "app/auth.py", "category": "security/xss"}],
            "stats": {"duration_ms": 12},
        }
        runner = _runner_with(monkeypatch, FakeTask(payload))
        result = await runner.run("security", SCOPE)
        assert result.success is True
        assert len(result.findings) == 1
        assert result.stats == {"duration_ms": 12}
        assert runner._sent["name"] == "agents.run_agent"  # type: ignore[attr-defined]
        assert runner._sent["queue"] == "security_queue"  # type: ignore[attr-defined]

    async def test_join_timeout_is_retryable(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        runner = _runner_with(monkeypatch, FakeTask(CeleryTimeoutError(60)))
        result = await runner.run("security", SCOPE)
        assert result.success is False
        assert result.retryable is True
        assert "join timed out" in (result.error or "")

    async def test_broker_failure_is_permanent(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        runner = _runner_with(monkeypatch, FakeTask(RuntimeError("worker died")))
        result = await runner.run("security", SCOPE)
        assert result.success is False
        assert result.retryable is False
        assert "worker died" in (result.error or "")

    async def test_malformed_payload_is_permanent(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        runner = _runner_with(monkeypatch, FakeTask({"not": "the right shape"}))
        result = await runner.run("security", SCOPE)
        assert result.success is False
        assert result.retryable is False
        assert "malformed" in (result.error or "")


class TestPayloadToResult:
    def test_wrong_agent_key_rejected(self) -> None:
        result = _payload_to_result("security", {"agent_key": "quality"})
        assert result.success is False
        assert "malformed" in (result.error or "")

    def test_non_dict_payload_rejected(self) -> None:
        result = _payload_to_result("security", "just a string")
        assert result.success is False

    def test_non_dict_findings_are_dropped(self) -> None:
        payload = {
            "agent_key": "security",
            "findings": [{"file_path": "a.py"}, "oops"],
        }
        result = _payload_to_result("security", payload)
        assert result.success is True
        assert len(result.findings) == 1
