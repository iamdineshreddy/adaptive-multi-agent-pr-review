"""Worker-process metrics exporter (Phase 18, docs/OBSERVABILITY.md §Prometheus).

The worker container is scraped on its own port (``infra/prometheus/prometheus.yml``
scrapes ``worker:8001``) so queue health can be observed even when the API is
down. The exporter is a dependency-light FastAPI app that reuses the exact same
auth gate (``get_principal``, bearer-token digest in
``ADAPTIVE_API_TOKEN_HASHES``) and the same rollup -> gauge -> text pipeline as
the API's monitoring endpoint. It never fabricates metrics: endpoint payloads
only ever contain counters incremented at real call sites and gauges projected
from ``metrics_rollup()``.
"""

from __future__ import annotations

from typing import Annotated

from fastapi import Depends, FastAPI, Response

from app.api.dashboard import get_dashboard_store
from app.monitoring import prometheus as metrics
from app.orchestrator.persistence import OrchestratorStore
from app.security.auth import get_principal

worker_app = FastAPI(
    title="adaptive-review worker metrics",
    docs_url=None,
    redoc_url=None,
    openapi_url=None,
)


@worker_app.get(
    "/api/v1/monitoring/prometheus",
    response_class=Response,
    dependencies=[Depends(get_principal)],
)
async def prometheus_metrics(
    store: Annotated[OrchestratorStore, Depends(get_dashboard_store)],
) -> Response:
    """Render counters + store-derived gauges as Prometheus text."""
    body, content_type = await metrics.render_prometheus_payload(store)
    return Response(content=body, media_type=content_type)


@worker_app.get("/health")
async def health() -> dict[str, str]:
    """Liveness probe for the worker container's exporter process."""
    return {"status": "ok", "service": "adaptive-review-worker"}
