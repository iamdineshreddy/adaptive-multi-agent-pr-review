"""Corpus loading + validation for the experiment pipeline (§5 reproducibility).

One review comment (and its outcome signals) becomes one :class:`LabeledRow`.
Grouping is the pipeline's documented proximity/category leg only — reviewers
commenting on the same file/category and line bucket are duplicates — while the
embedding-similarity leg is delegated to the dataset's own duplicate comments
(the corpus does not invent vectors). ``content_hash`` mirrors the production
digest intent (deterministic per-comment identity).
"""

from __future__ import annotations

import hashlib
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from typing import Any, cast

from app.experiments.labeling import (
    VALID_SEVERITIES,
    LabelResult,
    label_outcome,
)

GROUP_BUCKET_SIZE = 4  # same proximity bucket = same file/line region


class CorpusError(ValueError):
    """Malformed corpus record."""


@dataclass(frozen=True)
class LabeledRow:
    """One labelled review comment (research + ARUM input fields)."""

    review_id: str
    file_path: str
    line_start: int | None
    line_end: int | None
    category: str
    severity: str
    confidence: float
    reviewer: str
    round_no: int
    comment: str
    suggested_fix: str | None
    actionable: bool | None
    group_key: str
    content_hash: str
    label: str | None
    rule: str
    included: bool
    memory: dict[str, Any]

    @property
    def is_positive(self) -> bool:
        """``True`` when this row is an ACCEPTED/FIXED labelled outcome."""
        return self.included and self.label in ("ACCEPTED", "FIXED")

    @property
    def is_real(self) -> bool:
        """``True`` for real issues (accepted/fixed/modified evidence)."""
        return self.included and self.label in ("ACCEPTED", "FIXED", "MODIFIED")


@dataclass(frozen=True)
class Corpus:
    """A validated, labelled review corpus with provenance (§5)."""

    rows: tuple[LabeledRow, ...]
    label_counts: dict[str, int]
    excluded_count: int
    provenance: dict[str, str | int]

    @property
    def included_count(self) -> int:
        return sum(self.label_counts.values())

    @property
    def reviews(self) -> tuple[str, ...]:
        unique: list[str] = []
        for row in self.rows:
            if row.review_id not in unique:
                unique.append(row.review_id)
        return tuple(unique)


def build_corpus(
    records: Sequence[Mapping[str, Any]],
    *,
    source: str,
) -> Corpus:
    """Label + validate raw records into a :class:`Corpus`.

    Every record becomes a row (excluded records included) so the full audit
    trail of rules is available; ``label_counts`` only counts included rows
    (docs/EXPERIMENTS.md §1 acceptance accounting).
    """
    rows: list[LabeledRow] = []
    counts: dict[str, int] = {}
    excluded = 0
    raw_payload = "\n".join(str(r) for r in records)
    for record in records:
        _validate(record)
        result: LabelResult = label_outcome(record)
        row = _to_row(record, result)
        rows.append(row)
        if row.included:
            assert row.label is not None
            counts[row.label] = counts.get(row.label, 0) + 1
        else:
            excluded += 1
    return Corpus(
        rows=tuple(rows),
        label_counts=counts,
        excluded_count=excluded,
        provenance={
            "source": source,
            "raw_sha256": hashlib.sha256(raw_payload.encode()).hexdigest(),
            "rows": len(rows),
            "included": len(rows) - excluded,
            "excluded": excluded,
        },
    )


def _validate(record: Mapping[str, Any]) -> None:
    for key in (
        "review_id",
        "file_path",
        "category",
        "severity",
        "comment",
        "reviewer",
        "confidence",
    ):
        if not record.get(key):
            raise CorpusError(f"missing or empty field {key!r}")
    if record["severity"].lower() not in VALID_SEVERITIES:
        raise CorpusError(
            f"unknown severity {record['severity']!r} "
            f"(valid: {', '.join(sorted(VALID_SEVERITIES))})"
        )
    confidence = float(record["confidence"])
    if not 0.0 <= confidence <= 1.0:
        raise CorpusError(f"confidence must be within [0, 1], got {confidence!r}")
    round_no = int(record.get("round", 1))
    if round_no < 1:
        raise CorpusError(f"round must be >= 1, got {round_no!r}")
    memory = record.get("memory")
    if memory is not None and not isinstance(memory, dict):
        raise CorpusError("memory must be a JSON object when present")


def _to_row(record: Mapping[str, Any], result: LabelResult) -> LabeledRow:
    line_start = _optional_int(record.get("line_start"))
    line_end = _optional_int(record.get("line_end"))
    actionable = _optional_bool(record.get("actionable"))
    return LabeledRow(
        review_id=str(record["review_id"]),
        file_path=str(record["file_path"]),
        line_start=line_start,
        line_end=line_end,
        category=str(record["category"]),
        severity=str(record["severity"]).lower(),
        confidence=float(record["confidence"]),
        reviewer=str(record["reviewer"]),
        round_no=int(record.get("round", 1)),
        comment=str(record["comment"]),
        suggested_fix=_optional_str(record.get("suggested_fix")),
        actionable=actionable,
        group_key=_group_key(
            str(record["review_id"]),
            str(record["file_path"]),
            str(record["category"]),
            line_start,
        ),
        content_hash=_content_hash(
            str(record["file_path"]),
            line_start,
            line_end,
            str(record["comment"]),
        ),
        label=result.label,
        rule=result.rule,
        included=result.included,
        memory=_memory(record),
    )


def _group_key(
    review_id: str,
    file_path: str,
    category: str,
    line_start: int | None,
) -> str:
    """Deterministic duplication key: file + category + proximity bucket."""
    bucket = "none" if line_start is None else line_start // GROUP_BUCKET_SIZE
    return f"{review_id}|{file_path}|{category}|{bucket}"


def _content_hash(
    file_path: str,
    line_start: int | None,
    line_end: int | None,
    comment: str,
) -> str:
    raw = f"{file_path}|{line_start}|{line_end}|{comment}"
    return hashlib.sha256(raw.encode()).hexdigest()


def _optional_int(value: object) -> int | None:
    if value is None:
        return None
    return int(cast(Any, value))


def _optional_str(value: object) -> str | None:
    if value is None:
        return None
    return str(value)


def _optional_bool(value: object) -> bool | None:
    if value is None:
        return None
    return bool(value)


def _memory(record: Mapping[str, Any]) -> dict[str, Any]:
    value = record.get("memory")
    if isinstance(value, dict):
        return value
    return {}
