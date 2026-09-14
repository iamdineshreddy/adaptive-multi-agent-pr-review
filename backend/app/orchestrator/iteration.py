"""Iterative review engine (docs/ARCHITECTURE.md §4.7, ARUM.md §9, FR-6).

The pure, broker/DB-free kernel behind diff-aware re-review. Given the findings
of a previous review and the changed-file/line windows of the new commit
(delta), each previous finding is classified:

- ``RESOLVED`` — the flagged region is gone (its file was removed); mechanical,
  derived from the diff. This is *not* developer acceptance, so nothing is fed
  into feedback memory (FR-5.2 discipline).
- ``STALE`` — the region was touched by the new commit: re-evaluate. Only the
  agents responsible for those regions are targeted for a re-run (FR-6.2).
- ``KEEP`` — the code is untouched: carried forward as previously reviewed, at
  zero agent cost (FR-6.3, ARUM.md §9 "round 2 cheaper and quieter").

Only the delta is re-scored/published — unchanged findings are never
re-decided. All inputs are explicit (no store access): the service and the
future production seam decide where ``previous_findings`` comes from.
"""

from __future__ import annotations

import enum
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from typing import Any

from app.agents.contract import ChangeKind, FileSlice
from app.agents.prompts import PROMPT_SPECS

#: Tolerance (lines) when deciding whether a finding overlaps a changed window
#: while the engine still has no per-repo tuning. Zero = exact overlap.
DEFAULT_LINE_GAP = 0


class DeltaAction(enum.StrEnum):
    """What happens to a previous finding under the new commit's delta."""

    RESOLVED = "RESOLVED"
    STALE = "STALE"
    KEEP = "KEEP"


@dataclass(frozen=True)
class Resolution:
    """The disposition of one previous finding (FR-6.1)."""

    finding_id: str
    action: DeltaAction
    file_path: str
    category: str
    line_start: int | None
    line_end: int | None
    reason: str


@dataclass(frozen=True)
class IterationPlan:
    """The classified delta: dispositions + the targeted agent set + counts.

    ``targeted_agents`` is empty when nothing can be re-checked (e.g. all prior
    findings resolved); the orchestrator then falls back to the full agent set
    so newly-changed regions still get scanned.
    """

    resolutions: tuple[Resolution, ...]
    targeted_agents: tuple[str, ...]
    changed_paths: tuple[str, ...]
    removed_paths: tuple[str, ...]

    @property
    def resolved_count(self) -> int:
        return sum(1 for r in self.resolutions if r.action is DeltaAction.RESOLVED)

    @property
    def stale_count(self) -> int:
        return sum(1 for r in self.resolutions if r.action is DeltaAction.STALE)

    @property
    def keep_count(self) -> int:
        return sum(1 for r in self.resolutions if r.action is DeltaAction.KEEP)

    def resolutions_by(self, action: DeltaAction) -> tuple[Resolution, ...]:
        return tuple(r for r in self.resolutions if r.action is action)

    def diff_stats(self) -> dict[str, Any]:
        """JSON-safe summary for ``review_iterations.diff_stats``."""
        return {
            "changed_paths": list(self.changed_paths),
            "removed_paths": list(self.removed_paths),
            "previous": len(self.resolutions),
            "resolved": self.resolved_count,
            "stale": self.stale_count,
            "keep": self.keep_count,
            "targeted_agents": list(self.targeted_agents),
        }


