"""Specialized review agents, provider abstraction, and registry (Phase 5).

Importing this package registers the five concrete agents (security, quality,
performance, architecture, standards) with the agent registry.
"""

from __future__ import annotations

from app.agents.architecture import ArchitectureAgent
from app.agents.base import BaseReviewAgent
from app.agents.contract import (
    AgentFinding,
    AgentResult,
    AgentRunStats,
    AgentScope,
    FileSlice,
)
from app.agents.llm import (
    LLMError,
    LLMProvider,
    LLMRetryableError,
    MockLLMProvider,
    build_llm_provider,
)
from app.agents.performance import PerformanceAgent
from app.agents.prompts import PROMPT_SPECS, PROMPT_VERSION, AgentPromptSpec
from app.agents.quality import QualityAgent
from app.agents.registry import (
    agent_keys,
    agent_metadata,
    get_agent,
    queue_for_agent,
)
from app.agents.runner import run_agent
from app.agents.security import SecurityAgent
from app.agents.standards import StandardsAgent

__all__ = [
    "AgentFinding",
    "AgentPromptSpec",
    "AgentResult",
    "AgentRunStats",
    "AgentScope",
    "ArchitectureAgent",
    "BaseReviewAgent",
    "FileSlice",
    "LLMError",
    "LLMProvider",
    "LLMRetryableError",
    "MockLLMProvider",
    "PROMPT_SPECS",
    "PROMPT_VERSION",
    "PerformanceAgent",
    "QualityAgent",
    "SecurityAgent",
    "StandardsAgent",
    "agent_keys",
    "agent_metadata",
    "build_llm_provider",
    "get_agent",
    "queue_for_agent",
    "run_agent",
]
