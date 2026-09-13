"""Supervisor + LangGraph orchestration (ROADMAP Phase 6).

Topology (docs/AGENTS.md §2): SUPERVISOR_PLAN -> AGENT_FAN_OUT -> RETRY_AGENTS
(later overwritten by the iterative engine in Phase 12). Outcomes are persisted
through :class:`app.orchestrator.persistence.OrchestratorStore`; the DB-free
cores are unit-tested with in-memory store/runner doubles and the live path with
self-skipping integration tests.
"""

from app.orchestrator.graph import build_orchestration_graph
from app.orchestrator.service import ReviewOutcome, run_review

__all__ = ["ReviewOutcome", "build_orchestration_graph", "run_review"]
