"""Run one baseline/ablation mode over a corpus (docs/EXPERIMENTS.md §2, §4).

The runner reuses the production adaptive machinery: feature extraction
(``app.adaptive.features``), scoring (``scoring.utility`` / ``rank_decisions``),
weights (``weights`` / ``training.fit_logistic``) and selection
(``selection.select_decisions``). A mode only switches layers it declares — the
code path is otherwise identical to the product's, which is what keeps the
ablation matrix honest. No LLM is invoked: findings come from the labelled
corpus, so the harness measures the selection layer deterministically.
"""

from __future__ import annotations

import hashlib
import json
from collections.abc import Sequence
from dataclasses import dataclass, replace
from typing import Any

from app.adaptive.features import (
    FEATURE_NAMES,
    ArumFeatures,
    agent_agreement,
    confidence_score,
    context_relevance,
    disagreement_variance,
    historical_shares,
    redundancy_term,
    repository_relevance,
    severity_score,
)
from app.adaptive.scoring import Decision, rank_decisions, utility
from app.adaptive.selection import (
    GATE_REASON_SUPPRESSED,
    select_decisions,
)
from app.adaptive.training import (
    build_training_matrix,
    fit_logistic,
    logistic_predict,
    split_train_eval,
)
from app.adaptive.weights import ArumWeights, load_weights
from app.consolidation.datatypes import FindingWrite
from app.consolidation.redundancy import finding_id
from app.experiments.corpus import Corpus, LabeledRow
from app.experiments.metrics import (
    Metrics,
    RunOutcome,
    compute_metrics,
    outcome_total,
)
from app.experiments.modes import ModeConfig

LEARNED_WEIGHTS_VERSION = "v1-learned"
TRAIN_FRACTION = 0.8


@dataclass(frozen=True)
class Candidate:
    """A redundancy-group representative (one selection candidate)."""

    key: str
    representative: LabeledRow
    members: tuple[LabeledRow, ...]
    member_count: int
    distinct_agents: int
    variance: float

    @property
    def review_id(self) -> str:
        return self.representative.review_id


def run_mode(
    corpus: Corpus,
    config: ModeConfig,
    *,
    seed: int = 0,
) -> dict[str, Any]:
    """Execute one mode and return a full, reproducible result payload."""
    candidates = _build_candidates(corpus, config)
    features = {c.key: _features_for(c, config) for c in candidates}

    if config.learned:
        scores, weights, coefficients = _fit_and_score(
            candidates, features, config, seed
        )
        weights_payload: dict[str, Any] = {
            "version": LEARNED_WEIGHTS_VERSION,
            "coefficients": [round(c, 6) for c in coefficients],
        }
    else:
        static = load_weights()
        weights = {c.key: static for c in candidates}
        scores = {c.key: utility(features[c.key], static) for c in candidates}
        weights_payload = {
            "version": static.version,
            "coefficients": None,
        }

    decisions = [
        _decision(c, config, features[c.key], weights[c.key], scores[c.key])
        for c in candidates
    ]
    selected, selection = _select(decisions, config)
    outcome = _outcome(corpus, config.key, candidates, selected)
    metrics = compute_metrics(outcome)
    return _payload(
        corpus, config, seed, weights_payload, selection, metrics, decisions
    )


def _build_candidates(corpus: Corpus, config: ModeConfig) -> tuple[Candidate, ...]:
    rows = [
        row
        for row in corpus.rows
        if _in_scene(row, config.single_reviewer, config.scope)
    ]
    by_group: dict[str, list[LabeledRow]] = {}
    for row in rows:
        key = row.group_key if config.redundancy_filter else row.content_hash
        by_group.setdefault(key, []).append(row)

    candidates: list[Candidate] = []
    for key, members in by_group.items():
        representative = _representative(members)
        distinct_agents = len({m.reviewer for m in members})
        members_in = tuple(members)
        variance = (
            _member_variance(members_in)
            if config.variance and len(members_in) >= 2
            else 0.0
        )
        candidates.append(
            Candidate(
                key=key,
                representative=representative,
                members=members_in,
                member_count=len(members_in),
                distinct_agents=distinct_agents,
                variance=variance,
            )
        )
    return tuple(
        sorted(
            candidates,
            key=lambda c: (c.review_id, c.key),
        )
    )


def _in_scene(row: LabeledRow, single_reviewer: str | None, scope: str) -> bool:
    if not row.included:
        return False
    if single_reviewer is not None and row.reviewer != single_reviewer:
        return False
    return not (scope == "first" and row.round_no > 1)


