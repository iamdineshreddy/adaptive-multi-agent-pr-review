"""CodeReview (CodeRefinement) archive → raw corpus ingestion (Phase 17 part 2).

The Li et al. CodeReview archive (Zenodo 6900648, CC-BY-4.0 —
``datasets/README.md``) provides real review comments and the before/after pair
from the code-change triplet, but **not** the research fields the pipeline
requires (``category`` / ``severity`` / ``confidence`` / ``reviewer``) and it
carries **no file path at all** (evidence: ``datasets/DATASET_MAPPING_PROPOSAL.md``).
This module is the honest splice:

- **Mechanical mapping** — derived from the archive without interpretation:
  ``review_id`` (PR level: ``{repo}#{pr_number}``), ``comment``,
  ``implemented_later`` (= the before/after pair differs), line numbers read
  from the hunk header when present, ``round``, ``memory``, optional suggested
  fix. The published archive names (``ghid`` / ``comment`` / ``old`` / ``new``)
  and the bundled smoke fixture's raw-schema names (``pr_number`` /
  ``review_comment`` / ``code_before`` / ``code_after``) are both accepted —
  see ``ARCHIVE_ALIASES`` — so neither corpus has to be rewritten.
- **Annotation overlay** — a JSONL keyed by the **per-comment**
  :func:`annotation_key` (mirrored into ``provenance.annotation_key``) that
  supplies the research fields **and** ``file_path`` (D1: the archive has no
  path) plus outcome evidence (``implementation`` / ``explicit_outcome``) per
  the protocol in ``datasets/README.md``. Nothing is invented here: any record
  the overlay does not fully cover is **excluded** with a machine-readable
  reason, so the labelled research corpus never contains annotation it did not
  really get.

Two identities exist on purpose (D2): ``IngestRecord.review_id`` is PR level so
``corpus._group_key`` can form multi-comment redundancy groups, while
``IngestRecord.annotation_key`` stays per comment so one comment cannot
overwrite another's overlay entry.

Pass flow (documented in ``datasets/README.md``): (1) mechanical ingest → the
``annotation_required`` exclusions yield the per-comment annotation keys to
annotate; (2) annotate those; (3) re-ingest with ``--annotation`` and an audit
manifest.

Hard rule (docs/Auth.md): a differing before/after pair is change evidence,
never acceptance evidence. Positive labels therefore require the overlay's
``implementation`` (suggestion-matching, verified by the annotator) or an
``explicit_outcome``; a verbatim pair with no overlay evidence maps to
IGNORED/no-response, never to a positive label.
"""

from __future__ import annotations

import hashlib
import json
import re
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from app.experiments.corpus import Corpus, build_corpus
from app.experiments.io import write_json
from app.experiments.labeling import VALID_EXPLICIT_OUTCOMES, VALID_IMPLEMENTATIONS

ARCHIVE_DOI = "10.5281/zenodo.6900648"
ARCHIVE_LICENSE = "CC-BY-4.0"

# Raw-schema field -> every archive spelling accepted for it. The published
# CodeReview record uses the second name (verified against the archive bytes:
# datasets/DATASET_MAPPING_PROPOSAL.md §1); the bundled smoke fixture and the
# raw-schema examples use the raw-schema name itself.
ARCHIVE_ALIASES: dict[str, tuple[str, ...]] = {
    "repo": ("repo",),
    "pr_number": ("pr_number", "ghid"),
    "review_comment": ("review_comment", "comment"),
    "code_before": ("code_before", "old"),
    "code_after": ("code_after", "new"),
    "file_path": ("file_path",),  # optional in the archive; overlay otherwise
}

# What the archive itself must supply. ``file_path`` is deliberately absent: the
# published archive carries no path field, so the path comes from the overlay
# (decision D1 in datasets/DATASET_MAPPING_PROPOSAL.md).
REQUIRED_ARCHIVE_FIELDS = (
    "repo",
    "pr_number",
    "review_comment",
    "code_before",
    "code_after",
)

