"""GitHub webhook signature verification (docs/SECURITY.md §2).

GitHub signs deliveries with HMAC-SHA256 (``X-Hub-Signature-256``) and, for legacy
repositories, HMAC-SHA1 (``X-Hub-Signature``). Verification is constant-time.
"""

from __future__ import annotations

import hashlib
import hmac


class SignatureVerifier:
    """Verifies ``X-Hub-Signature-256``/``X-Hub-Signature`` against a secret."""

    def __init__(self, secret: str) -> None:
        self._secret = secret.encode("utf-8")

    @property
    def configured(self) -> bool:
        return bool(self._secret)

    def verify(
        self,
        body: bytes,
        signature_256: str | None,
        signature_sha1: str | None = None,
    ) -> bool:
        """Return True when a signature matches the raw body.

        Prefers the SHA-256 header and falls back to legacy SHA-1.
        """
        if signature_256:
            expected = (
                "sha256="
                + hmac.new(self._secret, body, digestmod=hashlib.sha256).hexdigest()
            )
            return hmac.compare_digest(expected, signature_256.strip())
        if signature_sha1:
            expected = (
                "sha1="
                + hmac.new(self._secret, body, digestmod=hashlib.sha1).hexdigest()
            )
            return hmac.compare_digest(expected, signature_sha1.strip())
        return False
