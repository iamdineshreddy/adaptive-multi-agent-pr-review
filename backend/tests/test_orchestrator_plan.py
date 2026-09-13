"""Unit tests for supervisor planning (ROADMAP Phase 6, docs/AGENTS.md §2)."""

from __future__ import annotations

from app.orchestrator.plan import TaskPlan, build_task_plan
from app.orchestrator.state import NODE_FAN_OUT, NODE_PLAN, NODE_RETRY


def _registered_keys() -> list[str]:
    from app.agents.registry import agent_keys

    return sorted(agent_keys())


class TestTaskPlan:
    def test_default_plan_covers_all_registered_agents(self) -> None:
        plan = build_task_plan()
        assert sorted(plan.agent_keys) == _registered_keys()
        assert plan.reason.startswith("full-review")

    def test_versions_are_captured_for_research_logging(self) -> None:
        plan = build_task_plan(("security",))
        assert plan.agents_invoked["security"] != ""
        assert plan.agents_invoked["security"] != "unknown"

    def test_targeted_plan_respects_provided_keys(self) -> None:
        plan = build_task_plan(("security",))
        assert list(plan.agent_keys) == ["security"]
        assert len(plan.agents_invoked) == 1

    def test_unknown_agent_key_gets_unknown_version_but_runs(self) -> None:
        plan = build_task_plan(("does-not-exist",))
        assert list(plan.agent_keys) == ["does-not-exist"]
        assert plan.agents_invoked["does-not-exist"] == "unknown"

    def test_roundtrip_dict(self) -> None:
        plan = build_task_plan(("quality", "performance"))
        restored = TaskPlan.from_dict(plan.to_dict())
        assert restored.agent_keys == plan.agent_keys
        assert restored.reason == plan.reason
        assert restored.agents_invoked == plan.agents_invoked

    def test_fan_out_node_name_stable_for_routing(self) -> None:
        assert NODE_PLAN == "SUPERVISOR_PLAN"
        assert NODE_FAN_OUT == "AGENT_FAN_OUT"
        assert NODE_RETRY == "RETRY_AGENTS"
