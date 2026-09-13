"""LangGraph orchestration state machine (docs/AGENTS.md §2).

Graph topology (matches AGENTS.md §2):

    SUPERVISOR_PLAN -> AGENT_FAN_OUT -> RETRY_AGENTS -> AGENT_FAN_OUT   (loop)
                          |-> COMPLETE (all agents done)
                          |-> FAILED   (permanent agent failure)

- ``SUPERVISOR_PLAN`` builds the task plan (which agents run).
- ``AGENT_FAN_OUT`` runs every not-yet-finished planned agent through the
  injected :class:`AgentRunner`. Outcomes are classified: success, retryable
  (transient provider error), or permanent (bad output, scope error, crash,
  hard timeout). A fan-out pass never raises.
- ``RETRY_AGENTS`` bumps the bounded retry counter for pending agents and moves
  exhausted ones to permanent failure with a supervisor note.
- Terminal ``COMPLETE``/``FAILED`` are diagnosed states; FAILED always carries
  the partial findings so consolidation in Phase 7 still sees them.

The graph is pure: all side effects (status transitions, notes, metrics,
review-task rows) happen in ``app.orchestrator.service`` after the final state.
"""

from __future__ import annotations

import asyncio
from collections.abc import Callable, Coroutine
from typing import Any

from langgraph.graph import END, START, StateGraph

from app.agents.contract import AgentScope
from app.agents.registry import agent_metadata
from app.orchestrator.plan import build_task_plan
from app.orchestrator.runners import AgentRunner
from app.orchestrator.state import (
    NODE_FAN_OUT,
    NODE_PLAN,
    NODE_RETRY,
    TERMINAL_COMPLETE,
    TERMINAL_FAILED,
    OrchestrationState,
)

NodeFn = Callable[
    [OrchestrationState],
    Coroutine[Any, Any, dict[str, Any]],
]


def make_plan_node(runner: AgentRunner) -> NodeFn:
    """Supervisor planning: decide which agents run for this review."""

    async def plan(state: OrchestrationState) -> dict[str, Any]:
        keys = await runner.agent_keys()
        plan = build_task_plan(tuple(keys))
        return {"plan": plan.to_dict()}

    return plan


def make_fan_out_node(runner: AgentRunner, agent_timeout_seconds: float) -> NodeFn:
    """Run every unfinished planned agent; classify outcomes and record state."""

    async def fan_out(state: OrchestrationState) -> dict[str, Any]:
        plan_keys = list(state["plan"]["agent_keys"])
        finished = {r["agent_key"] for r in state["results"]}
        finished |= {f["agent_key"] for f in state["failed"]}
        targets = [key for key in plan_keys if key not in finished]

        scope = AgentScope.from_dict(state["scope"])
        results = list(state["results"])
        failed = list(state["failed"])
        retries = dict(state["retries"])
        notes = list(state["notes"])
        pending: list[str] = []

        for key in targets:
            try:
                outcome = await asyncio.wait_for(
                    runner.run(key, scope), timeout=agent_timeout_seconds
                )
            except TimeoutError:
                note = (
                    f"supervisor: {key} exceeded the {agent_timeout_seconds:g}s "
                    "hard timeout; treating as a permanent failure."
                )
                failed.append(
                    {
                        "agent_key": key,
                        "error": (
                            f"exceeded the {agent_timeout_seconds:g}s hard timeout"
                        ),
                        "attempts": retries.get(key, 0) + 1,
                    }
                )
                notes.append(note)
                continue

            if outcome.success:
                results.append(
                    {
                        "agent_key": outcome.agent_key,
                        "findings": list(outcome.findings),
                        "stats": outcome.stats,
                    }
                )
            elif outcome.retryable:
                pending.append(outcome.agent_key)
                notes.append(
                    f"supervisor: {key} hit a transient failure; retry queued."
                )
            else:
                failed.append(
                    {
                        "agent_key": key,
                        "error": outcome.error or "permanent agent failure",
                        "attempts": retries.get(key, 0) + 1,
                    }
                )
                notes.append(
                    f"supervisor: {key} failed permanently: "
                    f"{outcome.error or 'unknown'}"
                )

        return {
            "results": results,
            "failed": failed,
            "retries": retries,
            "pending": pending,
            "notes": notes,
        }

    return fan_out


