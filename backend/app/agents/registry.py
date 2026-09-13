"""Agent registry (docs/AGENTS.md §1-2).

Concrete agent classes self-register via ``@register_agent``; importing this
package or any concrete agent module populates the registry. The registry also
exposes the metadata needed to seed the ``agents`` table (Phase 6).
"""

from __future__ import annotations

from app.agents import prompts
from app.agents.base import BaseReviewAgent
from app.agents.llm import LLMProvider

_AGENT_CLASSES: dict[str, type[BaseReviewAgent]] = {}


def register_agent(cls: type[BaseReviewAgent]) -> type[BaseReviewAgent]:
    """Register an agent class under ``cls.key`` (idempotent)."""
    if not cls.key:
        raise ValueError(f"{cls.__name__} must define a non-empty 'key'")
    _AGENT_CLASSES[cls.key] = cls
    return cls


def agent_keys() -> list[str]:
    """Registered agent keys in a stable (declaration) order."""
    return list(_AGENT_CLASSES)


def get_agent_class(key: str) -> type[BaseReviewAgent]:
    """Resolve an agent class by key."""
    try:
        return _AGENT_CLASSES[key]
    except KeyError as exc:
        raise KeyError(
            f"unknown agent '{key}'; "
            f"registered: {', '.join(_AGENT_CLASSES) or '(none)'}"
        ) from exc


def get_agent(key: str, llm: LLMProvider) -> BaseReviewAgent:
    """Instantiate an agent for a provider."""
    return get_agent_class(key)(llm)


def queue_for_agent(key: str) -> str:
    """The Celery queue an agent's task is routed to."""
    return get_agent_class(key).queue_name


def agent_metadata() -> list[dict[str, str]]:
    """Rows for seeding ``agents`` (Phase 6): key/name/version, active by default."""
    return [
        {
            "key": cls.key,
            "name": cls.name,
            "version": cls.version,
        }
        for cls in _AGENT_CLASSES.values()
    ]


def prompt_versions() -> dict[str, str]:
    """Prompt version per agent key (for reproducibility logging)."""
    return {key: prompts.PROMPT_VERSION for key in _AGENT_CLASSES}
