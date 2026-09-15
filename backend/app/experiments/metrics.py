"""Selection-layer metrics (docs/ARUM.md §5, docs/EXPERIMENTS.md §3).

These are the metrics computable from the labelled corpus + the selection
decision (which findings were published). Agent-layer costs (latency, tokens,
LLM cost) are recorded per run as ``None`` for the selection-layer harness and
are not fabricated. Positive labels are ``ACCEPTED`` / ``FIXED``; "real" issues
include ``MODIFIED`` (partially adopted) per §1.
"""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass


@dataclass(frozen=True)
class RunOutcome:
    """Counts needed by every metric, so definitions are traceable."""

    mode_key: str
    raw_count: int  # candidate findings fed to selection (post-dedup)
    member_rows: int  # raw member rows including duplicates
    group_count: int  # distinct redundancy groups (singletons included)
    published_count: int  # size of the publication set
    positives: int  # real issues in the candidate set (ACCEPTED/FIXED/MODIFIED)
    published_positives: int  # real and published
    accepted_published: int  # published with ACCEPTED/FIXED
    non_real: int  # non-issues in the candidate set
    suppressed_non_real: int  # non-issues suppressed or not published
    actionable_published: int  # published with a root-caused fix
    actionable_known: int  # published where an actionable flag exists
    outcome_total: int  # labelled outcomes in the corpus (acceptance-rate base)


@dataclass(frozen=True)
class Metrics:
    precision: float
    recall: float
    f1: float
    false_positive_rate: float
    comment_reduction: float
    redundancy_reduction: float
    acceptance_rate: float
    actionability: float | None

    def to_dict(self) -> dict[str, float | None]:
        return {
            "precision": self.precision,
            "recall": self.recall,
            "f1": self.f1,
            "false_positive_rate": self.false_positive_rate,
            "comment_reduction": self.comment_reduction,
            "redundancy_reduction": self.redundancy_reduction,
            "acceptance_rate": self.acceptance_rate,
            "actionability": self.actionability,
        }


def outcome_total(corpus_label_counts: Mapping[str, int]) -> int:
    """Total labelled (included) outcomes across the corpus."""
    from app.experiments.labeling import (
        NON_ISSUE_LABELS,
        POSITIVE_LABELS,
        REAL_LABELS,
    )

    considered = POSITIVE_LABELS | REAL_LABELS | NON_ISSUE_LABELS
    return sum(
        count for label, count in corpus_label_counts.items() if label in considered
    )


def _ratio(numerator: int, denominator: int) -> float:
    if denominator == 0:
        return 0.0
    return round(numerator / denominator, 6)


def compute_metrics(outcome: RunOutcome) -> Metrics:
    """ARUM §5 metrics from a run outcome.

    - ``precision``: published ∩ {ACCEPTED,FIXED} ÷ published.
    - ``recall``: published ∩ real ÷ all real candidates.
    - ``f1``: harmonic mean of the above.
    - ``false_positive_rate``: suppressed-or-unpublished non-issues ÷ non-issues.
    - ``comment_reduction``: (raw − published) ÷ raw.
    - ``redundancy_reduction``: members collapsed into groups ÷ members.
    - ``acceptance_rate``: ACCEPTED+FIXED outcomes ÷ all labelled outcomes.
    - ``actionability``: published with a root-caused fix ÷ published with a
      known flag; ``None`` when the corpus carries no actionable flags.
    """
    precision = _ratio(outcome.accepted_published, outcome.published_count)
    recall = _ratio(outcome.published_positives, outcome.positives)
    if precision and recall:
        f1 = round(2 * precision * recall / (precision + recall), 6)
    else:
        f1 = 0.0
    accepted = outcome.accepted_published
    # acceptance rate over published positives (ARUM §5: ACCEPTED+FIXED ÷
    # outcomes); with the outcome_total base recorded alongside for context.
    acceptance_rate = _ratio(accepted, outcome.published_count)
    known = outcome.actionable_known
    return Metrics(
        precision=precision,
        recall=recall,
        f1=f1,
        false_positive_rate=_ratio(outcome.suppressed_non_real, outcome.non_real),
        comment_reduction=_ratio(
            outcome.raw_count - outcome.published_count, outcome.raw_count
        ),
        redundancy_reduction=_ratio(
            outcome.member_rows - outcome.group_count, outcome.member_rows
        ),
        acceptance_rate=acceptance_rate,
        actionability=(_ratio(outcome.actionable_published, known) if known else None),
    )