def _representative(members: Sequence[LabeledRow]) -> LabeledRow:
    return min(
        members,
        key=lambda m: (-severity_score(m.severity), -m.confidence, m.content_hash),
    )


def _member_variance(members: Sequence[LabeledRow]) -> float:
    writes = [_finding_write(m) for m in members]
    return disagreement_variance(writes)


def _features_for(candidate: Candidate, config: ModeConfig) -> ArumFeatures:
    rep = candidate.representative
    memory = rep.memory if config.memory else {}
    shares = historical_shares(memory, rep.category)
    return ArumFeatures(
        severity=severity_score(rep.severity),
        confidence=confidence_score(rep.confidence),
        agent_agreement=agent_agreement(
            candidate.member_count,
            candidate.distinct_agents,
            variance=candidate.variance,
        ),
        historical_actionability=shares["accepted"],
        repository_relevance=repository_relevance(memory),
        context_relevance=context_relevance(memory),
        redundancy=(
            redundancy_term(candidate.member_count) if config.redundancy_filter else 0.0
        ),
        historical_rejection=shares["rejected"],
    )


def _fit_and_score(
    candidates: Sequence[Candidate],
    features: dict[str, ArumFeatures],
    config: ModeConfig,
    seed: int,
) -> tuple[dict[str, float], dict[str, ArumWeights], tuple[float, ...]]:
    records = [
        (features[c.key].as_vector(), 1 if c.representative.is_positive else 0)
        for c in candidates
    ]
    matrix = build_training_matrix(records, headers=FEATURE_NAMES)
    train, _ = split_train_eval(matrix, frac=TRAIN_FRACTION, seed=seed)
    if train.count == 0:
        raise ValueError(
            f"mode {config.key}: learned weights need at least one training "
            "candidate in the corpus"
        )
    coefficients = fit_logistic(train, seed=seed)
    weights = {c.key: _learned_weights(coefficients) for c in candidates}
    scores = {
        c.key: round(logistic_predict(coefficients, features[c.key].as_vector()), 6)
        for c in candidates
    }
    return scores, weights, coefficients


def _learned_weights(coefficients: Sequence[float]) -> ArumWeights:
    """Synthesise an :class:`ArumWeights` that reproduces the logistic score.

    The fitted model is ``s(bias) = bias + sum(coef_i * feature_i)``. The
    production scorer subtracts ``w7``/``w8``, so those two coefficients are
    negated in the weight object — with them, ``scoring.utility`` equals the
    learned score minus the (ranking-irrelevant) constant bias.
    """
    bias, c1, c2, c3, c4, c5, c6, c7, c8 = coefficients
    return ArumWeights(
        version=LEARNED_WEIGHTS_VERSION,
        w1_severity=c1,
        w2_confidence=c2,
        w3_agent_agreement=c3,
        w4_historical_actionability=c4,
        w5_repository_relevance=c5,
        w6_context_relevance=c6,
        w7_redundancy=-c7,
        w8_historical_rejection=-c8,
    )


def _decision(
    candidate: Candidate,
    config: ModeConfig,
    features: ArumFeatures,
    weights: ArumWeights,
    score: float,
) -> Decision:
    rep = candidate.representative
    return Decision(
        review_id=rep.review_id,
        finding_id=_candidate_finding_id(candidate),
        group_id=candidate.key,
        arum_version=weights.version,
        features=features,
        utility=score,
        weights=weights,
        inputs_hash=_inputs_hash(config, features, weights, score),
    )


def _candidate_finding_id(candidate: Candidate) -> str:
    """The production deterministic finding id for a candidate's representative."""
    rep = candidate.representative
    return finding_id(
        review_id=rep.review_id,
        agent_key=rep.reviewer,
        content_hash=rep.content_hash,
        category=rep.category,
        file_path=rep.file_path,
        line_start=rep.line_start,
        line_end=rep.line_end,
        severity=rep.severity,
    )