# Research fields the archive cannot supply; the annotation overlay must.
# ``file_path`` is an annotation field for the same reason (D1).
ANNOTATION_REQUIRED_FIELDS = (
    "file_path",
    "category",
    "severity",
    "confidence",
    "reviewer",
)
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
    """One mapped raw-schema record plus its exclusion reason (None = included).

    ``review_id`` is the PR-level grouping identity; ``annotation_key`` is the
    per-comment key the overlay is written against (see module docstring).
    """

    record: dict[str, Any]
    exclusion_reason: str | None
    review_id: str
    annotation_key: str


def ingest(
    archive: Sequence[Mapping[str, Any]],
    *,
    annotation: Sequence[Mapping[str, Any]] | None = None,
) -> list[IngestRecord]:
    """Map every archive record, splicing the annotation overlay when given."""
    overlay = _index_overlay(annotation)
    results: list[IngestRecord] = []
    for entry in archive:
        record, reason, key = _map_record(entry, overlay)
        results.append(IngestRecord(record, reason, str(record["review_id"]), key))
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


def _archive_field(entry: Mapping[str, Any], raw_name: str) -> Any | None:
    """First non-empty value among the accepted spellings of ``raw_name``."""
    for name in ARCHIVE_ALIASES[raw_name]:
        value = entry.get(name)
        if value is not None and value != "":
            return value
    return None


def _map_record(
    entry: Mapping[str, Any],
    overlay: Mapping[str, Mapping[str, Any]],
) -> tuple[dict[str, Any], str | None, str]:
    missing = [
        name
        for name in REQUIRED_ARCHIVE_FIELDS
        if _archive_field(entry, name) is None
    ]
    if missing:
        raise IngestError(
            f"archive record missing required fields: {', '.join(missing)}"
        )

    pr_id = review_id(entry)
    key = annotation_key(entry)
    annotation_for = overlay.get(key)
    if not overlay:
        return _partial(pr_id, key, entry), EXCLUSION_ANNOTATION_REQUIRED, key
    if annotation_for is None:
        return _partial(pr_id, key, entry), EXCLUSION_ANNOTATION_MISSING, key

    record = _partial(pr_id, key, entry)
    _apply_annotation(record, annotation_for)
    reason = _missing_annotation(record)
    if reason is not None:
        return record, reason, key
    return record, None, key


def review_id(entry: Mapping[str, Any]) -> str:
    """PR-level review identity (D2): ``{repo}#{pr_number}``.

    PR numbering is per repository, so the repo qualifier keeps grouping keys
    collision-free. Several comments of one PR share this id — which is exactly
    what lets ``corpus._group_key`` form multi-comment redundancy groups. The
    per-comment overlay key is :func:`annotation_key`.

    Public because the annotation aid samples PRs with the same identity.
    """
    repo = _archive_field(entry, "repo")
    pr = _archive_field(entry, "pr_number")
    return f"{repo}#{pr}"


def annotation_key(entry: Mapping[str, Any]) -> str:
    """Per-comment overlay key: deterministic, so reruns and annotators agree.

    Prefers the archive's own ``comment_id`` when present; otherwise a sha256
    over ``repo|pr_number|record_id|comment``, where ``record_id`` is the
    archive's ``ids[0]``. ``file_path`` is deliberately **not** hashed: the
    published archive carries no path and the path is an annotation field (D1),
    so hashing it would make the key uncomputable before annotation.
    """
    explicit = entry.get("comment_id")
    if explicit:
        return str(explicit)
    ids = entry.get("ids")
    record_id = str(ids[0]) if isinstance(ids, (list, tuple)) and ids else ""
    digest = hashlib.sha256(
        "|".join(
            (
                str(_archive_field(entry, "repo")),
                str(_archive_field(entry, "pr_number")),
                record_id,
                str(_archive_field(entry, "review_comment")),
            )
        ).encode("utf-8")
    ).hexdigest()
    return f"code-review:{digest}"


