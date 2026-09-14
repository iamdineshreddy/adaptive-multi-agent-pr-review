"""Structured logging configuration (Phase 14, docs/OBSERVABILITY.md).

All application modules log through ``structlog``; this module is the single
place that shapes that output: JSON rendering with timestamps, level-aware
filtering, stack/exception context, and a strict secret-redaction step so no
credential-shaped key's value ever reaches the log stream.

``configure_logging`` is idempotent and wired from ``app.main.create_app``.
"""

from __future__ import annotations

import sys
from typing import Any

import structlog

from app.config.settings import settings

_LOG_LEVELS = {
    "critical": 50,
    "error": 40,
    "warning": 30,
    "info": 20,
    "debug": 10,
}

_SECRET_KEY_MARKERS = (
    "api_key",
    "apikey",
    "secret",
    "token",
    "pat",
    "password",
    "authorization",
    "signature",
    "credential",
    "private_key",
)


def redact_secrets(
    logger: Any, method_name: str, event_dict: dict[str, Any]
) -> dict[str, Any]:
    """Blank values whose key looks like a secret; leaves the key name visible.

    Keeping the key name (rather than dropping the pair) preserves the event's
    shape for log consistency and makes leakage obvious when scanning for the
    ``[REDACTED]`` marker.
    """
    for key in list(event_dict):
        lowered = str(key).lower()
        if any(marker in lowered for marker in _SECRET_KEY_MARKERS):
            event_dict[key] = "[REDACTED]"
    return event_dict


_configured = False


def configure_logging(*, level: str | None = None, json_enabled: bool = True) -> None:
    """Install the shared structlog configuration for this process.

    ``level`` falls back to the ``ADAPTIVE_LOG_LEVEL`` setting. ``json_enabled``
    selects a JSON renderer (default) versus a plain console renderer for
    interactive dev shells.
    """
    global _configured
    config_level = (level or settings.log_level or "info").lower()
    numeric = _LOG_LEVELS.get(config_level, 20)

    processors = [
        structlog.contextvars.merge_contextvars,
        structlog.stdlib.add_log_level,
        structlog.processors.TimeStamper(fmt="iso"),
        structlog.processors.StackInfoRenderer(),
        structlog.processors.format_exc_info,
        redact_secrets,
    ]
    if json_enabled:
        processors.append(structlog.processors.JSONRenderer())
    else:
        processors.append(structlog.dev.ConsoleRenderer())

    structlog.configure(
        processors=processors,  # type: ignore[arg-type]
        wrapper_class=structlog.make_filtering_bound_logger(numeric),
        logger_factory=structlog.PrintLoggerFactory(file=sys.stdout),
        cache_logger_on_first_use=True,
    )
    _configured = True
