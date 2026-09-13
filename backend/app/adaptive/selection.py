"""ARUM review budget + safety gates (docs/ARUM.md §7).

Selection is the hard, deterministic policy evaluated *after* scoring. The
:func:`rank_decisions` order fills a per-review cap (PR risk class -> configured
budget), and three non-negotiable gates can override pure ranking:

- ``critical`` severity -> mandatory publication (unless a human-admin rule
  suppresses it; admin rules are not yet implemented, so critical findings
  always publish);
- high severity AND high confidence -> protected from budget truncation;
- low confidence AND low severity AND high redundancy -> suppressed (the most
  aggressive gate).

Every decision is annotated with the cap it ran under, the gate reason that
fired (or ``None`` for plain budget truncation), and whether it entered the
publication set. The annotations are part of the reproducibility contract
(ARUM.md §10) — nothing is gated silently.
"""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass, replace

from app.adaptive.scoring import Decision, rank_decisions

# Severity feature scores (features.severity_score): INFO 0.05, LOW 0.2,
# MEDIUM 0.5, HIGH 0.8, CRITICAL 1.0. Gate thresholds are expressed on that
# scale so the whole policy lives on feature values.
HIGH_SEVERITY_SCORE = 0.8
LOW_SEVERITY_SCORE = 0.2
CRITICAL_SEVERITY_SCORE = 1.0

GATE_REASON_CRITICAL = "critical_mandatory"
GATE_REASON_PROTECTED = "high_confidence_high_severity"
GATE_REASON_SUPPRESSED = "low_value_high_redundancy"


@dataclass(frozen=True)
class SelectionResult:
    """Deterministic outcome of budget + gate selection over ranked decisions."""

    decisions: tuple[Decision, ...]  # rank order, selection annotated
    cap: int  # budget cap in effect
    selected_count: int  # publication set size (safety gates can exceed cap)
    mandatory_count: int  # critical severity, never truncated
    protected_count: int  # high severity + high confidence, never truncated
    suppressed_by_gate: int  # the low-value / high redundancy gate
    truncated_by_budget: int  # ranked past the cap


def select_decisions(
    decisions: Sequence[Decision],
    *,
    cap: int,
    high_confidence: float = 0.8,
    low_confidence: float = 0.3,
    high_redundancy: float = 0.6,
) -> SelectionResult:
    """Apply safety gates then fill the budget by ARUM rank.

    Gates are evaluated first: mandatory/protected decisions are selected
    regardless of the cap; suppression-gated decisions are excluded regardless
    of the cap. The remaining candidates then fill ``cap`` slots in rank order;
    gated selections count against the cap, so a review heavy on criticals
    publishes more than ``cap`` findings (the gate outranks the budget).
    """
    if cap < 0:
        raise ValueError(f"budget cap must be non-negative, got {cap}")
    for name, value in (
        ("high_confidence", high_confidence),
        ("low_confidence", low_confidence),
        ("high_redundancy", high_redundancy),
    ):
        if not 0.0 <= value <= 1.0:
            raise ValueError(f"{name} threshold must be within [0, 1], got {value}")

    ranked = rank_decisions(decisions)
    annotated: list[Decision] = []
    pending: list[Decision] = []
    mandatory = 0
    protected = 0

    for decision in ranked:
        reason = _gate_reason(
            decision, high_confidence, low_confidence, high_redundancy
        )
        if reason == GATE_REASON_CRITICAL:
            annotated.append(_annotate(decision, cap, reason, selected=True))
            mandatory += 1
        elif reason == GATE_REASON_PROTECTED:
            annotated.append(_annotate(decision, cap, reason, selected=True))
            protected += 1
        elif reason == GATE_REASON_SUPPRESSED:
            annotated.append(_annotate(decision, cap, reason, selected=False))
        else:
            pending.append(decision)

    remaining = max(cap - (mandatory + protected), 0)
    truncated = 0
    selected_count = mandatory + protected
    for decision in pending:
        if remaining > 0:
            annotated.append(_annotate(decision, cap, None, selected=True))
            selected_count += 1
            remaining -= 1
        else:
            truncated += 1
            annotated.append(_annotate(decision, cap, None, selected=False))

    return SelectionResult(
        decisions=tuple(annotated),
        cap=cap,
        selected_count=selected_count,
        mandatory_count=mandatory,
        protected_count=protected,
        suppressed_by_gate=sum(
            1 for d in annotated if d.safety_gate == GATE_REASON_SUPPRESSED
        ),
        truncated_by_budget=truncated,
    )


def _annotate(
    decision: Decision, cap: int, reason: str | None, *, selected: bool
) -> Decision:
    return replace(
        decision,
        budget_cap=cap,
        safety_gate=reason,
        selected=selected,
    )


def _gate_reason(
    decision: Decision,
    high_confidence: float,
    low_confidence: float,
    high_redundancy: float,
) -> str | None:
    features = decision.features
    if features.severity >= CRITICAL_SEVERITY_SCORE:
        return GATE_REASON_CRITICAL
    if (
        features.severity >= HIGH_SEVERITY_SCORE
        and features.confidence >= high_confidence
    ):
        return GATE_REASON_PROTECTED
    if (
        features.severity <= LOW_SEVERITY_SCORE
        and features.confidence <= low_confidence
        and features.redundancy >= high_redundancy
    ):
        return GATE_REASON_SUPPRESSED
    return None