def _partial(review_id: str, key: str, entry: Mapping[str, Any]) -> dict[str, Any]:
    """Mechanical-only mapping (no interpretation, no invented annotation)."""
    memory = entry.get("memory")
    if memory is not None and not isinstance(memory, dict):
        raise IngestError("archive 'memory' must be a JSON object when present")
    before = str(_archive_field(entry, "code_before"))
    after = str(_archive_field(entry, "code_after"))
    line_start, line_end = _lines(entry)
    provenance: dict[str, Any] = {
        "source_archive": ARCHIVE_DOI,
        "annotation_key": key,
        "record_hash": _content_hash(review_id, entry),
    }
    if entry.get("ids") is not None:
        # archive provenance: [record_id, sha_before, sha_after]
        provenance["archive_ids"] = entry["ids"]
    return {
        "review_id": review_id,
        "repo": str(_archive_field(entry, "repo")),
        "file_path": _archive_field(entry, "file_path"),
        "line_start": line_start,
        "line_end": line_end,
        "category": entry.get("category"),
        "severity": entry.get("severity"),
        "confidence": entry.get("confidence"),
        "reviewer": entry.get("reviewer_username") or entry.get("reviewer"),
        "round": _optional_int(entry.get("round"), default=1),
        "comment": str(_archive_field(entry, "review_comment")),
        "suggested_fix": entry.get("suggested_fix"),
        "actionable": _optional_bool(entry.get("actionable")),
        "is_review_comment": True,
        "related_to_feedback": True,
        "implemented_later": before != after,
        "implementation": entry.get("implementation"),
        "explicit_outcome": entry.get("explicit_outcome"),
        "memory": memory or {},
        "provenance": provenance,
    }


_HUNK_HEADER = re.compile(r"^@@ -(\d+)(?:,(\d+))? \+(\d+)(?:,\d+)? @@")


def _lines(entry: Mapping[str, Any]) -> tuple[int | None, int | None]:
    """Explicit line fields when given, else the **old-side** range of ``hunk``.

    Evidence (datasets/DATASET_MAPPING_PROPOSAL.md §1, 5,000 rows): marker-
    stripped ``old`` equals the old side of ``hunk`` in 4,982/5,000 rows, so
    ``hunk`` is the refinement change (C1 -> C2) and its ``-`` range is where
    the reviewer was looking (the pre-refinement code). When neither source
    exists the fields stay ``None`` and ``corpus._group_key`` buckets them as
    ``"none"``.
    """
    line_start = entry.get("line_start")
    line_end = entry.get("line_end")
    derived_start: int | None = None
    derived_end: int | None = None
    if line_start is None or line_end is None:
        derived_start, derived_end = _hunk_lines(entry.get("hunk"))
    start = line_start if line_start is not None else derived_start
    end = line_end if line_end is not None else derived_end
    return _optional_int(start, default=None), _optional_int(end, default=None)


def _hunk_lines(hunk: object) -> tuple[int | None, int | None]:
    """Parse ``@@ -a,b +c,d @@`` → old-side line range ``a .. a+b-1``.

    A pure insertion (``b`` absent or 0) has no old-side span; the anchor line
    ``a`` is used so ``line_end >= line_start`` still holds.
    """
    if not isinstance(hunk, str):
        return None, None
    match = _HUNK_HEADER.match(hunk.split("\n", 1)[0])
    if not match:
        return None, None
    start = int(match.group(1))
    length = int(match.group(2) or 1)
    return start, (start + length - 1) if length else start


def _apply_annotation(
    record: dict[str, Any],
    overlay_entry: Mapping[str, Any],
) -> None:
    for key in (
        "file_path",
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
            str(_archive_field(entry, "file_path") or ""),
            str(_archive_field(entry, "review_comment") or ""),
            str(_archive_field(entry, "code_before") or ""),
            str(_archive_field(entry, "code_after") or ""),
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
    annotation_provenance: Mapping[str, Any] | None = None,
) -> None:
    """Write included raw JSONL, excluded JSONL (with reason), and the manifest.

    ``annotation_provenance`` records *who/what* produced the overlay (annotator
    identity, rubric file, evidence file) — methods-provenance only; it never
    affects mapping, labelling or exclusion.
    """
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
            **(
                {"annotation_provenance": dict(annotation_provenance)}
                if annotation_provenance
                else {}
            ),
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
