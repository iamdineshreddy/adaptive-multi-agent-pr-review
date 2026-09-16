"""CodeReview (CodeRefinement) archive → raw corpus ingestion (Phase 17 part 2).

The Li et al. CodeReview archive (Zenodo 6900648, CC-BY-4.0 —
``datasets/README.md``) provides real review comments and the before/after pair
from the code-change triplet, but **not** the research fields the pipeline
requires (``category`` / ``severity`` / ``confidence`` / ``reviewer``) nor
suggestion-matching implementation evidence. This module is the honest splice:

- **Mechanical mapping** — derived from the archive without interpretation:
  ``review_id`` (deterministic content hash), ``comment``, ``file_path``,
  ``implemented_later`` (= the before/after pair differs), ``round``,
  ``memory``, optional line numbers / suggested fix.
- **Annotation overlay** — an optional JSONL keyed by the mechanical
  ``review_id`` supplying the research fields and outcome evidence
  (``implementation`` / ``explicit_outcome``) per the protocol in
  ``datasets/README.md``. Nothing is invented here: any record the overlay does
  not fully cover is **excluded** with a machine-readable reason, so the
  labelled research corpus never contains annotation it did not really get.

Pass flow (documented in ``datasets/README.md``): (1) mechanical ingest → the
``annotation_required`` exclusions yield the review-ids to annotate;
(2) annotate those; (3) re-ingest with ``--annotation`` and an audit manifest.

Hard rule (docs/Auth.md): a differing before/after pair is change evidence,
never acceptance evidence. Positive labels therefore require the overlay's
``implementation`` (suggestion-matching, verified by the annotator) or an
``explicit_outcome``; a verbatim pair with no overlay evidence maps to
IGNORED/no-response, never to a positive label.
"""

from __future__ import annotations

import hashlib
import json
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from app.experiments.corpus import Corpus, build_corpus
from app.experiments.io import write_json
from app.experiments.labeling import VALID_EXPLICIT_OUTCOMES, VALID_IMPLEMENTATIONS

ARCHIVE_DOI = "10.5281/zenodo.6900648"
ARCHIVE_LICENSE = "CC-BY-4.0"

REQUIRED_ARCHIVE_FIELDS = (
    "repo",
    "pr_number",
    "file_path",
    "review_comment",
    "code_before",
    "code_after",
)

# Research fields the archive cannot supply; the annotation overlay must.
ANNOTATION_REQUIRED_FIELDS = ("category", "severity", "confidence", "reviewer")
# Outcome evidence that may come from the overlay; enum-validated.
ANNOTATION_OPTIONAL_FIELDS = (
    "implementation",
    "explicit_outcome",
    "actionable",
    "suggested_fix",
)

EXCLUSION_ANNOTATION_REQUIRED = "annotation_required"
EXCLUSION_ANNOTATION_MISSING = "annotation_missing"
EXCLUSION_MISSING_ARCHIVE_FIELDS = "missing_archive_fields"
EXCLUSION_INVALID_MEMORY = "invalid_memory"
EXCLUSION_ANNOTATION_INCOMPLETE = "annotation_incomplete"


class IngestError(ValueError):
    """Malformed archive record or invalid annotation input."""


@dataclass(frozen=True)
class IngestRecord:
    """One mapped raw-schema record plus its exclusion reason (None = included)."""

    record: dict[str, Any]
    exclusion_reason: str | None
    review_id: str


def ingest(
    archive: Sequence[Mapping[str, Any]],
    *,
    annotation: Sequence[Mapping[str, Any]] | None = None,
) -> list[IngestRecord]:
    """Map every archive record, splicing the annotation overlay when given."""
    overlay = _index_overlay(annotation)
    results: list[IngestRecord] = []
    for entry in archive:
        record, reason = _map_record(entry, overlay)
        results.append(IngestRecord(record, reason, str(record["review_id"])))
    return results


def _index_overlay(
    annotation: Sequence[Mapping[str, Any]] | None,
) -> dict[str, Mapping[str, Any]]:
    if annotation is None:
        return {}
    indexed: dict[str, Mapping[str, Any]] = {}
    for entry in annotation:
        key = entry.get("review_id")
        if not key:
            raise IngestError("annotation overlay entries need a 'review_id' key")
        indexed[str(key)] = entry
    return indexed


