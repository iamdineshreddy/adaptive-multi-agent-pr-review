"""Unit tests for the iterative-review kernel (Phase 12, FR-6).

The pure classification in ``app.orchestrator.iteration`` decides what happens
to each previous finding under a new commit's diff: RESOLVED (file removed),
STALE (region touched — re-evaluate), KEEP (untouched — carried forward at zero
cost). These tests pin the classification + agent-targeting contract with no
broker or database.
"""

from __future__ import annotations

from app.agents.contract import ChangeKind, FileSlice
from app.agents.prompts import PROMPT_SPECS
from app.orchestrator.iteration import (
    DEFAULT_LINE_GAP,
    DeltaAction,
    IterationPlan,
    plan_review_delta,
)


def _slice(
    path: str,
    *,
    start: int | None = None,
    end: int | None = None,
    kind: ChangeKind = ChangeKind.MODIFIED,
) -> FileSlice:
    return FileSlice(
        path,
        "@@ hunk @@\n",
        new_start=start,
        new_end=end,
        change_kind=kind,
    )


def _finding(
    *,
    finding_id: str = "f-1",
    file_path: str = "app/auth.py",
    category: str = "security/xss",
    line_start: int | None = 10,
    line_end: int | None = 12,
) -> dict:
    return {
        "id": finding_id,
        "file_path": file_path,
        "category": category,
        "line_start": line_start,
        "line_end": line_end,
    }


def _agents_for(category: str) -> list[str]:
    """Registered agent keys whose prompt categories allow ``category``."""
    return [
        key
        for key, spec in PROMPT_SPECS.items()
        if any(
            category == base or category.startswith(base + "/")
            for base in (entry.rstrip("/") for entry in spec.categories)
        )
    ]


class TestPlanReviewDelta:
    def test_untouched_file_is_kept_with_zero_targets(self) -> None:
        plan = plan_review_delta(
            [_finding()],
            [_slice("app/other.py", start=1, end=5)],
        )
        assert [r.action for r in plan.resolutions] == [DeltaAction.KEEP]
        assert plan.keep_count == 1
        assert plan.resolved_count == 0
        assert plan.stale_count == 0
        assert plan.changed_paths == ("app/other.py",)
        assert plan.targeted_agents == ()

    def test_removed_file_resolves_the_finding(self) -> None:
        plan = plan_review_delta(
            [_finding(file_path="app/dead.py")],
            [_slice("app/dead.py", kind=ChangeKind.REMOVED)],
        )
        resolution = plan.resolutions[0]
        assert resolution.action is DeltaAction.RESOLVED
        assert "removed" in resolution.reason
        assert plan.resolved_count == 1
        assert plan.removed_paths == ("app/dead.py",)

    def test_touched_region_is_stale_and_targets_its_agents(self) -> None:
        plan = plan_review_delta(
            [_finding(category="security/xss", line_start=10, line_end=12)],
            [_slice("app/auth.py", start=11, end=13)],
        )
        assert plan.resolutions[0].action is DeltaAction.STALE
        assert plan.stale_count == 1
        expected = sorted(_agents_for("security/xss"))
        assert sorted(plan.targeted_agents) == expected

    def test_region_elsewhere_in_changed_file_is_kept(self) -> None:
        plan = plan_review_delta(
            [_finding(line_start=100, line_end=102)],
            [_slice("app/auth.py", start=1, end=5)],
        )
        assert plan.resolutions[0].action is DeltaAction.KEEP
        assert plan.keep_count == 1

    def test_overlap_honours_line_gap_tolerance(self) -> None:
        finding = _finding(line_start=10, line_end=10)
        window = _slice("app/auth.py", start=15, end=15)
        assert (
            plan_review_delta([finding], [window]).resolutions[0].action
            is DeltaAction.KEEP
        )
        assert (
            plan_review_delta([finding], [window], line_gap=5).resolutions[0].action
            is DeltaAction.STALE
        )

    def test_finding_without_line_range_in_changed_file_is_stale(self) -> None:
        plan = plan_review_delta(
            [_finding(line_start=None, line_end=None)],
            [_slice("app/auth.py", start=1, end=5)],
        )
        assert plan.resolutions[0].action is DeltaAction.STALE

    def test_default_line_gap_is_zero(self) -> None:
        assert DEFAULT_LINE_GAP == 0

    def test_category_prefix_targets_owner(self) -> None:
        plan = plan_review_delta(
            [_finding(category="quality/complexity")],
            [_slice("app/auth.py", start=11, end=13)],
        )
        assert "quality" in plan.targeted_agents

    def test_diff_stats_are_json_safe(self) -> None:
        import json

        plan = plan_review_delta([_finding()], [_slice("app/auth.py", start=1, end=5)])
        json.dumps(plan.diff_stats())
        stats = plan.diff_stats()
        assert stats["previous"] == 1
        assert stats["targeted_agents"] == list(plan.targeted_agents)


class TestIterationPlan:
    def test_counts_by_action(self) -> None:
        plan = plan_review_delta(
            [
                _finding(finding_id="a", file_path="removed.py"),
                _finding(finding_id="b", line_start=11, line_end=12),
                _finding(finding_id="c", file_path="other.py"),
            ],
            [
                _slice("removed.py", kind=ChangeKind.REMOVED),
                _slice("app/auth.py", start=11, end=13),
                _slice("other2.py", start=1, end=2),
            ],
        )
        assert plan.resolved_count == 1
        assert plan.stale_count == 1
        assert plan.keep_count == 1

    def test_resolutions_by(self) -> None:
        plan = plan_review_delta(
            [
                _finding(finding_id="a", file_path="removed.py"),
                _finding(finding_id="b", line_start=11, line_end=12),
            ],
            [
                _slice("removed.py", kind=ChangeKind.REMOVED),
                _slice("app/auth.py", start=11, end=13),
            ],
        )
        kept = plan.resolutions_by(DeltaAction.KEEP)
        assert kept == ()

    def test_plan_is_frozen(self) -> None:
        plan = plan_review_delta([], [])
        assert isinstance(plan, IterationPlan)
        try:
            plan.resolutions = ()  # type: ignore[misc]
        except AttributeError:
            pass
        else:
            raise AssertionError("IterationPlan must be immutable")


class TestOrderingDeterminism:
    def test_targeted_agents_are_deterministic(self) -> None:
        finding = _finding(category="security/xss", line_start=11, line_end=12)
        window = _slice("app/auth.py", start=11, end=13)
        first = plan_review_delta([finding], [window])
        second = plan_review_delta([finding], [window])
        assert first.targeted_agents == second.targeted_agents

    def test_changed_paths_sorted(self) -> None:
        plan = plan_review_delta(
            [],
            [_slice("z.py", start=1, end=2), _slice("a.py", start=1, end=2)],
        )
        assert plan.changed_paths == ("a.py", "z.py")
