"""Unit tests for the embeddings provider abstraction (Phase 7).

The ``mock`` provider must be deterministic, offline, exactly ``EMBEDDING_DIM``
dimensional and unit-normed so cosine grouping is meaningful and reproducible.
The OpenAI provider is only *constructed* here (no calls; no API key required
to hit the config branch).
"""

from __future__ import annotations

import pytest

from app.config.settings import Settings
from app.consolidation.embeddings import (
    EMBEDDING_DIM,
    EmbeddingsConfigurationError,
    MockEmbeddingsProvider,
    OpenAIEmbeddingsProvider,
    build_embeddings_provider,
)
from app.consolidation.similarity import cosine_similarity


async def test_mock_embeddings_are_deterministic() -> None:
    provider = MockEmbeddingsProvider()
    once = await provider.embed_texts(["alpha finding"])
    twice = await provider.embed_texts(["alpha finding"])
    assert once == twice


async def test_mock_embeddings_dimension_and_single_norm() -> None:
    provider = MockEmbeddingsProvider()
    (vector,) = await provider.embed_texts(["a finding"])
    assert len(vector) == EMBEDDING_DIM
    import math

    norm = sum(v * v for v in vector) ** 0.5
    assert math.isclose(norm, 1.0, abs_tol=1e-6)


async def test_mock_identical_texts_cosine_one() -> None:
    provider = MockEmbeddingsProvider()
    a, b = await provider.embed_texts(["same", "same"])
    assert cosine_similarity(a, b) == pytest.approx(1.0)


async def test_mock_distinct_texts_are_distinguishable() -> None:
    provider = MockEmbeddingsProvider()
    a, b = await provider.embed_texts(["cat xss", "quality naming"])
    assert cosine_similarity(a, b) < 1.0


async def test_mock_is_deterministic_across_instances() -> None:
    a = await MockEmbeddingsProvider().embed_texts(["stable"])
    b = await MockEmbeddingsProvider().embed_texts(["stable"])
    assert a == b


def test_build_provider_defaults_to_mock() -> None:
    provider = build_embeddings_provider(Settings(embeddings_provider="mock"))
    assert isinstance(provider, MockEmbeddingsProvider)


def test_build_provider_openai_requires_key() -> None:
    with pytest.raises(EmbeddingsConfigurationError):
        build_embeddings_provider(
            Settings(embeddings_provider="openai", openai_api_key="")
        )


def test_build_provider_unsupported_name_raises() -> None:
    with pytest.raises(EmbeddingsConfigurationError):
        build_embeddings_provider(Settings(embeddings_provider="bogus"))


def test_openai_provider_constructed_with_key() -> None:
    provider = OpenAIEmbeddingsProvider("sk-test", model="text-embedding-3-small")
    assert provider.name == "openai/text-embedding-3-small"
    assert provider.model == "text-embedding-3-small"
    assert callable(provider.embed_texts)


def test_openai_provider_without_key_raises() -> None:
    with pytest.raises(EmbeddingsConfigurationError):
        OpenAIEmbeddingsProvider("", model="text-embedding-3-small")
