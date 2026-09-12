"""API router registry.

Endpoints are added per roadmap phase. The router exists now so the app shell can
load; unimplemented endpoints are NOT stubbed here (no fake APIs).
"""

from __future__ import annotations

from fastapi import APIRouter

api_router = APIRouter()
