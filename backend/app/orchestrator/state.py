"""State shape for the LangGraph orchestration state machine (docs/AGENTS.md §2).

The state is deliberately plain JSON-safe values (strings, dicts, lists): the
graph result is persisted to PostgreSQL and re-runnable from a stored scope
without re-polling any LLM. Nodes are pure functions of this state plus the
injected runner/provider boundaries; all side effects live in
``app.orchestrator.service``.
"""

from __future__ import annotations

from typing import Any, TypedDict


class OrchestrationState(TypedDict):
    """One review's orchestration progress through the LangGraph state machine."""

    review_id: str
    repository_id: str
    scope: dict[str, Any]  # serialisable AgentScope (review_tasks.scope)
    plan: dict[str, Any]  # TaskPlan.to_dict() from supervisor planning
    pending: list[str]  # agents flagged retryable by the last fan-out
    results: list[dict[str, Any]]  # success payloads {agent_key, findings, stats}
    retries: dict[str, int]  # agent_key -> number of retries already spent
    retry_history: list[str]  # observability trail, e.g. "security retry 1/2"
    failed: list[dict[str, Any]]  # permanent failures {agent_key, error, attempts}
    notes: list[str]  # supervisor notes (persisted, AGENTS.md §2)
    root_cause: str | None  # diagnosed cause for the FAILED terminal
    terminal: str | None  # "COMPLETE" | "FAILED"


# Node names (stable identifiers; also the transition labels in logs).
NODE_PLAN = "SUPERVISOR_PLAN"
NODE_FAN_OUT = "AGENT_FAN_OUT"
NODE_RETRY = "RETRY_AGENTS"
TERMINAL_COMPLETE = "COMPLETE"
TERMINAL_FAILED = "FAILED"

_TERMINAL_VALUES = (TERMINAL_COMPLETE, TERMINAL_FAILED)


def empty_state(
    review_id: str, repository_id: str, scope: dict[str, Any]
) -> OrchestrationState:
    """A fresh state before the supervisor plans the fan-out."""
    return {
        "review_id": review_id,
        "repository_id": repository_id,
        "scope": scope,
        "plan": {},
        "pending": [],
        "results": [],
        "retries": {},
        "retry_history": [],
        "failed": [],
        "notes": [],
        "root_cause": None,
        "terminal": None,
    }


def is_terminal(value: object) -> bool:
    """True when ``value`` is a diagnostic terminal label."""
    return value in _TERMINAL_VALUES
