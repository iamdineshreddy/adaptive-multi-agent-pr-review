"""PR priority scoring at ingestion (docs/QUEUE.md §2).

    priority_score = scale * (w_sec·SecurityRisk + w_impact·ChangeImpact
                            + w_hist·HistoricalRisk + w_comp·ComponentCriticality
                            + w_dep·DependencyRisk + w_urgency·RepoPriority)

Each factor is a documented 0..1 value. Factors that need data the webhook payload
does not provide are returned as an explicit, documented baseline (0.0) until the
phase that supplies them lands:

- SecurityRisk / DependencyRisk / ComponentCriticality need the changed-file list
  (GitHub REST client -> Phase 5 with the ingestion worker).
- HistoricalRisk needs repository memory (Phase 10).
"""

from __future__ import annotations

import math
from dataclasses import dataclass

from app.config.settings import Settings
from app.models.enums import RiskClass
from app.webhooks.schemas import GitHubPullRequest

_UNREACHABLE_FACTOR = 0.0


@dataclass(frozen=True)
class PriorityFactors:
    security_risk: float
    change_impact: float
    historical_risk: float
    component_criticality: float
    dependency_risk: float
    repo_priority: float


@dataclass(frozen=True)
class PriorityResult:
    score: float
    risk_class: RiskClass
    factors: PriorityFactors


def change_impact(changed_files: int, additions: int, deletions: int) -> float:
    """Log-binned blast radius from counts alone (no file paths needed)."""
    files = max(0, changed_files)
    lines = max(0, additions + deletions)
    f_files = min(1.0, math.log10(files + 1) / math.log10(1001))
    f_lines = min(1.0, math.log10(lines + 1) / math.log10(100_001))
    return 0.4 * f_files + 0.6 * f_lines


def repo_priority(urgency: float | None) -> float:
    """Repository-set urgency override (configured on the repository row)."""
    if urgency is None:
        return _UNREACHABLE_FACTOR
    return max(0.0, min(1.0, float(urgency)))


def risk_class_for(score: float, settings: Settings) -> RiskClass:
    if score < settings.risk_low_threshold:
        return RiskClass.LOW
    if score <= settings.risk_medium_threshold:
        return RiskClass.MEDIUM
    return RiskClass.HIGH


def compute_priority(
    pr: GitHubPullRequest,
    settings: Settings,
    repo_urgency: float | None = None,
) -> PriorityResult:
    """Compute the ingestion priority score and derived risk class."""
    factors = PriorityFactors(
        security_risk=_UNREACHABLE_FACTOR,
        change_impact=change_impact(pr.changed_files, pr.additions, pr.deletions),
        historical_risk=_UNREACHABLE_FACTOR,
        component_criticality=_UNREACHABLE_FACTOR,
        dependency_risk=_UNREACHABLE_FACTOR,
        repo_priority=repo_priority(repo_urgency),
    )
    raw = (
        settings.priority_w_security * factors.security_risk
        + settings.priority_w_impact * factors.change_impact
        + settings.priority_w_history * factors.historical_risk
        + settings.priority_w_component * factors.component_criticality
        + settings.priority_w_dependency * factors.dependency_risk
        + settings.priority_w_urgency * factors.repo_priority
    )
    score = settings.priority_scale * raw
    return PriorityResult(
        score=round(score, 3),
        risk_class=risk_class_for(score, settings),
        factors=factors,
    )
