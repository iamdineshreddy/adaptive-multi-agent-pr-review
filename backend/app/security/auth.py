"""FastAPI auth/authz dependencies (Phase 15, docs/SECURITY.md §4).

``get_principal`` is the single gate on the admin dashboard: it parses the
``Authorization: Bearer <token>`` header, resolves the token through a
:class:`BearerTokenProvider`, and 401s on any miss. Role gates (``require_role``)
return 403 for principals below the required rank. Repository-scoped tokens can
be enforced per-request with ``require_repo_access``.

The provider comes from a module-global that tests override; production uses
``SettingsTokenProvider`` (``ADAPTIVE_API_TOKEN_HASHES`` +
``ADAPTIVE_API_TOKEN_ROLES`` + ``ADAPTIVE_API_TOKEN_SCOPES``).
"""

from __future__ import annotations

from typing import Annotated, Any

from fastapi import Depends, Header, HTTPException, status

from app.config.settings import get_settings
from app.security.tokens import (
    DEFAULT_ROLE,
    TokenPrincipal,
    constant_time_equals,
    hash_token,
    role_rank,
)
from app.security.tokens import BearerTokenProvider as _BearerTokenProvider

_provider: _BearerTokenProvider | None = None


class SettingsTokenProvider:
    """Resolves tokens from operator settings (digests only, constant-time).

    Reads settings on every lookup so provisioned tokens take effect without a
    restart (matching how the webhook secret and rate limits are read).
    """

    async def lookup(self, token: str) -> TokenPrincipal | None:
        settings = get_settings()
        digest = hash_token(token)
        for stored in settings.api_token_hashes:
            if constant_time_equals(stored, digest):
                roles = settings.api_token_roles or {}
                scopes = settings.api_token_scopes or {}
                repos = scopes.get(digest)
                return TokenPrincipal(
                    token_hash=digest,
                    role=roles.get(digest, DEFAULT_ROLE),
                    repos=frozenset(repos) if repos else None,
                )
        return None


def get_token_provider() -> _BearerTokenProvider:
    global _provider
    if _provider is None:
        _provider = SettingsTokenProvider()
    return _provider


def _unauthorized(code: str, message: str) -> HTTPException:
    return HTTPException(
        status_code=status.HTTP_401_UNAUTHORIZED,
        detail={"code": code, "message": message},
        headers={"WWW-Authenticate": "Bearer"},
    )


def _forbidden(code: str, message: str) -> HTTPException:
    return HTTPException(
        status_code=status.HTTP_403_FORBIDDEN,
        detail={"code": code, "message": message},
    )


async def get_principal(
    authorization: Annotated[str | None, Header()] = None,
    provider: Annotated[_BearerTokenProvider, Depends(get_token_provider)] = None,  # type: ignore[assignment]
) -> TokenPrincipal:
    """Resolve the caller from the ``Authorization: Bearer`` header or 401."""
    if not authorization or not authorization.lower().startswith("bearer "):
        raise _unauthorized("missing_bearer_token", "Bearer token required")
    token = authorization.split(" ", 1)[1].strip()
    if not token:
        raise _unauthorized("missing_bearer_token", "Bearer token required")
    principal = await provider.lookup(token)
    if principal is None:
        raise _unauthorized("invalid_token", "Unknown or deactivated API token")
    return principal


def require_role(minimum: str) -> Any:
    """Factory for a route dependency that enforces a minimum role rank."""

    async def _require(
        principal: Annotated[TokenPrincipal, Depends(get_principal)],
    ) -> TokenPrincipal:
        if role_rank(principal.role) < role_rank(minimum):
            raise _forbidden(
                "insufficient_permissions",
                f"requires role '{minimum}' (principal has '{principal.role}')",
            )
        return principal

    return _require


def require_repo_access(repository_id: object, principal: TokenPrincipal) -> None:
    """Raise 403 when a repo-scoped principal cannot read ``repository_id``."""
    if not principal.can_access(repository_id):
        raise _forbidden(
            "repository_out_of_scope",
            "token is scoped and does not include this repository",
        )