def _map_record(
    entry: Mapping[str, Any],
    overlay: Mapping[str, Mapping[str, Any]],
) -> tuple[dict[str, Any], str | None]:
    missing = [key for key in REQUIRED_ARCHIVE_FIELDS if not entry.get(key)]
    if missing:
        raise IngestError(
            f"archive record missing required fields: {', '.join(missing)}"
        )

    review_id = _review_id(entry)
    annotation_for = overlay.get(review_id)
    if not overlay:
        return _partial(review_id, entry), EXCLUSION_ANNOTATION_REQUIRED
    if annotation_for is None:
        return _partial(review_id, entry), EXCLUSION_ANNOTATION_MISSING

    record = _partial(review_id, entry)
    _apply_annotation(record, annotation_for)
    reason = _missing_annotation(record)
    if reason is not None:
        return record, reason
    return record, None


def _review_id(entry: Mapping[str, Any]) -> str:
    """Deterministic identity: content-based so reruns and annotators agree.

    Prefers the archive's own ``comment_id`` when present; otherwise a sha256
    over ``repo|pr_number|file_path|review_comment``.
    """
    explicit = entry.get("comment_id")
    if explicit:
        return str(explicit)
    digest = hashlib.sha256(
        "|".join(
            (
                str(entry["repo"]),
                str(entry["pr_number"]),
                str(entry["file_path"]),
                str(entry["review_comment"]),
            )
        ).encode("utf-8")
    ).hexdigest()
    return f"code-review:{digest}"


def _partial(review_id: str, entry: Mapping[str, Any]) -> dict[str, Any]:
    """Mechanical-only mapping (no interpretation, no invented annotation)."""
    memory = entry.get("memory")
    if memory is not None and not isinstance(memory, dict):
        raise IngestError("archive 'memory' must be a JSON object when present")
    before = str(entry["code_before"])
    after = str(entry["code_after"])
    return {
        "review_id": review_id,
        "file_path": str(entry["file_path"]),
        "line_start": entry.get("line_start"),
        "line_end": entry.get("line_end"),
        "category": entry.get("category"),
        "severity": entry.get("severity"),
        "confidence": entry.get("confidence"),
        "reviewer": entry.get("reviewer_username") or entry.get("reviewer"),
        "round": _optional_int(entry.get("round"), default=1),
        "comment": str(entry["review_comment"]),
        "suggested_fix": entry.get("suggested_fix"),
        "actionable": _optional_bool(entry.get("actionable")),
        "is_review_comment": True,
        "related_to_feedback": True,
        "implemented_later": before != after,
        "implementation": entry.get("implementation"),
        "explicit_outcome": entry.get("explicit_outcome"),
        "memory": memory or {},
        "provenance": {
            "source_archive": ARCHIVE_DOI,
            "record_hash": _content_hash(review_id, entry),
        },
    }


def _apply_annotation(
    record: dict[str, Any],
    overlay_entry: Mapping[str, Any],
) -> None:
    for key in (
        "category",
        "severity",
        "reviewer",
        "implementation",
        "explicit_outcome",
        "actionable",
        "suggested_fix",
        "memory",
    ):
        if key in overlay_entry:
            record[key] = overlay_entry[key]
    if overlay_entry.get("round") is not None:
        record["round"] = _optional_int(overlay_entry["round"], default=1)
    if overlay_entry.get("confidence") is not None:
        record["confidence"] = float(overlay_entry["confidence"])

    implementation = record.get("implementation")
    if implementation is not None and implementation not in VALID_IMPLEMENTATIONS:
        raise IngestError(
            f"{record['review_id']}: unknown implementation {implementation!r} "
            f"(valid: {', '.join(sorted(VALID_IMPLEMENTATIONS))})"
        )
    explicit = record.get("explicit_outcome")
    if explicit is not None and explicit not in VALID_EXPLICIT_OUTCOMES:
        raise IngestError(
            f"{record['review_id']}: unknown explicit_outcome {explicit!r} "
            f"(valid: {', '.join(sorted(VALID_EXPLICIT_OUTCOMES))})"
        )


