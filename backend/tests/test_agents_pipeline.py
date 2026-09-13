"""End-to-end agent pipeline tests (docs/AGENTS.md §2-4, Phase 5)."""

from __future__ import annotations

import json

import pytest

from app.agents.base import BaseReviewAgent
from app.agents.contract import AgentOutputError, AgentScopeConfigurationError
from app.agents.llm import (
    LLMError,
    LLMRetryableError,
    MockLLMProvider,
)
from app.agents.prompts import PROMPT_SPECS
from app.agents.registry import agent_keys, agent_metadata, get_agent, queue_for_agent
from app.agents.runner import run_agent
from app.queue.agent_tasks import execute_agent_task_core


def _batch_content(*items: dict) -> str:
    return json.dumps({"findings": list(items)})


class TestRegistry:
    def test_five_builtin_agents_registered(self) -> None:
        assert set(agent_keys()) == {
            "security",
            "quality",
            "performance",
            "architecture",
            "standards",
        }

    def test_each_agent_has_prompt_spec(self) -> None:
        for key in agent_keys():
            assert key in PROMPT_SPECS

    def test_queue_names_match_queue_spec(self) -> None:
        assert queue_for_agent("security") == "security_queue"
        assert queue_for_agent("quality") == "quality_queue"
        assert queue_for_agent("performance") == "performance_queue"
        assert queue_for_agent("architecture") == "architecture_queue"
        assert queue_for_agent("standards") == "standards_queue"

    def test_metadata_is_seedable(self) -> None:
        rows = agent_metadata()
        assert {row["key"] for row in rows} == set(agent_keys())
        assert all(row["name"] and row["version"] for row in rows)

    def test_unknown_agent_rejected(self) -> None:
        with pytest.raises(KeyError):
            get_agent("nonexistent", MockLLMProvider())

    def test_agents_are_distinct_classes(self) -> None:
        instances = {key: get_agent(key, MockLLMProvider()) for key in agent_keys()}
        assert isinstance(instances["security"], BaseReviewAgent)
        assert len({type(instance) for instance in instances.values()}) == 5


class TestAgentRun:
    async def test_valid_batch_returns_structured_findings(
        self, sample_scope, finding_data
    ) -> None:
        provider = MockLLMProvider([_batch_content(finding_data)])
        agent = get_agent("security", provider)
        result = await agent.run(sample_scope)

        assert result.agent_key == "security"
        assert len(result.findings) == 1
        assert result.findings[0].file_path == "app/auth.py"
        assert result.findings[0].severity.value == "HIGH"
        assert result.stats.findings_raw == 1
        assert result.stats.findings_valid == 1
        assert result.stats.failed_results == 0
        assert result.stats.agent_key == "security"
        assert result.stats.duration_ms >= 0
        assert result.stats.cost_usd >= 0
        assert "untrusted-review-input" in provider.calls[0]["user_prompt"]

    async def test_non_json_triggers_one_repair_then_succeeds(
        self, sample_scope, finding_data
    ) -> None:
        provider = MockLLMProvider(
            [
                "not json at all",
                _batch_content(finding_data),
            ]
        )
        agent = get_agent("security", provider)
        result = await agent.run(sample_scope)
        assert result.stats.repaired is True
        assert len(result.findings) == 1
        assert len(provider.calls) == 2

    async def test_repair_exhausted_raises(self, sample_scope) -> None:
        provider = MockLLMProvider(["bad output", "still bad output"])
        agent = get_agent("quality", provider)
        with pytest.raises(AgentOutputError):
            await agent.run(sample_scope)
        assert len(provider.calls) == 2

    async def test_provider_error_propagates_not_retried_by_agent(
        self, sample_scope
    ) -> None:
        provider = MockLLMProvider([LLMError("config error")])
        agent = get_agent("performance", provider)
        with pytest.raises(LLMError):
            await agent.run(sample_scope)

    async def test_provider_transient_error_propagates(self, sample_scope) -> None:
        provider = MockLLMProvider([LLMRetryableError("timeout 1")])
        agent = get_agent("standards", provider)
        with pytest.raises(LLMRetryableError):
            await agent.run(sample_scope)

    async def test_invalid_item_counted_not_fatal(
        self, sample_scope, finding_data
    ) -> None:
        good = dict(finding_data)
        bad = dict(finding_data, category="security/not-a-real-category")
        provider = MockLLMProvider([_batch_content(good, bad)])
        agent = get_agent("security", provider)
        result = await agent.run(sample_scope)
        assert len(result.findings) == 1
        assert result.stats.findings_raw == 2
        assert result.stats.failed_results == 1

    async def test_scope_without_changed_files_rejected(self, sample_scope) -> None:
        from app.agents.contract import AgentScope

        empty = AgentScope(
            review_id=sample_scope.review_id,
            repository_id=sample_scope.repository_id,
            pr_number=1,
            pr_title="t",
            pr_description="",
            base_ref="main",
            head_ref="feat",
            changed_files=(),
        )
        agent = get_agent("security", MockLLMProvider())
        with pytest.raises(AgentScopeConfigurationError):
            await agent.run(empty)

    async def test_runner_returns_result(self, sample_scope, finding_data) -> None:
        provider = MockLLMProvider(
            [_batch_content(dict(finding_data, category="architecture/layering"))]
        )
        result = await run_agent("architecture", sample_scope, provider=provider)
        assert result.agent_key == "architecture"
        assert len(result.findings) == 1


class TestAgentTaskCore:
    async def test_payload_shape(self, sample_scope, finding_data) -> None:
        provider = MockLLMProvider(
            [_batch_content(dict(finding_data, category="performance/query"))]
        )
        payload = await execute_agent_task_core(
            "performance",
            sample_scope.to_dict(),
            provider=provider,
        )
        assert payload["agent_key"] == "performance"
        assert payload["review_id"] == str(sample_scope.review_id)
        assert payload["findings"][0]["category"] == "performance/query"
        assert payload["stats"]["findings_valid"] == 1
        assert payload["stats"]["agent_key"] == "performance"

    async def test_invalid_scope_rejected(self, sample_scope) -> None:
        scope_data = sample_scope.to_dict()
        scope_data["changed_files"] = []
        with pytest.raises(ValueError):
            await execute_agent_task_core(
                "security", scope_data, provider=MockLLMProvider()
            )
