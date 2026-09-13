"""Base agent + execution pipeline (docs/AGENTS.md §2-4).

One execution:
1. Render the versioned system prompt and the delimited, injection-hardened
   user data block from the review scope.
2. Call the (injected) LLM provider once.
3. Strictly parse/validate the structured output. A non-JSON batch triggers ONE
   bounded repair attempt (docs/AGENTS.md §4). Provider errors propagate so the
   queue-level retry/fallback policy owns them.
4. Return an ``AgentResult`` with findings + full accounting for ``agent_metrics``.
"""

from __future__ import annotations

import time
from dataclasses import dataclass
from typing import ClassVar

from app.agents.contract import (
    AgentOutputError,
    AgentResult,
    AgentRunStats,
    AgentScope,
    AgentScopeConfigurationError,
    BatchParseResult,
    parse_batch,
    scope_validation_error,
)
from app.agents.llm import LLMError, LLMProvider, LLMResult
from app.agents.prompts import (
    PROMPT_SPECS,
    PROMPT_VERSION,
    AgentPromptSpec,
    build_system_prompt,
    build_user_prompt,
)


@dataclass(frozen=True)
class _Attempt:
    """One completed provider round-trip and its parse outcome."""

    llm: LLMResult
    parse: BatchParseResult


class BaseReviewAgent:
    """Detects candidate findings for one review dimension. Never decides output."""

    key: ClassVar[str] = ""
    name: ClassVar[str] = ""
    version: ClassVar[str] = "1.0.0"
    queue_name: ClassVar[str] = "review_task_queue"

    def __init__(self, llm: LLMProvider) -> None:
        self.llm = llm

    @property
    def prompt_spec(self) -> AgentPromptSpec:
        """Stateless, versioned prompt content for this agent type."""
        spec = PROMPT_SPECS.get(self.key)
        if spec is None:
            raise AgentScopeConfigurationError(
                f"no prompt spec registered for agent '{self.key}'"
            )
        return spec

    @property
    def prompt_version(self) -> str:
        return PROMPT_VERSION

    async def run(self, scope: AgentScope) -> AgentResult:
        """Execute one detection pass over ``scope``.

        Raises ``AgentScopeConfigurationError`` on a scope that cannot be run,
        propagates provider ``LLMError``, and raises ``AgentOutputError`` if the
        output is still unparseable after the single repair attempt.
        """
        invalid = scope_validation_error(scope)
        if invalid is not None:
            raise AgentScopeConfigurationError(f"{self.key} cannot run: {invalid}")

        started = time.monotonic()
        system_prompt = build_system_prompt(self.prompt_spec)
        user_prompt = build_user_prompt(scope)

        attempt, error = await self._attempt(system_prompt, user_prompt, scope)
        repaired = False
        if error is not None:
            if not isinstance(error, AgentOutputError):
                raise error
            repaired = True
            attempt, error = await self._attempt(system_prompt, user_prompt, scope)
        if error is not None or attempt is None:
            raise error or AgentOutputError("agent produced no usable output")

        duration_ms = int((time.monotonic() - started) * 1000)
        stats = AgentRunStats(
            agent_key=self.key,
            prompt_version=self.prompt_version,
            model=attempt.llm.model,
            provider=attempt.llm.provider,
            tokens_in=attempt.llm.usage.prompt_tokens,
            tokens_out=attempt.llm.usage.completion_tokens,
            cost_usd=attempt.llm.cost_usd,
            findings_raw=attempt.parse.raw_count,
            findings_valid=attempt.parse.valid_count,
            failed_results=attempt.parse.invalid_count,
            duration_ms=duration_ms,
            repaired=repaired,
        )
        return AgentResult(
            agent_key=self.key,
            scope=scope,
            findings=attempt.parse.findings,
            stats=stats,
        )

    async def _attempt(
        self,
        system_prompt: str,
        user_prompt: str,
        scope: AgentScope,
    ) -> tuple[_Attempt | None, Exception | None]:
        try:
            llm_result = await self.llm.complete(
                system_prompt=system_prompt,
                user_prompt=user_prompt,
            )
        except LLMError as exc:
            return None, exc
        try:
            parsed = parse_batch(
                llm_result.content,
                changed_paths=list(scope.changed_paths),
                category_prefixes=self.prompt_spec.categories,
            )
        except AgentOutputError as exc:
            return None, exc
        return _Attempt(llm=llm_result, parse=parsed), None
