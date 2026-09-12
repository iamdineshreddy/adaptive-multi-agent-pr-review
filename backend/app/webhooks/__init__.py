"""GitHub webhook service (Phase 3)."""

from app.webhooks.router import router as webhook_router

__all__ = ["webhook_router"]
