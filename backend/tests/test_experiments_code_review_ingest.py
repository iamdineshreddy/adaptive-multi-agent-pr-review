"""Ingest mapper tests for the CodeReview archive → raw corpus step (Phase 17 part 2).

Covers the honest splice: mechanical archive fields + annotation overlay under
``datasets/README.md``, deterministic review ids, and exclusion-with-reason for
every record the overlay does not fully cover (nothing is inferred). Runs the
thin CLI end-to-end once via subprocess so the wiring is exercised too.
"""

from __future__ import annotations

import json
import subprocess
import sys
from pathlib import Path

import pytest

from app.experiments.corpus import build_corpus
from app.experiments.ingest import (
    EXCLUSION_ANNOTATION_INCOMPLETE,
    EXCLUSION_ANNOTATION_MISSING,
    EXCLUSION_ANNOTATION_REQUIRED,
    IngestError,
    ingest,
    render_preview,
    write_ingest_outputs,
)
from app.experiments.io import read_corpus_jsonl

_REPO = {"repo": "owner/demo", "pr_number": 42}


def _archive_record(
    number: int,
    *,
    comment_id: str | None = None,
    before: str = "a = 1",
    after: str = "a = 2",
    unchanged: bool = False,
) -> dict[str, object]:
    record: dict[str, object] = {
        **_REPO,
        "file_path": "src/demo.py",
        "review_comment": f"Use a named constant ({number}).",
        "code_before": before if not unchanged else after,
        "code_after": after,
        "language": "python",
        "comment_id": comment_id,
    }
    if comment_id is None:
        del record["comment_id"]
    return record


def _annotation(review_id: str, **fields: object) -> dict[str, object]:
    base: dict[str, object] = {
        "review_id": review_id,
        "category": "quality/maintainability",
        "severity": "medium",
        "confidence": 0.9,
        "reviewer": "annotator",
    }
    base.update(fields)
    return base


def _ingest(
    records: list[dict[str, object]],
    annotation: list[dict[str, object]] | None = None,
) -> list[object]:
    return ingest(records, annotation=annotation)  # type: ignore[arg-type]


def test_mechanical_mapping_without_overlay_excludes_all() -> None:
    records = _ingest([_archive_record(1)])
    assert len(records) == 1
    item = records[0]
    assert item.exclusion_reason == EXCLUSION_ANNOTATION_REQUIRED
    record = item.record
    assert record["file_path"] == "src/demo.py"
    assert record["comment"].startswith("Use a named constant")
    assert record["implemented_later"] is True
    assert record["is_review_comment"] is True
    assert record["related_to_feedback"] is True
    assert record["round"] == 1
    assert record["memory"] == {}
    assert record["provenance"]["source_archive"] == "10.5281/zenodo.6900648"


def test_review_id_is_deterministic_and_uses_comment_id() -> None:
    a = _ingest([_archive_record(1)])
    b = _ingest([_archive_record(1)])
    assert a[0].review_id == b[0].review_id
    assert a[0].review_id.startswith("code-review:")

    with_id = _ingest([_archive_record(1, comment_id="comment-999")])
    assert with_id[0].review_id == "comment-999"


def test_unchanged_pair_is_not_implementation_evidence() -> None:
    item = _ingest([_archive_record(1, unchanged=True)])[0]
    assert item.record["implemented_later"] is False


def test_overlay_splice_includes_and_labels_fixed() -> None:
    archive = [_archive_record(1)]
    review_id = _ingest(archive)[0].review_id
    annotation = [_annotation(review_id, implementation="verbatim")]
    item = _ingest(archive, annotation)[0]
    assert item.exclusion_reason is None
    record = item.record
    assert record["category"] == "quality/maintainability"
    assert record["severity"] == "medium"
    assert record["confidence"] == 0.9
    assert record["reviewer"] == "annotator"
    assert record["implementation"] == "verbatim"

    preview = render_preview([item], source="raw.jsonl")
    assert preview["included"] == 1
    assert preview["label_counts"] == {"FIXED": 1}


def test_overlay_partial_implementation_labels_modified() -> None:
    archive = [_archive_record(2)]
    review_id = _ingest(archive)[0].review_id
    item = _ingest(archive, [_annotation(review_id, implementation="partial")])[0]
    assert item.exclusion_reason is None
    preview = render_preview([item], source="raw.jsonl")
    assert preview["label_counts"] == {"MODIFIED": 1}


def test_explicit_outcome_wins_over_implementation_evidence() -> None:
    archive = [_archive_record(3)]
    review_id = _ingest(archive)[0].review_id
    item = _ingest(
        archive,
        [
            _annotation(
                review_id,
                implementation="verbatim",
                explicit_outcome="dismissed",
            )
        ],
    )[0]
    assert item.exclusion_reason is None
    preview = render_preview([item], source="raw.jsonl")
    assert preview["label_counts"] == {"DISMISSED": 1}