def _missing_annotation(record: Mapping[str, Any]) -> str | None:
    """Return the exclusion reason when a research field is still absent."""
    for key in ANNOTATION_REQUIRED_FIELDS:
        value = record.get(key)
        if value is None or (isinstance(value, str) and not value.strip()):
            return f"{EXCLUSION_ANNOTATION_INCOMPLETE}:{key}"
    try:
        confidence = float(record["confidence"])
        if not 0.0 <= confidence <= 1.0:
            return f"{EXCLUSION_ANNOTATION_INCOMPLETE}:confidence"
    except (TypeError, ValueError):
        return f"{EXCLUSION_ANNOTATION_INCOMPLETE}:confidence"
    return None


def _content_hash(review_id: str, entry: Mapping[str, Any]) -> str:
    raw = "|".join(
        (
            review_id,
            str(entry.get("file_path", "")),
            str(entry.get("review_comment", "")),
            str(entry.get("code_before", "")),
            str(entry.get("code_after", "")),
        )
    )
    return hashlib.sha256(raw.encode("utf-8")).hexdigest()


def _optional_int(value: object, *, default: int) -> int:
    if value is None or value == "":
        return default
    return int(str(value))


def _optional_bool(value: object) -> bool | None:
    if value is None:
        return None
    return bool(value)


def render_preview(included: Sequence[IngestRecord], *, source: str) -> dict[str, Any]:
    """Pipeline audit preview: label a fully-annotated subset through build_corpus.

    Raises :class:`CorpusError` when an included record would not pass the
    experiment pipeline's own validation (a loud data-quality gate, never a
    silent drop).
    """
    if not included:
        return {"records": 0, "label_counts": {}, "rules": {}}
    corpus: Corpus = build_corpus([item.record for item in included], source=source)
    rules: dict[str, int] = {}
    for row in corpus.rows:
        rules[row.rule] = rules.get(row.rule, 0) + 1
    return {
        "records": len(corpus.rows),
        "included": corpus.included_count,
        "excluded": corpus.excluded_count,
        "label_counts": corpus.label_counts,
        "rules": rules,
    }


def write_ingest_outputs(
    records: Sequence[IngestRecord],
    *,
    out: Path,
    excluded: Path,
    manifest: Path,
    source_archive: str,
    annotation_path: str | None,
) -> None:
    """Write included raw JSONL, excluded JSONL (with reason), and the manifest."""
    included = [item for item in records if item.exclusion_reason is None]
    excluded_records = [
        {
            **item.record,
            "ingestion_excluded_reason": item.exclusion_reason,
        }
        for item in records
        if item.exclusion_reason is not None
    ]
    _write_jsonl(out, [item.record for item in included])
    _write_jsonl(excluded, excluded_records)

    exclusions: dict[str, int] = {}
    for item in records:
        if item.exclusion_reason is not None:
            exclusions[item.exclusion_reason] = (
                exclusions.get(item.exclusion_reason, 0) + 1
            )

    archive_bytes = Path(source_archive).read_bytes()
    preview = render_preview(included, source=str(out))
    write_json(
        manifest,
        {
            "dataset": {
                "doi": ARCHIVE_DOI,
                "license": ARCHIVE_LICENSE,
                "source_archive": source_archive,
                "archive_sha256": hashlib.sha256(archive_bytes).hexdigest(),
            },
            "annotation": annotation_path,
            "counts": {
                "total": len(records),
                "included": len(included),
                "excluded": len(excluded_records),
            },
            "exclusion_reasons": exclusions,
            "labelled_preview": preview,
            "integrity": (
                "Included records carry evidence-mapped archive fields plus an "
                "annotation overlay under the protocol in datasets/README.md. "
                "Nothing is inferred by the ingest step."
            ),
        },
    )


def _write_jsonl(path: Path, records: Sequence[Mapping[str, Any]]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", encoding="utf-8") as handle:
        for record in records:
            handle.write(json.dumps(record, ensure_ascii=False))
            handle.write("\n")
