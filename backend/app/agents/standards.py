"""Standards review agent (docs/AGENTS.md §1)."""

from __future__ import annotations

from app.agents.base import BaseReviewAgent
from app.agents.registry import register_agent


@register_agent
class StandardsAgent(BaseReviewAgent):
    """Repository-convention compliance detection."""

    key = "standards"
    name = "Standards Compliance Agent"
    version = "1.0.0"
    queue_name = "standards_queue"
