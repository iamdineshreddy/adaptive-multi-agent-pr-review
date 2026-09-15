"""Prometheus metrics instrumentation (Phase 14, docs/OBSERVABILITY.md).

All metrics are real: counters are incremented at genuine call sites (webhook
ingestion, feedback recording) and store-derived gauges are pulled from
``OrchestratorStore.metrics_rollup()`` on every exposition. Nothing here is
fabricated.

The exposition endpoint uses the Prometheus text format on a dedicated
``CollectorRegistry`` (never the default registry, so tests stay isolated).

When ``prometheus-client`` is not installed the module still imports and stays
type-clean: every call site becomes a no-op and the endpoint reports an
explicit ``unavailable`` body instead of fake metrics.
"""

from __future__ import annotations

import functools
from collections.abc import Callable
from typing import Any, Protocol

import structlog

from app.config.settings import settings

logger = structlog.get_logger(__name__)


class RollupSource(Protocol):
    """Anything exposing an ORM-agnostic ``metrics_rollup`` (store or a fake)."""

    async def metrics_rollup(self) -> dict[str, Any]: ...


async def render_prometheus_payload(store: RollupSource) -> tuple[str, str]:
    """Refresh store-derived gauges and render the full exposition payload.

    The API router and the worker-process exporter both go through here so the
    two scraped endpoints can never diverge: same rollup -> same gauges -> same
    text format.
    """
    if prometheus_available():
        rollup = await store.metrics_rollup()
        update_store_gauges(rollup)
    return render_metrics()


_PROMETHEUS_CLIENT_PRESENT = True
_PROMETHEUS_UNAVAILABLE: Callable[[], str] | None = None

try:
    from prometheus_client import (
        CONTENT_TYPE_LATEST,
        CollectorRegistry,
        Counter,
        Gauge,
        generate_latest,
    )
except ImportError:  # pragma: no cover - covered when dep is absent
    _PROMETHEUS_CLIENT_PRESENT = False
    CONTENT_TYPE_LATEST = "text/plain; version=0.0.4; charset=utf-8"

    def _unavailable_payload() -> str:
        return (
            "# adaptive_review: prometheus-client not installed; "
            "prometheus exposition unavailable\n"
        )

    _PROMETHEUS_UNAVAILABLE = _unavailable_payload


def prometheus_available() -> bool:
    """True when ``prometheus-client`` is installed and enabled."""
    return _PROMETHEUS_CLIENT_PRESENT and settings.prometheus_enabled


_REGISTRY: CollectorRegistry | None = None
_WEBHOOK_EVENTS: Counter | None = None
_FEEDBACK_RECORDED: Counter | None = None
_REVIEWS_BY_STATUS: Gauge | None = None
_FINDINGS_BY_STATUS: Gauge | None = None
_FEEDBACK_BY_OUTCOME: Gauge | None = None
_REDUNDANCY_RATE: Gauge | None = None
_AGENT_TOKENS: Gauge | None = None
_AGENT_COST_USD: Gauge | None = None
_AGENT_LATENCY: Gauge | None = None


@functools.lru_cache(maxsize=1)
def _registry() -> CollectorRegistry:
    """The application registry, built once (module import keeps it stable)."""
    if not prometheus_available():
        raise RuntimeError("prometheus instrumentation is not available")
    return CollectorRegistry()


