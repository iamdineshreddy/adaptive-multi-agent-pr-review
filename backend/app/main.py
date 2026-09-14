"""FastAPI application entrypoint.

Phase 1 provides the application shell and a `/health` endpoint. API features are
added in later phases (webhook: Phase 3, dashboard/admin endpoints: Phase 13).
"""

from __future__ import annotations

from fastapi import FastAPI

from app import __version__
from app.api.router import api_router
from app.config.settings import settings


def create_app() -> FastAPI:
    """Build and configure the application."""
    from app.monitoring.logsetup import configure_logging

    configure_logging()

    application = FastAPI(
        title="Adaptive Multi-Agent AI PR Review API",
        version=__version__,
        description=(
            "Asynchronous, queue-driven, multi-agent GitHub Pull Request review "
            "with adaptive finding selection (ARUM)."
        ),
        docs_url="/api/docs" if settings.debug else None,
        redoc_url="/api/redoc" if settings.debug else None,
        openapi_url="/api/openapi.json",
    )
    application.include_router(api_router, prefix="/api/v1")
    return application


app = create_app()


@app.get("/health", tags=["infra"])
async def health() -> dict[str, str]:
    """Liveness probe with environment context."""
    return {"status": "ok", "version": __version__, "environment": settings.env}
