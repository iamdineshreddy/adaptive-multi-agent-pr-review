"""API bearer-token auth primitives (Phase 15, docs/SECURITY.md §4).

Design:
- Operators provision random high-entropy tokens (``sk-...``); only their
  SHA-256 digests are stored (``ADAPTIVE_API_TOKEN_HASHES``). A token that is
  never stored in plaintext cannot leak from disk/logs; high-entropy random
  tokens make an unsalted digest equivalent to a salted one, so no secret salt
  is required.
- Each token carries a role (``viewer`` / ``operator`` / ``admin``) and an
  optional repository scope (the resolved-finding ``repository_id`` values it
  may read; ``None`` = all repositories).
- Verification is constant-time; lookups never leak timing about which digest
  matched.
"""

from __future__ import annotations

import hashlib
import hmac
import secrets
from dataclasses import dataclass
from typing import Protocol

ROLE_ORDER: tuple[str, ...] = ("viewer", "operator", "admin")
ROLE_RANK: dict[str, int] = {role: i for i, role in enumerate(ROLE_ORDER)}
DEFAULT_ROLE = "viewer"

TOKEN_PREFIX = "sk-"


def role_rank(role: str | None) -> int:
    """Numeric rank for role ordering; unknown/missing roles rank lowest."""
    if role is None:
        return -1
    return ROLE_RANK.get(role, -1)


def hash_token(token: str) -> str:
    """SHA-256 digest of a bearer token (plaintext is never persisted)."""
    return hashlib.sha256(token.encode("utf-8")).hexdigest()


def constant_time_equals(left: str, right: str) -> bool:
    """Constant-time string comparison for digests."""
    return hmac.compare_digest(left.encode("utf-8"), right.encode("utf-8"))


@dataclass(frozen=True)
class TokenPrincipal:
    """Identity resolved from a presented bearer token."""

    token_hash: str
    role: str = DEFAULT_ROLE
    label: str = ""
    repos: frozenset[str] | None = None  # None = all repositories

    @property
    def is_admin(self) -> bool:
        return role_rank(self.role) >= role_rank("admin")

    def can_access(self, repository_id: object) -> bool:
        """True when the principal may read ``repository_id`` (or is unscoped)."""
        if self.repos is None:
            return True
        return str(repository_id) in self.repos


class BearerTokenProvider(Protocol):
    """Resolves a raw bearer token to a principal; ``None`` when unknown."""

    async def lookup(self, token: str) -> TokenPrincipal | None: ...


def issue_token(
    *,
    label: str,
    role: str = DEFAULT_ROLE,
    repos: list[str] | None = None,
) -> tuple[TokenPrincipal, str]:
    """Create a token + its principal.

    Returns ``(principal, raw_token)``. The raw token is shown to the operator
    exactly once; only ``principal.token_hash`` should be stored.
    """
    if role not in ROLE_RANK:
        raise ValueError(f"unknown role {role!r}; expected one of {ROLE_ORDER}")
    raw = TOKEN_PREFIX + secrets.token_urlsafe(32)
    principal = TokenPrincipal(
        token_hash=hash_token(raw),
        role=role,
        label=label,
        repos=frozenset(repos) if repos is not None else None,
    )
    return principal, raw
