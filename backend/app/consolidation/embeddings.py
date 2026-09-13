"""Embedding provider abstraction for redundancy detection and future RAG
(docs/ARCHITECTURE.md component G, M).

Follows the same discipline as ``app.agents.llm``: providers are injected
(never built from module state), real SDKs are only touched lazily when a key
is present, and everything else runs offline/deterministically. The default
``mock`` provider is deterministic so redundancy tests are reproducible
(docs/EXPERIMENTS.md §5).
"""

from __future__ import annotations

import hashlib
from collections.abc import Sequence
from typing import Protocol

from app.config.settings import Settings

EMBEDDING_DIM = 1536  # fixed by the ``embeddings.vector`` vector(1536) column
_MASK_64 = (1 << 64) - 1


class EmbeddingsError(Exception):
    """A provider call could not produce vectors."""


class EmbeddingsConfigurationError(ValueError):
    """The configured provider/model/key combination cannot be built."""


class EmbeddingsProvider(Protocol):
    """Boundary the redundancy layer calls to embed finding texts."""

    name: str
    model: str

    async def embed_texts(self, texts: Sequence[str]) -> list[tuple[float, ...]]: ...


class MockEmbeddingsProvider:
    """Deterministic, offline embeddings for tests and local dev.

    Vectors are derived from the SHA-256 of the text via splitmix64 and
    L2-normalised, so identical texts produce the identical unit vector
    (cosine 1.0) while different texts are distinct but stable across runs.
    """

    name = "mock/unit"
    model = "mock/text-embedding"

    def __init__(self, dim: int = EMBEDDING_DIM) -> None:
        self._dim = dim

    async def embed_texts(self, texts: Sequence[str]) -> list[tuple[float, ...]]:
        return [self._vector(text) for text in texts]

    def _vector(self, text: str) -> tuple[float, ...]:
        return _seeded_unit_vector(text, self._dim)


class OpenAIEmbeddingsProvider:
    """OpenAI embeddings provider (``text-embedding-3-small`` family)."""

    def __init__(
        self,
        api_key: str,
        model: str,
        *,
        timeout_seconds: float = 120.0,
    ) -> None:
        if not api_key:
            raise EmbeddingsConfigurationError("OpenAI provider requires an API key")
        self.model = model
        self.name = f"openai/{model}"
        from openai import AsyncOpenAI

        self._client = AsyncOpenAI(api_key=api_key, timeout=timeout_seconds)

    async def embed_texts(self, texts: Sequence[str]) -> list[tuple[float, ...]]:
        from openai import (
            APIConnectionError,
            APITimeoutError,
            AuthenticationError,
            BadRequestError,
            InternalServerError,
            RateLimitError,
        )

        try:
            response = await self._client.embeddings.create(
                model=self.model, input=list(texts)
            )
        except (
            APIConnectionError,
            APITimeoutError,
            InternalServerError,
            RateLimitError,
            AuthenticationError,
            BadRequestError,
        ) as exc:
            raise EmbeddingsError(f"openai embeddings request failed: {exc}") from exc
        return [tuple(item.embedding) for item in response.data]

    async def aclose(self) -> None:
        await self._client.close()


def build_embeddings_provider(settings: Settings) -> EmbeddingsProvider:
    """Build an embeddings provider from application settings.

    ``embeddings_provider = "mock"`` (default) is offline and deterministic;
    ``"openai"`` requires an API key and is only exercised for real at runtime.
    """
    provider_name = (settings.embeddings_provider or "mock").lower()
    if provider_name == "mock":
        return MockEmbeddingsProvider()
    if provider_name == "openai":
        return OpenAIEmbeddingsProvider(
            settings.openai_api_key,
            model=settings.llm_embedding_model,
            timeout_seconds=settings.llm_timeout_seconds,
        )
    raise EmbeddingsConfigurationError(
        f"unsupported embeddings_provider '{provider_name}'; "
        "expected 'mock' or 'openai'"
    )


def _seeded_unit_vector(text: str, dim: int) -> tuple[float, ...]:
    seed = int.from_bytes(hashlib.sha256(text.encode("utf-8")).digest()[:16], "big")
    state = seed

    def next_u64() -> int:
        nonlocal state
        state = (state + 0x9E3779B97F4A7C15) & _MASK_64
        z = state
        z = (z ^ (z >> 30)) * 0xBF58476D1CE4E5B9 & _MASK_64
        z = (z ^ (z >> 27)) * 0x94D049BB133111EB & _MASK_64
        return z ^ (z >> 31)

    values = [float(next_u64() % _MASK_64) / float(_MASK_64) for _ in range(dim)]
    normaliser = sum(v * v for v in values) ** 0.5
    if normaliser == 0.0:
        return (0.0,) * dim
    return tuple(v / normaliser for v in values)
