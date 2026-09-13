"""Performance review agent (docs/AGENTS.md §1)."""

from __future__ import annotations

from app.agents.base import BaseReviewAgent
from app.agents.registry import register_agent


@register_agent
class PerformanceAgent(BaseReviewAgent):
    """Performance-regression detection."""

    key = "performance"
    name = "Performance Review Agent"
    version = "1.0.0"
    queue_name = "performance_queue"