def _select(
    decisions: Sequence[Decision], config: ModeConfig
) -> tuple[list[Decision], dict[str, int | None]]:
    if not config.adaptive:
        annotated = [_annotate(d, selected=True) for d in decisions]
        return (
            annotated,
            {
                "cap": None,
                "selected_count": len(annotated),
                "mandatory_count": 0,
                "protected_count": 0,
                "suppressed_by_gate": 0,
                "truncated_by_budget": 0,
            },
        )

    cap = config.budget if config.budget is not None else len(decisions)
    if config.gates:
        result = select_decisions(decisions, cap=cap)
        return (
            list(result.decisions),
            {
                "cap": cap,
                "selected_count": result.selected_count,
                "mandatory_count": result.mandatory_count,
                "protected_count": result.protected_count,
                "suppressed_by_gate": sum(
                    1
                    for d in result.decisions
                    if d.selected is False and d.safety_gate == GATE_REASON_SUPPRESSED
                ),
                "truncated_by_budget": result.truncated_by_budget,
            },
        )

    ranked = rank_decisions(decisions)
    published = {d.finding_id for d in ranked[:cap]}
    annotated = [
        _annotate(
            d,
            selected=d.finding_id in published,
            budget_cap=cap if d.finding_id in published else None,
        )
        for d in ranked
    ]
    return (
        annotated,
        {
            "cap": cap,
            "selected_count": len(published),
            "mandatory_count": 0,
            "protected_count": 0,
            "suppressed_by_gate": 0,
            "truncated_by_budget": max(len(ranked) - cap, 0),
        },
    )


def _annotate(
    decision: Decision,
    *,
    selected: bool,
    budget_cap: int | None = None,
) -> Decision:
    return replace(
        decision,
        budget_cap=budget_cap,
        safety_gate=None,
        selected=selected,
    )


def _outcome(
    corpus: Corpus,
    mode_key: str,
    candidates: Sequence[Candidate],
    selected: Sequence[Decision],
) -> RunOutcome:
    selected_ids = {d.finding_id for d in selected}
    published = [c for c in candidates if _candidate_finding_id(c) in selected_ids]
    published_ids = {_candidate_finding_id(c) for c in published}
    not_published = [
        c for c in candidates if _candidate_finding_id(c) not in published_ids
    ]
    return RunOutcome(
        mode_key=mode_key,
        raw_count=len(candidates),
        member_rows=sum(c.member_count for c in candidates),
        group_count=len(candidates),
        published_count=len(published),
        positives=sum(1 for c in candidates if c.representative.is_real),
        published_positives=sum(1 for c in published if c.representative.is_real),
        accepted_published=sum(1 for c in published if c.representative.is_positive),
        non_real=sum(1 for c in candidates if not c.representative.is_real),
        suppressed_non_real=sum(
            1 for c in not_published if not c.representative.is_real
        ),
        actionable_published=sum(1 for c in published if c.representative.actionable),
        actionable_known=sum(
            1 for c in published if c.representative.actionable is not None
        ),
        outcome_total=outcome_total(corpus.label_counts),
    )


def _finding_write(row: LabeledRow) -> FindingWrite:
    return FindingWrite(
        id=row.content_hash,
        agent_key=row.reviewer,
        agent_id=row.reviewer,
        review_id=row.review_id,
        repository_id="corpus",
        file_path=row.file_path,
        line_start=row.line_start,
        line_end=row.line_end,
        category=row.category,
        severity=row.severity,
        confidence=row.confidence,
        title=row.comment[:64],
        description=row.comment,
        evidence={"content_hash": row.content_hash, "group": row.group_key},
        suggested_fix=row.suggested_fix,
        reason_summary=row.comment,
    )


def _inputs_hash(
    config: ModeConfig, features: ArumFeatures, weights: ArumWeights, score: float
) -> str:
    payload = json.dumps(
        [
            features.as_dict(),
            weights.as_dict(),
            score,
            config.scope,
            config.single_reviewer,
        ],
        sort_keys=True,
    )
    return hashlib.sha256(payload.encode()).hexdigest()


def _payload(
    corpus: Corpus,
    config: ModeConfig,
    seed: int,
    weights_payload: dict[str, Any],
    selection: dict[str, int | None],
    metrics: Metrics,
    decisions: Sequence[Decision],
) -> dict[str, Any]:
    return {
        "mode": config.key,
        "label": config.label,
        "seed": seed,
        "honesty": {
            "note": (
                "selection-layer harness over a concrete corpus; agent-layer "
                "latency/token/cost are not reported here"
            ),
            "corpus": corpus.provenance,
            "executed": True,
        },
        "config": {
            "single_reviewer": config.single_reviewer,
            "dedup_only": config.dedup_only,
            "adaptive": config.adaptive,
            "learned": config.learned,
            "memory": config.memory,
            "budget": config.budget,
            "gates": config.gates,
            "scope": config.scope,
            "variance": config.variance,
            "redundancy_filter": config.redundancy_filter,
        },
        "weights": weights_payload,
        "selection": selection,
        "metrics": metrics.to_dict(),
        "candidates": len(decisions),
        "top_utilities": [round(d.utility, 6) for d in rank_decisions(decisions)[:5]],
    }