def make_retry_node(max_retries: int) -> NodeFn:
    """Bounded retry bookkeeping; exhausted agents become permanent failures."""

    async def retry(state: OrchestrationState) -> dict[str, Any]:
        retries = dict(state["retries"])
        retry_history = list(state["retry_history"])
        failed = list(state["failed"])
        notes = list(state["notes"])

        for key in state["pending"]:
            retries[key] = retries.get(key, 0) + 1
            attempt = retries[key]
            retry_history.append(f"{key} retry {attempt}/{max_retries}")
            if attempt >= max_retries:
                failed.append(
                    {
                        "agent_key": key,
                        "error": (
                            f"transient failure exhausted after "
                            f"{attempt} retr{'' if attempt == 1 else 'ies'}"
                        ),
                        "attempts": attempt,
                    }
                )
                notes.append(
                    f"supervisor: {key} permanently failed after {attempt} "
                    "retries; partial findings still flow to consolidation."
                )
            else:
                notes.append(
                    f"supervisor: retrying {key} (attempt {attempt}/{max_retries})."
                )

        return {
            "retries": retries,
            "retry_history": retry_history,
            "failed": failed,
            "notes": notes,
            "pending": [],
        }

    return retry


def route_after_fan_out(state: OrchestrationState) -> str:
    """Choose the next step: another retry round, or a diagnostic terminal."""
    if state["pending"]:
        return NODE_RETRY
    if state["failed"]:
        return TERMINAL_FAILED
    return TERMINAL_COMPLETE


async def complete_node(state: OrchestrationState) -> dict[str, Any]:
    """Diagnosed terminal: every planned agent finished (found findings or none)."""
    notes = list(state["notes"])
    notes.append(
        f"supervisor: all {len(state['results'])} planned agent(s) finished; "
        "ready for consolidation."
    )
    return {"terminal": TERMINAL_COMPLETE, "notes": notes, "root_cause": None}


async def failed_node(state: OrchestrationState) -> dict[str, Any]:
    """Diagnosed terminal: ≥1 agent failed permanently. Partial findings kept."""
    keys = ", ".join(sorted({f["agent_key"] for f in state["failed"]}))
    root_cause = state["root_cause"] or (
        f"{len(state['failed'])} agent(s) permanently failed: {keys}"
    )
    notes = list(state["notes"])
    notes.append(
        f"supervisor: review marked FAILED after permanent agent failure(s) "
        f"({(keys) or 'none'}); partial findings forwarded to consolidation."
    )
    return {
        "terminal": TERMINAL_FAILED,
        "root_cause": root_cause,
        "notes": notes,
    }


def build_orchestration_graph(
    runner: AgentRunner,
    *,
    max_retries: int = 2,
    agent_timeout_seconds: float = 120.0,
) -> Any:
    """Compile the review-orchestration LangGraph for a given runner."""
    if max_retries < 0:
        raise ValueError("max_retries must be >= 0")
    if agent_timeout_seconds <= 0:
        raise ValueError("agent_timeout_seconds must be > 0")

    graph = StateGraph(OrchestrationState)
    graph.add_node(NODE_PLAN, make_plan_node(runner))
    graph.add_node(NODE_FAN_OUT, make_fan_out_node(runner, agent_timeout_seconds))
    graph.add_node(NODE_RETRY, make_retry_node(max_retries))
    graph.add_node(TERMINAL_COMPLETE, complete_node)
    graph.add_node(TERMINAL_FAILED, failed_node)

    graph.add_edge(START, NODE_PLAN)
    graph.add_edge(NODE_PLAN, NODE_FAN_OUT)
    graph.add_conditional_edges(
        NODE_FAN_OUT,
        route_after_fan_out,
        {
            NODE_RETRY: NODE_RETRY,
            TERMINAL_FAILED: TERMINAL_FAILED,
            TERMINAL_COMPLETE: TERMINAL_COMPLETE,
        },
    )
    graph.add_edge(NODE_RETRY, NODE_FAN_OUT)
    graph.add_edge(TERMINAL_COMPLETE, END)
    graph.add_edge(TERMINAL_FAILED, END)
    return graph.compile()


def infer_plan_keys(state: OrchestrationState) -> list[str]:
    """Agent keys from the planned state (used by persistence/bookkeeping)."""
    return list(state["plan"].get("agent_keys") or [])


def infer_agent_versions() -> dict[str, str]:
    """Registered agent key -> version (for reproducible plans)."""
    return {row["key"]: row["version"] for row in agent_metadata()}