def test_missing_overlay_entry_and_incomplete_overlay_excluded() -> None:
    archive = [_archive_record(1), _archive_record(2)]
    ids = [item.review_id for item in _ingest(archive)]
    partial = _annotation(ids[0])
    del partial["category"]
    items = _ingest(archive, [partial])
    by_id = {item.review_id: item for item in items}
    assert by_id[ids[0]].exclusion_reason == (
        f"{EXCLUSION_ANNOTATION_INCOMPLETE}:category"
    )
    assert by_id[ids[1]].exclusion_reason == EXCLUSION_ANNOTATION_MISSING


def test_out_of_range_confidence_excluded() -> None:
    archive = [_archive_record(4)]
    review_id = _ingest(archive)[0].review_id
    item = _ingest(archive, [_annotation(review_id, confidence=1.7)])[0]
    assert item.exclusion_reason == f"{EXCLUSION_ANNOTATION_INCOMPLETE}:confidence"


def test_invalid_implementation_raises() -> None:
    archive = [_archive_record(5)]
    review_id = _ingest(archive)[0].review_id
    with pytest.raises(IngestError, match="implementation"):
        _ingest(archive, [_annotation(review_id, implementation="sometimes")])


def test_invalid_explicit_outcome_raises() -> None:
    archive = [_archive_record(6)]
    review_id = _ingest(archive)[0].review_id
    with pytest.raises(IngestError, match="explicit_outcome"):
        _ingest(archive, [_annotation(review_id, explicit_outcome="maybe")])


def test_non_dict_memory_raises() -> None:
    bad = {**_archive_record(7), "memory": ["not", "a", "dict"]}
    with pytest.raises(IngestError, match="memory"):
        _ingest([bad])


def test_missing_archive_field_raises() -> None:
    bad = dict(_archive_record(8))
    del bad["file_path"]
    with pytest.raises(IngestError, match="file_path"):
        _ingest([bad])


def test_write_ingest_outputs_end_to_end(tmp_path: Path) -> None:
    archive = [_archive_record(1), _archive_record(2)]
    ids = [item.review_id for item in _ingest(archive)]
    annotation = [
        _annotation(ids[0], implementation="verbatim"),
        _annotation(ids[1]),
    ]
    records = ingest(archive, annotation=annotation)
    archive_path = tmp_path / "archive.jsonl"
    archive_path.write_text("\n".join(json.dumps(r) for r in archive), encoding="utf-8")
    out, excluded, manifest = (
        tmp_path / "raw.jsonl",
        tmp_path / "excluded.jsonl",
        tmp_path / "manifest.json",
    )
    write_ingest_outputs(
        records,
        out=out,
        excluded=excluded,
        manifest=manifest,
        source_archive=str(archive_path),
        annotation_path="annotation.jsonl",
    )

    raw = read_corpus_jsonl(out)
    assert len(raw) == 2
    assert raw[0]["implementation"] == "verbatim"
    excluded_records = read_corpus_jsonl(excluded)
    assert excluded_records == []
    payload = json.loads(manifest.read_text(encoding="utf-8"))
    assert payload["counts"] == {"total": 2, "included": 2, "excluded": 0}
    assert payload["dataset"]["doi"] == "10.5281/zenodo.6900648"
    assert payload["dataset"]["license"] == "CC-BY-4.0"
    assert payload["labelled_preview"]["records"] == 2
    assert payload["labelled_preview"]["included"] == 1

    corpus = build_corpus(raw, source=str(out))
    assert corpus.included_count == 1


def test_cli_end_to_end_subprocess(tmp_path: Path) -> None:
    archive = [_archive_record(1)]
    review_id = _ingest(archive)[0].review_id
    archive_path = tmp_path / "archive.jsonl"
    archive_path.write_text(json.dumps(archive[0]), encoding="utf-8")
    annotation_path = tmp_path / "annotation.jsonl"
    annotation_path.write_text(
        json.dumps(_annotation(review_id, implementation="verbatim")),
        encoding="utf-8",
    )
    out = tmp_path / "raw.jsonl"
    excluded = tmp_path / "excluded.jsonl"
    manifest = tmp_path / "manifest.json"

    cli = (
        Path(__file__).resolve().parents[2]
        / "experiments"
        / "ingest"
        / "code_review_ingest.py"
    )
    result = subprocess.run(
        [
            sys.executable,
            str(cli),
            str(archive_path),
            "--annotation",
            str(annotation_path),
            "--out",
            str(out),
            "--excluded",
            str(excluded),
            "--manifest",
            str(manifest),
        ],
        capture_output=True,
        text=True,
        timeout=120,
        cwd=Path(__file__).resolve().parents[1],
    )
    assert result.returncode == 0, result.stderr
    assert "included: 1" in result.stdout
    raw = read_corpus_jsonl(out)
    assert len(raw) == 1
    payload = json.loads(manifest.read_text(encoding="utf-8"))
    assert payload["counts"]["included"] == 1
