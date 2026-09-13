"""ARUM weights: versioned, configurable, repo-overridable policy (docs/ARUM.md §3).

The default values are the documented heuristic prior from §3 ("v1"). They are
*not* presented as optimal; the learning module (``training.py``) recalibrates
them, and every decision record stores the exact weights used.
"""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass
from typing import cast

DEFAULT_WEIGHTS_VERSION = "v1"

_WEIGHT_KEYS = (
    "w1_severity",
    "w2_confidence",
    "w3_agent_agreement",
    "w4_historical_actionability",
    "w5_repository_relevance",
    "w6_context_relevance",
    "w7_redundancy",
    "w8_historical_rejection",
)


class ArumConfigurationError(ValueError):
    """Invalid weight policy (unknown key, non-finite value, etc.)."""


@dataclass(frozen=True)
class ArumWeights:
    """The eight ARUM weights (docs/ARUM.md §3) plus a version label.

    ``w7`` (redundancy) and ``w8`` (historical rejection) are penalty terms,
    regardless of sign — ``scoring.utility`` always subtracts them (ARUM.md §1).
    """

    version: str = DEFAULT_WEIGHTS_VERSION
    w1_severity: float = 0.30
    w2_confidence: float = 0.20
    w3_agent_agreement: float = 0.15
    w4_historical_actionability: float = 0.15
    w5_repository_relevance: float = 0.10
    w6_context_relevance: float = 0.10
    w7_redundancy: float = 0.08
    w8_historical_rejection: float = 0.10

    def as_dict(self) -> dict[str, float | str]:
        """Flat, JSON-serialisable mapping (the persistence/repro log shape)."""
        return {
            "version": self.version,
            "w1_severity": self.w1_severity,
            "w2_confidence": self.w2_confidence,
            "w3_agent_agreement": self.w3_agent_agreement,
            "w4_historical_actionability": self.w4_historical_actionability,
            "w5_repository_relevance": self.w5_repository_relevance,
            "w6_context_relevance": self.w6_context_relevance,
            "w7_redundancy": self.w7_redundancy,
            "w8_historical_rejection": self.w8_historical_rejection,
        }

    def as_vector(self) -> tuple[float, ...]:
        """Ordered payload matching ``features.FEATURE_NAMES`` (training matrix)."""
        return (
            self.w1_severity,
            self.w2_confidence,
            self.w3_agent_agreement,
            self.w4_historical_actionability,
            self.w5_repository_relevance,
            self.w6_context_relevance,
            self.w7_redundancy,
            self.w8_historical_rejection,
        )

    def with_overrides(self, overrides: Mapping[str, float]) -> ArumWeights:
        """Return a copy with per-repository overrides applied.

        Repo overrides come from ``repository_memory.arum_weights``. Unknown
        keys are rejected loudly rather than merged silently (a typo would
        silently change policy otherwise).
        """
        unknown = [k for k in overrides if k not in _WEIGHT_KEYS]
        if unknown:
            raise ArumConfigurationError(
                f"unknown ARUM weight key(s): {', '.join(sorted(unknown))}"
            )
        values = {
            key: float(value) for key, value in overrides.items() if value is not None
        }
        for key, value in values.items():
            if value < 0.0 or value != value or value in (float("inf"), float("-inf")):
                raise ArumConfigurationError(
                    f"ARUM weight {key!r} must be a finite non-negative number"
                )
        current = self.as_dict()
        current.pop("version", None)
        current.update(values)
        return ArumWeights(version=self.version, **cast(dict[str, float], current))


def load_weights(
    *,
    version: str = DEFAULT_WEIGHTS_VERSION,
    repo_overrides: Mapping[str, float] | None = None,
) -> ArumWeights:
    """Build a weight set for the given version label, then apply repo overrides.

    Only ``v1`` exists so far; unknown versions raise so policy drift is never
    silent (weights in the decision log must reproduce exactly).
    """
    if version != DEFAULT_WEIGHTS_VERSION:
        raise ArumConfigurationError(
            f"unknown ARUM weights version {version!r} (available: 'v1')"
        )
    weights = ArumWeights(version=version)
    if repo_overrides:
        return weights.with_overrides(repo_overrides)
    return weights
