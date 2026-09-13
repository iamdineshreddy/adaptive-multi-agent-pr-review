"""Architecture review agent (docs/AGENTS.md §1)."""

from __future__ import annotations

from app.agents.base import BaseReviewAgent
from app.agents.registry import register_agent


@register_agent
class ArchitectureAgent(BaseReviewAgent):
    """Design/layering/coupling detection."""

    key = "architecture"
    name = "Architecture Review Agent"
    version = "1.0.0"
    queue_name = "architecture_queue"
