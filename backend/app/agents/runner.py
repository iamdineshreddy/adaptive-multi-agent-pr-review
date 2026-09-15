"""High-level agent execution entry point.

Phase 6's orchestration fan-out calls ``run_agent`` (or the ``agents.run_agent``
Celery task) for each review task. The scope passed in mirrors ``review_tasks.scope``
(docs/AGENTS.md §3), so a task can be re-run deterministically from its stored row.
"""

from __future__ import annotations

from app.agents.base import BaseReviewAgent
from app.agents.contract import AgentResult, AgentScope
from app.agents.llm import LLMProvider, build_llm_provider
from app.agents.registry import get_agent
from app.config.settings import get_settings


async def run_agent(
    agent_key: str,
    scope: AgentScope,
    *,
    provider: LLMProvider | None = None,
) -> AgentResult:
    """Run one agent over a scope, returning structured findings + accounting."""
    from app.monitoring.langfuse import execution_trace

    llm = provider or build_llm_provider(get_settings())
    agent: BaseReviewAgent = get_agent(agent_key, llm)
    async with execution_trace(agent_key, review_id=str(scope.review_id)) as _trace:
        return await agent.run(scope)
