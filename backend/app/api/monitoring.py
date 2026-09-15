"""Observability endpoints (Phase 14).

``GET /api/v1/monitoring/prometheus`` exposes application metrics in the
Prometheus text format. Counters are incremented at real call sites (webhook
ingestion, feedback recording); queue/finding/feedback/agent gauges are pulled
from ``OrchestratorStore.metrics_rollup()`` at each scrape, so the endpoint never
serves fabricated values.
"""

from __future__ import annotations

from typing import Annotated

import structlog
from fastapi import APIRouter, Depends, Response

from app.api.dashboard import get_dashboard_store
from app.monitoring import prometheus as metrics
from app.orchestrator.persistence import OrchestratorStore
from app.security.auth import get_principal

logger = structlog.get_logger(__name__)

router = APIRouter(tags=["monitoring"], dependencies=[Depends(get_principal)])


@router.get(
    "/monitoring/prometheus",
    summary="Prometheus metrics in text exposition format",
    response_class=Response,
)
async def prometheus_metrics(
    store: Annotated[OrchestratorStore, Depends(get_dashboard_store)],
) -> Response:
    """Render current counters + store-derived gauges as Prometheus text."""
    body, content_type = await metrics.render_prometheus_payload(store)
    return Response(content=body, media_type=content_type)
