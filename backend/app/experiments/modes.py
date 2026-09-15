"""Baseline + ablation mode definitions (docs/EXPERIMENTS.md §2, §4).

A mode is a declarative switch in the *same* selection pipeline the product
runs (features/weights/selection in ``app.adaptive``): a single mode's code
path exercises exactly the layers it declares rather than a hand-wired
alternate pipeline (EXPERIMENTS.md §2). ``B1``..``B5`` step one layer on at a
time; ablations ``A1``..``A6`` remove exactly one ``B5`` component so delta
tables are honest (EXPERIMENTS.md §4).
"""

from __future__ import annotations

from dataclasses import dataclass, replace

# settings.review_budget_medium (docs/ADAPTIVE.md, app/config/settings.py:76).
DEFAULT_BUDGET = 10
SINGLE_REVIEWER = "primary"


@dataclass(frozen=True)
class ModeConfig:
    """One baseline or ablation switch.

    ``single_reviewer`` limits the corpus to one reviewer's findings (B1);
    ``dedup_only`` collapses duplicate groups but publishes everything with no
    selection; ``adaptive`` enables ARUM ranking + budget; ``learned`` replaces
    the static v1 weights with a logistic fit on the corpus train split;
    ``memory`` feeds historical/RAG feature terms; ``budget`` is the review cap
    (``None`` = uncapped); ``gates`` turns on the non-negotiable safety gates;
    ``scope`` selects iteration rounds (``"first"`` vs ``"all"``);
    ``variance`` applies disagreement handling to agent agreement; and
    ``redundancy_filter`` collapses duplicate groups (off for the A3 ablation).
    """

    key: str
    label: str
    single_reviewer: str | None = None
    dedup_only: bool = False
    adaptive: bool = False
    learned: bool = False
    memory: bool = False
    budget: int | None = None
    gates: bool = False
    scope: str = "first"
    variance: bool = False
    redundancy_filter: bool = True


BASELINES: dict[str, ModeConfig] = {
    "B1": ModeConfig(
        "B1",
        "single reviewer, all findings (cap = none)",
        single_reviewer=SINGLE_REVIEWER,
        dedup_only=True,
    ),
    "B2": ModeConfig(
        "B2",
        "multi-agent, no adaptive selection (dedup-only, cap = none)",
        dedup_only=True,
    ),
    "B3": ModeConfig(
        "B3",
        "multi-agent + ARUM static weights + budget",
        adaptive=True,
        budget=DEFAULT_BUDGET,
    ),
    "B4": ModeConfig(
        "B4",
        "multi-agent + ARUM learned weights + memory",
        adaptive=True,
        learned=True,
        memory=True,
        budget=DEFAULT_BUDGET,
    ),
    "B5": ModeConfig(
        "B5",
        "full system (B4 + gates + iteration + disagreement handling)",
        adaptive=True,
        learned=True,
        memory=True,
        budget=DEFAULT_BUDGET,
        gates=True,
        scope="all",
        variance=True,
    ),
}

# Each ablation removes exactly one declared B5 component (EXPERIMENTS.md §4).
ABLATIONS: dict[str, ModeConfig] = {
    "A1": replace(  # no feedback memory (weights frozen, no decay)
        BASELINES["B5"],
        key="A1",
        label="no feedback memory (weights frozen, no decay)",
        learned=False,
        memory=False,
    ),
    "A2": replace(  # no RAG (no retrieval context)
        BASELINES["B5"], key="A2", label="no RAG (no retrieval context)", memory=False
    ),
    "A3": replace(  # no redundancy filtering (raw finding count)
        BASELINES["B5"],
        key="A3",
        label="no redundancy filtering",
        redundancy_filter=False,
    ),
    "A4": replace(  # no adaptive budget
        BASELINES["B5"], key="A4", label="no adaptive budget", budget=None
    ),
    "A5": replace(  # no iterative review
        BASELINES["B5"], key="A5", label="no iterative review", scope="first"
    ),
    "A6": replace(  # no disagreement handling
        BASELINES["B5"], key="A6", label="no disagreement handling", variance=False
    ),
}


def all_modes() -> tuple[ModeConfig, ...]:
    """All baselines then all ablations, in documented order."""
    return tuple(
        [BASELINES[k] for k in ("B1", "B2", "B3", "B4", "B5")]
        + [ABLATIONS[k] for k in ("A1", "A2", "A3", "A4", "A5", "A6")]
    )
