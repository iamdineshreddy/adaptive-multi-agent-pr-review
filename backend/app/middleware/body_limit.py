"""Request body-size cap (Phase 15 part 2).

Rejects requests whose declared ``Content-Length`` exceeds
``ADAPTIVE_MAX_REQUEST_BODY_BYTES`` with a 413, guarding the public write
endpoints (webhook + feedback) against oversized permutation payloads. The cap
is checked before routing so oversized bodies never reach the parser.
"""

from __future__ import annotations

from starlette.datastructures import Headers
from starlette.middleware.base import BaseHTTPMiddleware, RequestResponseEndpoint
from starlette.requests import Request
from starlette.responses import JSONResponse, Response
from starlette.types import ASGIApp

from app.config.settings import get_settings


class RequestBodyLimitMiddleware(BaseHTTPMiddleware):
    def __init__(self, app: ASGIApp, max_whole_body_bytes: int | None = None) -> None:
        super().__init__(app)
        self._limit = (
            max_whole_body_bytes
            if max_whole_body_bytes is not None
            else get_settings().max_request_body_bytes
        )

    async def dispatch(
        self, request: Request, call_next: RequestResponseEndpoint
    ) -> Response:
        headers: Headers = request.headers
        raw = headers.get("content-length")
        if raw is not None and raw.isdigit() and int(raw) > self._limit:
            return JSONResponse(
                status_code=413,
                content={
                    "detail": {
                        "code": "body_too_large",
                        "message": (
                            f"request body exceeds the {self._limit}-byte limit"
                        ),
                    }
                },
                headers={"Connection": "close"},
            )
        return await call_next(request)
