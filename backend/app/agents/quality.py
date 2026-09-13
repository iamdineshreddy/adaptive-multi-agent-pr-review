"""Quality review agent (docs/AGENTS.md §1)."""

from __future__ import annotations

from app.agents.base import BaseReviewAgent
from app.agents.registry import register_agent


@register_agent
class QualityAgent(BaseReviewAgent):
    """Maintainability/readability issue detection."""

    key = "quality"
    name = "Code Quality Review Agent"
    version = "1.0.0"
    queue_name = "quality_queue"
