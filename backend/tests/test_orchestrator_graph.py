"""Unit tests for the orchestrator graph state machine (docs/AGENTS.md §2).

Exercises the LangGraph topology with a scripted fake runner: success, bounded
retry → re-run, retry exhaustion → diagnosed FAILED with the partial findings
preserved, and the hard per-agent timeout classification.
"""

from __future__ import annotations

import asyncio
import uuid

import pytest

from app.agents.contract import (
    AgentOutputError,
    AgentScope,
    AgentScopeConfigurationError,
    ChangeKind,
    FileSlice,
)
from app.agents.llm import LLMError, LLMRetryableError
from app.orchestrator.graph import build_orchestration_graph
from app.orchestrator.runners import InProcessAgentRunner, RunnerResult
from app.orchestrator.state import (
    TERMINAL_COMPLETE,
    TERMINAL_FAILED,
    empty_state,
)

REVIEW_ID = str(uuid.uuid4())
REPO_ID = str(uuid.uuid4())


class ScriptedRunner:
    """Broker/provider-free runner whose outcomes are scripted per agent."""

    name = "scripted"

    def __init__(self, script: dict[str, list[RunnerResult]]) -> None:
        self._script = {k: list(v) for k, v in script.items()}
        self.calls: list[str] = []

    async def agent_keys(self) -> tuple[str, ...]:
        return tuple(self._script)

    async def run(self, agent_key: str, scope: AgentScope) -> RunnerResult:
        self.calls.append(agent_key)
        queue = self._script[agent_key]
        if not queue:
            raise AssertionError(f"no scripted outcome left for {agent_key}")
        return queue.pop(0)


def _success(key: str, *, findings: tuple = ()) -> RunnerResult:
    return RunnerResult(key, success=True, findings=findings, stats={"duration_ms": 1})


def _retryable(key: str) -> RunnerResult:
    return RunnerResult(key, success=False, retryable=True, error="transient boom")


def _permanent(key: str, *, error: str = "hard boom") -> RunnerResult:
    return RunnerResult(key, success=False, retryable=False, error=error)


def _run_graph(runner, scope, **kwargs) -> dict:
    graph = build_orchestration_graph(runner, **kwargs)
    return asyncio.run(graph.ainvoke(empty_state(REVIEW_ID, REPO_ID, scope.to_dict())))


@pytest.fixture
def scope() -> AgentScope:
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


class TestGraphHappyPath:
    def test_all_agents_succeed_to_complete(self, scope: AgentScope) -> None:
        runner = ScriptedRunner(
            {
                "security": [
                    _success("security", findings=({"file_path": "app/auth.py"},))
                ]
            }
        )
        final = _run_graph(runner, scope)
        assert final["terminal"] == TERMINAL_COMPLETE
        assert final["pending"] == []
        assert final["failed"] == []
        assert final["root_cause"] is None
        assert final["results"][0]["agent_key"] == "security"
        assert len(final["results"][0]["findings"]) == 1

    def test_zero_findings_is_still_complete(self, scope: AgentScope) -> None:
        runner = ScriptedRunner({"security": [_success("security")]})
        final = _run_graph(runner, scope)
        assert final["terminal"] == TERMINAL_COMPLETE


class TestGraphRetry:
    def test_transient_failure_is_retried_then_succeeds(
        self, scope: AgentScope
    ) -> None:
        runner = ScriptedRunner(
            {"security": [_retryable("security"), _success("security")]}
        )
        final = _run_graph(runner, scope, max_retries=2)
        assert final["terminal"] == TERMINAL_COMPLETE
        assert final["retries"] == {"security": 1}
        assert runner.calls == ["security", "security"]

    def test_retry_history_recorded(self, scope: AgentScope) -> None:
        runner = ScriptedRunner(
            {"security": [_retryable("security"), _success("security")]}
        )
        final = _run_graph(runner, scope, max_retries=2)
        assert "security retry 1/2" in final["retry_history"]

    def test_exhausted_retries_diagnose_failed_and_keep_partial_findings(
        self, scope: AgentScope
    ) -> None:
        runner = ScriptedRunner(
            {
                "security": [
                    _success("security", findings=({"file_path": "app/auth.py"},))
                ],
                "quality": [_retryable("quality"), _retryable("quality")],
            }
        )
        final = _run_graph(runner, scope, max_retries=2)
        assert final["terminal"] == TERMINAL_FAILED
        assert final["retries"] == {"quality": 2}
        assert final["pending"] == []
        failed_keys = {f["agent_key"] for f in final["failed"]}
        assert failed_keys == {"quality"}
        assert "security" not in final["failed"]
        assert len(final["results"][0]["findings"]) == 1
        assert final["failed"][0]["attempts"] == 2
        assert any("partial findings still flow" in n for n in final["notes"])
        assert any("permanently failed" in n for n in final["notes"])

    def test_permanent_failure_skips_retry(self, scope: AgentScope) -> None:
        runner = ScriptedRunner({"security": [_permanent("security")]})
        final = _run_graph(runner, scope, max_retries=2)
        assert final["terminal"] == TERMINAL_FAILED
        assert final["retries"] == {}
        assert final["failed"][0]["error"] == "hard boom"


class TestGraphTimeout:
    def test_hard_timeout_is_permanent_failure(self, scope: AgentScope) -> None:
        class SlowRunner(ScriptedRunner):
            async def run(self, agent_key: str, scope: AgentScope) -> RunnerResult:
                await asyncio.sleep(0.5)
                return _success(agent_key)

        runner = SlowRunner({"security": []})
        final = _run_graph(runner, scope, agent_timeout_seconds=0.05)
        assert final["terminal"] == TERMINAL_FAILED
        assert final["failed"][0]["agent_key"] == "security"
        assert "0.05s hard timeout" in final["failed"][0]["error"]
        assert any("hard timeout" in n for n in final["notes"])

    def test_invalid_graph_arguments_rejected(self, scope: AgentScope) -> None:
        runner = ScriptedRunner({"security": [_success("security")]})
        with pytest.raises(ValueError):
            build_orchestration_graph(runner, max_retries=-1)
        with pytest.raises(ValueError):
            build_orchestration_graph(runner, agent_timeout_seconds=0)


class TestInProcessRunnerClassification:
    """Exception → RunnerResult classification used by the in-process default."""

    def test_retryable_maps_to_retryable(self) -> None:
        result = InProcessAgentRunner._classify(LLMRetryableError("boom"), "security")
        assert result.retryable is True

    def test_fatal_llm_error_is_permanent(self) -> None:
        result = InProcessAgentRunner._classify(LLMError("boom"), "security")
        assert result.retryable is False

    def test_output_error_is_permanent(self) -> None:
        result = InProcessAgentRunner._classify(AgentOutputError("nope"), "security")
        assert result.retryable is False
        assert "one repair attempt" in (result.error or "")

    def test_scope_configuration_error_is_permanent(self) -> None:
        result = InProcessAgentRunner._classify(
            AgentScopeConfigurationError("bad scope"), "security"
        )
        assert result.retryable is False

    def test_unexpected_exception_is_permanent(self) -> None:
        result = InProcessAgentRunner._classify(RuntimeError("crash"), "security")
        assert result.retryable is False
        assert "unexpected agent failure" in (result.error or "")
