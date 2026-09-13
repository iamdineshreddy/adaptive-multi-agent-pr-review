"""Unified-diff parsing + per-file change slicing (docs/AGENTS.md §3).

A patch from the GitHub API is split into hunks; each hunk is trimmed to a bounded
model-facing window carrying the new-file line range so agents can anchor findings
to absolute line numbers.
"""

from __future__ import annotations

import re
from dataclasses import dataclass

from app.agents.contract import ChangeKind, FileSlice

_HUNK_HEADER = re.compile(r"^@@ -(\d+)(?:,(\d+))? \+(\d+)(?:,(\d+))? @@(.*)$")

_DEFAULT_MAX_SLICE_CHARS = 6000
_MAX_SLICES_PER_FILE = 12


@dataclass(frozen=True)
class Hunk:
    """One ``@@ .. @@`` hunk with mapped new-file line numbers."""

    old_start: int
    new_start: int
    old_count: int
    new_count: int
    added_lines: tuple[int, ...]  # new-file absolute lines added by this hunk
    body: str
    header: str


def parse_patch(patch: str) -> list[Hunk]:
    """Parse a unified diff body into hunks (tolerates missing trailing newlines).

    Line tracking maps each hunk line back to absolute new-file positions: ``+``
    lines advance the new counter and are recorded; ``-`` lines advance the old
    counter only; `` \\ No newline at end of file`` lines are attached verbatim.
    """
    hunks: list[Hunk] = []
    header_match: re.Match[str] | None = None
    old_pos = 0
    new_pos = 0
    body_lines: list[str] = []
    added_lines: list[int] = []

    def flush() -> None:
        if header_match is None:
            return
        hunks.append(
            Hunk(
                old_start=int(header_match.group(1)),
                new_start=int(header_match.group(3)),
                old_count=int(header_match.group(2) or 1),
                new_count=int(header_match.group(4) or 1),
                added_lines=tuple(added_lines),
                body="\n".join(body_lines),
                header=header_match.group(0),
            )
        )

    for raw_line in patch.splitlines():
        if raw_line.strip().startswith("@@"):
            flush()
            match = _HUNK_HEADER.match(raw_line.strip())
            if match is None:
                header_match, old_pos, new_pos, body_lines, added_lines = (
                    None,
                    0,
                    0,
                    [],
                    [],
                )
                continue
            header_match = match
            old_pos = int(match.group(1))
            new_pos = int(match.group(3))
            body_lines = []
            added_lines = []
            continue
        if header_match is None:
            continue

        if raw_line.startswith("+"):
            added_lines.append(new_pos)
            new_pos += 1
            body_lines.append(raw_line)
        elif raw_line.startswith("-"):
            old_pos += 1
            body_lines.append(raw_line)
        elif raw_line.startswith(" "):
            old_pos += 1
            new_pos += 1
            body_lines.append(raw_line)
        elif raw_line.startswith("\\"):
            body_lines.append(raw_line)

    flush()
    return hunks


def changed_file_slices(
    file_path: str,
    patch: str | None,
    status: str | None = None,
    *,
    max_slice_chars: int = _DEFAULT_MAX_SLICE_CHARS,
    max_slices: int = _MAX_SLICES_PER_FILE,
) -> list[FileSlice]:
    """Build bounded FileSlices for one changed file (renames/removals included)."""
    if not patch:
        return [_path_only_slice(file_path, status)]

    hunks = parse_patch(patch)
    slices: list[FileSlice] = []
    for hunk in hunks[:max_slices]:
        body = hunk.body[:max_slice_chars]
        if hunk.new_count:
            new_end = hunk.new_start + hunk.new_count - 1
        else:
            new_end = hunk.new_start
        slices.append(
            FileSlice(
                file_path=file_path,
                patch=f"{hunk.header}\n{body}",
                new_start=hunk.new_start,
                new_end=new_end,
                change_kind=_infer_kind(status),
            )
        )
    return slices


def _path_only_slice(file_path: str, status: str | None) -> FileSlice:
    kind = _infer_kind(status)
    return FileSlice(
        file_path=file_path,
        patch=f"(no diff window supplied; file {kind.value} in this PR)",
        new_start=None,
        new_end=None,
        change_kind=kind,
    )


def _infer_kind(status: str | None) -> ChangeKind:
    if not status:
        return ChangeKind.UNKNOWN
    lowered = status.strip().lower()
    mapping = {
        "added": ChangeKind.ADDED,
        "modified": ChangeKind.MODIFIED,
        "removed": ChangeKind.REMOVED,
        "deleted": ChangeKind.REMOVED,
        "renamed": ChangeKind.RENAMED,
        "copied": ChangeKind.RENAMED,
    }
    return mapping.get(lowered, ChangeKind.UNKNOWN)


def file_slices_from_patches(
    files: list[tuple[str, str | None, str | None]],
    *,
    max_slice_chars: int = _DEFAULT_MAX_SLICE_CHARS,
) -> list[FileSlice]:
    """Convenience builder from (file_path, patch, status) triples."""
    slices: list[FileSlice] = []
    for file_path, patch, status in files:
        slices.extend(
            changed_file_slices(
                file_path,
                patch,
                status,
                max_slice_chars=max_slice_chars,
            )
        )
    return slices