def plan_review_delta(
    previous_findings: Sequence[Mapping[str, Any]],
    changed_slices: Sequence[FileSlice],
    *,
    line_gap: int = DEFAULT_LINE_GAP,
    registry: Mapping[str, Any] | None = None,
) -> IterationPlan:
    """Classify every previous finding against the new commit's delta.

    ``previous_findings`` rows need ``id``, ``file_path``, ``category``, and
    optional ``line_start``/``line_end`` (None = whole-file/unknown region).
    ``changed_slices`` are the delta's diff windows; a ``REMOVED`` slice means
    the whole file is gone. Agent targeting maps changed-region categories back
    to their owning agents via the prompt registry.
    """
    slices_by_path: dict[str, list[FileSlice]] = {}
    removed: set[str] = set()
    for slice_ in changed_slices:
        if slice_.change_kind is ChangeKind.REMOVED:
            removed.add(slice_.file_path)
        slices_by_path.setdefault(slice_.file_path, []).append(slice_)

    resolutions: list[Resolution] = []
    stale_categories: set[str] = set()
    for finding in previous_findings:
        finding_id = str(finding.get("id") or "")
        file_path = str(finding.get("file_path") or "")
        category = str(finding.get("category") or "")
        line_start = _optional_int(finding.get("line_start"))
        line_end = _optional_int(finding.get("line_end"))

        if not finding_id or not file_path:
            continue
        if file_path in removed:
            resolutions.append(
                Resolution(
                    finding_id,
                    DeltaAction.RESOLVED,
                    file_path,
                    category,
                    line_start,
                    line_end,
                    "file removed by the new commit",
                )
            )
            continue
        windows = slices_by_path.get(file_path)
        if windows is None:
            resolutions.append(
                Resolution(
                    finding_id,
                    DeltaAction.KEEP,
                    file_path,
                    category,
                    line_start,
                    line_end,
                    "file untouched by the new commit",
                )
            )
            continue
        if _overlaps_any_window(windows, line_start, line_end, line_gap=line_gap):
            resolutions.append(
                Resolution(
                    finding_id,
                    DeltaAction.STALE,
                    file_path,
                    category,
                    line_start,
                    line_end,
                    "region touched by the new commit; re-evaluate",
                )
            )
            if category:
                stale_categories.add(category)
        else:
            resolutions.append(
                Resolution(
                    finding_id,
                    DeltaAction.KEEP,
                    file_path,
                    category,
                    line_start,
                    line_end,
                    "file changed elsewhere; region untouched",
                )
            )

    targeted = _agents_for_categories(stale_categories, registry=registry)
    return IterationPlan(
        resolutions=tuple(resolutions),
        targeted_agents=tuple(targeted),
        changed_paths=tuple(sorted(slices_by_path)),
        removed_paths=tuple(sorted(removed)),
    )


def _overlaps_any_window(
    windows: Sequence[FileSlice],
    line_start: int | None,
    line_end: int | None,
    *,
    line_gap: int,
) -> bool:
    """True when the finding's line range intersects any changed window.

    A finding without a line range (whole-file / unknown) in a changed file is
    conservatively treated as touched: we cannot prove it is untouched, so it
    must be re-checked (a false ``KEEP`` would silently carry a finding that may
    no longer exist).
    """
    if line_start is None:
        return True
    start, end = line_start, line_end if line_end is not None else line_start
    for window in windows:
        w_start, w_end = window.new_start, window.new_end
        if w_start is None:
            continue
        w_end_effective = w_end if w_end is not None else w_start
        if not (end + line_gap < w_start or w_end_effective + line_gap < start):
            return True
    return False


def _agents_for_categories(
    categories: set[str],
    *,
    registry: Mapping[str, Any] | None,
) -> list[str]:
    """The agents whose prompt categories own any ``categories`` (prefix match).

    Deterministic order (module-declaration order of the registry) so a planned
    iteration is reproducible.
    """
    specs = dict(registry) if registry is not None else PROMPT_SPECS
    wanted: list[str] = []
    for key, spec in specs.items():
        allowed = tuple(spec.categories)
        if any(_category_allowed(category, allowed) for category in categories):
            wanted.append(key)
    return wanted


def _category_allowed(category: str, allowed: Sequence[str]) -> bool:
    for entry in allowed:
        base = entry.rstrip("/")
        if category == base or category.startswith(base + "/"):
            return True
    return False


def _optional_int(value: Any) -> int | None:
    if value is None or value == "":
        return None
    try:
        return int(value)
    except (TypeError, ValueError):
        return None
