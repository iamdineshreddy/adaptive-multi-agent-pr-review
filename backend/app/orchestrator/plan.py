"""Supervisor planning: which agents run for a review (docs/AGENTS.md §1-2).

The supervisor separates planning from execution: it emits a task plan, never
findings. For Phase 6 the default plan runs every registered agent over the full
changed diff; targeted/priority-driven planning (only high-risk files/agents) is
the iterative review engine's responsibility (Phase 12) and is not silently
faked here.
"""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass
from typing import Any

from app.agents.registry import agent_keys, agent_metadata

_FULL_REVIEW_REASON = (
    "full-review: every registered agent over the whole changed diff "
    "(targeted planning arrives with the iterative engine, Phase 12)"
)


@dataclass(frozen=True)
class TaskPlan:
    """The supervisor's decision for one review run."""

    agent_keys: tuple[str, ...]
    reason: str
    agents_invoked: dict[str, str]  # agent_key -> agent version

    def to_dict(self) -> dict[str, Any]:
        return {
            "agent_keys": list(self.agent_keys),
            "reason": self.reason,
            "agents_invoked": dict(self.agents_invoked),
        }

    @classmethod
    def from_dict(cls, data: Mapping[str, Any]) -> TaskPlan:
        invoked = {
            str(k): str(v) for k, v in (data.get("agents_invoked") or {}).items()
        }
        return cls(
            agent_keys=tuple(str(k) for k in (data.get("agent_keys") or [])),
            reason=str(data.get("reason", "")),
            agents_invoked=invoked,
        )


def build_task_plan(keys: tuple[str, ...] | None = None) -> TaskPlan:
    """Build the default plan (or a restricted one from ``keys``).

    Agent versions come from the registration metadata so ``agents_invoked`` is
    reproducible for research logging (docs/EXPERIMENTS.md §5).
    """
    chosen = tuple(keys) if keys is not None else ()
    if not chosen:
        chosen = tuple(agent_keys())
    versions = {row["key"]: row["version"] for row in agent_metadata()}
    return TaskPlan(
        agent_keys=chosen,
        reason=_FULL_REVIEW_REASON,
        agents_invoked={key: versions.get(key, "unknown") for key in chosen},
    )