def _metrics() -> tuple[
    CollectorRegistry,
    Counter,
    Counter,
    Gauge,
    Gauge,
    Gauge,
    Gauge,
    Gauge,
    Gauge,
    Gauge,
]:
    """Return the cached metric objects, building them on first use."""
    global \
        _REGISTRY, \
        _WEBHOOK_EVENTS, \
        _FEEDBACK_RECORDED, \
        _REVIEWS_BY_STATUS, \
        _FINDINGS_BY_STATUS, \
        _FEEDBACK_BY_OUTCOME, \
        _REDUNDANCY_RATE, \
        _AGENT_TOKENS, \
        _AGENT_COST_USD, \
        _AGENT_LATENCY
    if _REGISTRY is not None and _WEBHOOK_EVENTS is not None:
        assert _FEEDBACK_RECORDED is not None
        assert _REVIEWS_BY_STATUS is not None
        assert _FINDINGS_BY_STATUS is not None
        assert _FEEDBACK_BY_OUTCOME is not None
        assert _REDUNDANCY_RATE is not None
        assert _AGENT_TOKENS is not None
        assert _AGENT_COST_USD is not None
        assert _AGENT_LATENCY is not None
        return (
            _REGISTRY,
            _WEBHOOK_EVENTS,
            _FEEDBACK_RECORDED,
            _REVIEWS_BY_STATUS,
            _FINDINGS_BY_STATUS,
            _FEEDBACK_BY_OUTCOME,
            _REDUNDANCY_RATE,
            _AGENT_TOKENS,
            _AGENT_COST_USD,
            _AGENT_LATENCY,
        )

    registry = _registry()
    _WEBHOOK_EVENTS = Counter(
        "adaptive_review_webhook_events_total",
        "GitHub webhook deliveries processed for pull_request events",
        ("event", "action", "outcome"),
        registry=registry,
    )
    _FEEDBACK_RECORDED = Counter(
        "adaptive_review_feedback_recorded_total",
        "Developer feedback events recorded",
        ("outcome_label",),
        registry=registry,
    )
    _REVIEWS_BY_STATUS = Gauge(
        "adaptive_review_reviews_by_status",
        "Current review count by review status",
        ("status",),
        registry=registry,
    )
    _FINDINGS_BY_STATUS = Gauge(
        "adaptive_review_findings_by_status",
        "Current finding count by publication status",
        ("status",),
        registry=registry,
    )
    _FEEDBACK_BY_OUTCOME = Gauge(
        "adaptive_review_feedback_by_outcome",
        "Feedback events by outcome label",
        ("outcome",),
        registry=registry,
    )
    _REDUNDANCY_RATE = Gauge(
        "adaptive_review_redundancy_rate",
        "Fraction of findings the consolidation layer considered redundant",
        registry=registry,
    )
    _AGENT_TOKENS = Gauge(
        "adaptive_review_agent_tokens_total",
        "Total LLM tokens consumed across agent executions",
        registry=registry,
    )
    _AGENT_COST_USD = Gauge(
        "adaptive_review_agent_cost_usd_total",
        "Total estimated LLM cost (USD) across agent executions",
        registry=registry,
    )
    _AGENT_LATENCY = Gauge(
        "adaptive_review_agent_latency_seconds",
        "Per-agent execution latency in seconds",
        ("role",),
        registry=registry,
    )
    _REGISTRY = registry
    return (
        _REGISTRY,
        _WEBHOOK_EVENTS,
        _FEEDBACK_RECORDED,
        _REVIEWS_BY_STATUS,
        _FINDINGS_BY_STATUS,
        _FEEDBACK_BY_OUTCOME,
        _REDUNDANCY_RATE,
        _AGENT_TOKENS,
        _AGENT_COST_USD,
        _AGENT_LATENCY,
    )


def record_webhook_event(event: str, action: str, outcome: str) -> None:
    """Count a processed ``pull_request`` webhook delivery."""
    if not prometheus_available():
        return
    _metrics()[1].labels(event=event, action=action, outcome=outcome).inc()


def record_feedback(outcome_label: str) -> None:
    """Count a recorded feedback event by its ARUM outcome label."""
    if not prometheus_available():
        return
    _metrics()[2].labels(outcome_label=outcome_label).inc()


def update_store_gauges(rollup: dict[str, Any]) -> None:
    """Mirror the dashboard metrics rollup onto gauges for exposition."""
    if not prometheus_available():
        return
    (
        _registry_obj,
        _webhook,
        _feedback,
        reviews_gauge,
        findings_gauge,
        feedback_gauge,
        redundancy,
        tokens,
        cost,
        latency,
    ) = _metrics()
    reviews = rollup.get("reviews_by_status") or {}
    findings = rollup.get("findings_by_status") or {}
    fb_outcomes = rollup.get("feedback_by_outcome") or {}
    agent = rollup.get("agent_metrics") or {}

    reviews_gauge.clear()
    findings_gauge.clear()
    feedback_gauge.clear()
    latency.clear()
    for st, count in reviews.items():
        reviews_gauge.labels(status=str(st)).set(_to_number(count))
    for st, count in findings.items():
        findings_gauge.labels(status=str(st)).set(_to_number(count))
    for outcome, count in fb_outcomes.items():
        feedback_gauge.labels(outcome=str(outcome)).set(_to_number(count))
    redundancy.set(_to_number(rollup.get("redundancy_rate")))
    tokens_in = _to_number(agent.get("tokens_in"))
    tokens_out = _to_number(agent.get("tokens_out"))
    tokens.set(tokens_in + tokens_out)
    cost.set(_to_number(agent.get("cost_usd")))
    latency_map = {
        "avg": _to_number(agent.get("latency_avg_ms")) / 1000.0,
        "max": _to_number(agent.get("latency_max_ms")) / 1000.0,
    }
    for role, value in latency_map.items():
        latency.labels(role=str(role)).set(value)


def _to_number(value: Any) -> float:
    if isinstance(value, bool):
        return 1.0 if value else 0.0
    if isinstance(value, (int, float)):
        return float(value)
    if isinstance(value, str):
        try:
            return float(value)
        except ValueError:
            return 0.0
    return 0.0


def render_metrics() -> tuple[str, str]:
    """Exposition payload in Prometheus text format plus its content type.

    Returns ``("", ...)``-ish explicit content when instrumentation is off so the
    endpoint never pretends metrics exist where they cannot.
    """
    if not prometheus_available():
        if _PROMETHEUS_UNAVAILABLE is not None:
            return _PROMETHEUS_UNAVAILABLE(), CONTENT_TYPE_LATEST
        return (
            "# adaptive_review: prometheus instrumentation disabled\n",
            CONTENT_TYPE_LATEST,
        )
    from prometheus_client import CONTENT_TYPE_LATEST as _CTL

    return generate_latest(_registry()).decode(), _CTL
