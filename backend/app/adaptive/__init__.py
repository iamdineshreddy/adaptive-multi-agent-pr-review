"""ARUM: the adaptive decision layer (docs/ARUM.md).

Scored, deterministic finding selection: feature extraction → weighted utility →
ranking → review budget + safety gates, plus the offline logistic/LightGBM
training path and the reproducibility log. Phase 10 wires repository memory +
RAG: decayed per-category accept/reject shares (``memory.py``) and
pgvector retrieval of coded standards / resolved findings (``rag.py``) feed
``historical_actionability`` / ``historical_rejection`` / ``repository_relevance`` /
``context_relevance``.
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
from app.adaptive.feedback import (
    ACTIONABLE_OUTCOMES,
    REJECTIVE_OUTCOMES,
    FeedbackWrite,
    attach_code_change_evidence,
    code_change_signal,
    feedback_identity,
    outcome_label,
)
from app.adaptive.learning import (
    feedback_labels,
    labelled_matrix,
    learn_candidate,
)
from app.adaptive.memory import (
    FeedbackEvent,
    aggregate_category_shares,
    build_memory_snapshot,
)
from app.adaptive.rag import (
    EmbeddingRow,
    RagHit,
    max_relevance,
    top_k_similar,
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
    "ACTIONABLE_OUTCOMES",
    "ArumConfigurationError",
    "ArumFeatures",
    "ArumWeights",
    "DEFAULT_WEIGHTS_VERSION",
    "Decision",
    "DecisionTrace",
    "EmbeddingRow",
    "FEATURE_NAMES",
    "FeedbackEvent",
    "FeedbackWrite",
    "FileDecisionTrace",
    "MemoryDecisionTrace",
    "MlExtraUnavailableError",
    "REJECTIVE_OUTCOMES",
    "RagHit",
    "SelectionResult",
    "TrainingMatrix",
    "agent_agreement",
    "aggregate_category_shares",
    "attach_code_change_evidence",
    "build_memory_snapshot",
    "build_training_matrix",
    "code_change_signal",
    "compute_inputs_hash",
    "confidence_score",
    "decay_weight",
    "disagreement_variance",
    "evaluate",
    "extract_features",
    "feedback_identity",
    "feedback_labels",
    "fit_lightgbm",
    "fit_logistic",
    "historical_shares",
    "historical_terms",
    "labelled_matrix",
    "learn_candidate",
    "lightgbm_predict",
    "load_weights",
    "logistic_predict",
    "max_relevance",
    "outcome_label",
    "rank_decisions",
    "redundancy_term",
    "repository_relevance",
    "select_decisions",
    "severity_score",
    "split_train_eval",
    "top_k_similar",
    "utility",
    "weighted_share",
]
