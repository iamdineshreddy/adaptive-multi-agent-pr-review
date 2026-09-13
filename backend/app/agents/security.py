"""Security review agent (docs/AGENTS.md §1)."""

from __future__ import annotations

from app.agents.base import BaseReviewAgent
from app.agents.registry import register_agent


@register_agent
class SecurityAgent(BaseReviewAgent):
    """OWASP-class security flaw detection."""

    key = "security"
    name = "Security Review Agent"
    version = "1.0.0"
    queue_name = "security_queue"
