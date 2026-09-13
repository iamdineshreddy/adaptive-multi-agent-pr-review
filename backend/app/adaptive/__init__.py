"""ARUM: the adaptive decision layer (docs/ARUM.md).

Scored, deterministic finding selection: feature extraction → weighted utility →
ranking → review budget + safety gates, plus the offline logistic/LightGBM
training path and the reproducibility log. RAG-driven relevance and feedback
memory feed ``repository_relevance`` / ``context_relevance`` / historical shares
in Phases 10–12.
"""

from __future__ import annotations

from app.adaptive.decay import decay_weight, weighted_share
from app.adaptive.features import (
    FEATURE_NAMES,
    ArumFeatures,
    agent_agreement,
    confidence_score,
    disagreement_variance,
    extract_features,
    historical_shares,
    historical_terms,
    redundancy_term,
    repository_relevance,
    severity_score,
)
from app.adaptive.reprolog import (
    DecisionTrace,
    FileDecisionTrace,
    MemoryDecisionTrace,
    compute_inputs_hash,
)
from app.adaptive.scoring import Decision, rank_decisions, utility
from app.adaptive.selection import SelectionResult, select_decisions
from app.adaptive.training import (
    MlExtraUnavailableError,
    TrainingMatrix,
    build_training_matrix,
    evaluate,
    fit_lightgbm,
    fit_logistic,
    lightgbm_predict,
    logistic_predict,
    split_train_eval,
)
from app.adaptive.weights import (
    DEFAULT_WEIGHTS_VERSION,
    ArumConfigurationError,
    ArumWeights,
    load_weights,
)

__all__ = [
    "ArumConfigurationError",
    "ArumFeatures",
    "ArumWeights",
    "DEFAULT_WEIGHTS_VERSION",
    "Decision",
    "DecisionTrace",
    "FEATURE_NAMES",
    "FileDecisionTrace",
    "MemoryDecisionTrace",
    "MlExtraUnavailableError",
    "SelectionResult",
    "TrainingMatrix",
    "agent_agreement",
    "build_training_matrix",
    "compute_inputs_hash",
    "confidence_score",
    "decay_weight",
    "disagreement_variance",
    "evaluate",
    "extract_features",
    "fit_lightgbm",
    "fit_logistic",
    "historical_shares",
    "historical_terms",
    "lightgbm_predict",
    "load_weights",
    "logistic_predict",
    "rank_decisions",
    "redundancy_term",
    "repository_relevance",
    "select_decisions",
    "severity_score",
    "split_train_eval",
    "utility",
    "weighted_share",
]
