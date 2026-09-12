"""API router registry.

Endpoints are added per roadmap phase. Only implemented endpoints are registered;
unimplemented endpoints are NOT stubbed here (no fake APIs).
"""

from __future__ import annotations

from fastapi import APIRouter

from app.webhooks.router import router as webhook_router

api_router = APIRouter()
api_router.include_router(webhook_router)
