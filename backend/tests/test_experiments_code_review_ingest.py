"""Ingest mapper tests for the CodeReview archive → raw corpus step (Phase 17 part 2).

Covers the honest splice: mechanical archive fields + annotation overlay under
``datasets/README.md``, the two identities (PR-level ``review_id`` for grouping,
per-comment ``annotation_key`` for the overlay), and exclusion-with-reason for
every record the overlay does not fully cover (nothing is inferred). Both the
published archive spellings (``ghid``/``comment``/``old``/``new``) and the
bundled smoke fixture's raw-schema names are exercised. Runs the thin CLI
end-to-end once via subprocess so the wiring is exercised too.
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


def _published_record() -> dict[str, object]:
    """A record exactly as the Zenodo CodeReview archive publishes it."""
    return {
        "repo": "spotify/luigi",
        "ghid": 2789,
        "ids": [65574, "a" * 40, "b" * 40],
        "lang": "py",
        "comment": "Hoist the repeated URL constant instead of inlining it.",
        "old": "endpoint = f'{base}/users'",
        "new": "BASE = base\nendpoint = f'{BASE}/users'",
        "old_hunk": "@@ -10,3 +10,5 @@ def handler():",
        "hunk": "@@ -12,4 +12,6 @@ def handler():",
        "oldf": "def handler(): ...",
    }


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
    assert record["repo"] == "owner/demo"
    assert record["comment"].startswith("Use a named constant")
    assert record["implemented_later"] is True
    assert record["is_review_comment"] is True
    assert record["related_to_feedback"] is True
    assert record["round"] == 1
    assert record["memory"] == {}
    assert record["provenance"]["source_archive"] == "10.5281/zenodo.6900648"
    assert record["provenance"]["annotation_key"] == item.annotation_key


def test_review_id_is_pr_level_and_shared_by_a_prs_comments() -> None:
    """D2: grouping identity is ``{repo}#{pr}``, shared by one PR's comments."""
    a = _ingest([_archive_record(1)])
    b = _ingest([_archive_record(1)])
    assert a[0].review_id == b[0].review_id
    assert a[0].review_id == "owner/demo#42"

    pair = _ingest([_archive_record(1), _archive_record(2)])
    assert pair[0].review_id == pair[1].review_id == "owner/demo#42"


def test_annotation_key_is_per_comment_and_deterministic() -> None:
    """The overlay key is per comment, so comments cannot overwrite each other."""
    a = _ingest([_archive_record(1)])
    b = _ingest([_archive_record(1)])
    assert a[0].annotation_key == b[0].annotation_key
    assert a[0].annotation_key.startswith("code-review:")

    pair = _ingest([_archive_record(1), _archive_record(2)])
    assert pair[0].annotation_key != pair[1].annotation_key

    with_id = _ingest([_archive_record(1, comment_id="comment-999")])
    assert with_id[0].annotation_key == "comment-999"


def test_published_archive_spellings_are_accepted_as_published() -> None:
    item = _ingest([_published_record()])[0]
    record = item.record
    assert item.review_id == "spotify/luigi#2789"
    assert item.exclusion_reason == EXCLUSION_ANNOTATION_REQUIRED
    assert record["comment"].startswith("Hoist")
    assert record["implemented_later"] is True
    # the published archive carries no path: nothing is invented (D1)
    assert record["file_path"] is None
    # old-side hunk range is read mechanically: -12,4 -> 12..15
    assert record["line_start"] == 12
    assert record["line_end"] == 15
    assert record["provenance"]["archive_ids"] == [65574, "a" * 40, "b" * 40]
    assert record["provenance"]["annotation_key"] == item.annotation_key


def test_explicit_line_fields_win_over_the_hunk_header() -> None:
    entry = {**_published_record(), "line_start": 5, "line_end": 9}
    record = _ingest([entry])[0].record
    assert (record["line_start"], record["line_end"]) == (5, 9)


def test_unparseable_hunk_leaves_lines_unset() -> None:
    entry = {**_published_record(), "hunk": "no hunk header here"}
    record = _ingest([entry])[0].record
    assert record["line_start"] is None
    assert record["line_end"] is None


def test_file_path_is_not_required_from_the_archive() -> None:
    """D1: the path is an annotation field; the archive simply has none."""
    legacy = dict(_archive_record(9))
    del legacy["file_path"]
    item = _ingest([legacy])[0]
    assert item.record["file_path"] is None
    assert item.exclusion_reason == EXCLUSION_ANNOTATION_REQUIRED


def test_overlay_supplies_file_path_when_the_archive_has_none() -> None:
    entry = _published_record()
    key = _ingest([entry])[0].annotation_key
    annotation = [
        _annotation(key, implementation="verbatim", file_path="src/config.py")
    ]
    item = _ingest([entry], annotation)[0]
    assert item.exclusion_reason is None
    assert item.record["file_path"] == "src/config.py"

    preview = render_preview([item], source="raw.jsonl")
    assert preview["included"] == 1
    assert preview["label_counts"] == {"FIXED": 1}


def test_overlay_without_file_path_is_excluded_as_incomplete() -> None:
    entry = _published_record()
    key = _ingest([entry])[0].annotation_key
    item = _ingest([entry], [_annotation(key)])[0]
    assert item.exclusion_reason == f"{EXCLUSION_ANNOTATION_INCOMPLETE}:file_path"


def test_unchanged_pair_is_not_implementation_evidence() -> None:
    item = _ingest([_archive_record(1, unchanged=True)])[0]
    assert item.record["implemented_later"] is False


def test_overlay_splice_includes_and_labels_fixed() -> None:
    archive = [_archive_record(1)]
    key = _ingest(archive)[0].annotation_key
    annotation = [_annotation(key, implementation="verbatim")]
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
    key = _ingest(archive)[0].annotation_key
    item = _ingest(archive, [_annotation(key, implementation="partial")])[0]
    assert item.exclusion_reason is None
    preview = render_preview([item], source="raw.jsonl")
    assert preview["label_counts"] == {"MODIFIED": 1}


def test_explicit_outcome_wins_over_implementation_evidence() -> None:
    archive = [_archive_record(3)]
    key = _ingest(archive)[0].annotation_key
    item = _ingest(
        archive,
        [
            _annotation(
                key,
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
    keys = [item.annotation_key for item in _ingest(archive)]
    partial = _annotation(keys[0])
    del partial["category"]
    items = _ingest(archive, [partial])
    by_key = {item.annotation_key: item for item in items}
    assert by_key[keys[0]].exclusion_reason == (
        f"{EXCLUSION_ANNOTATION_INCOMPLETE}:category"
    )
    assert by_key[keys[1]].exclusion_reason == EXCLUSION_ANNOTATION_MISSING


def test_out_of_range_confidence_excluded() -> None:
    archive = [_archive_record(4)]
    key = _ingest(archive)[0].annotation_key
    item = _ingest(archive, [_annotation(key, confidence=1.7)])[0]
    assert item.exclusion_reason == f"{EXCLUSION_ANNOTATION_INCOMPLETE}:confidence"


def test_invalid_implementation_raises() -> None:
    archive = [_archive_record(5)]
    key = _ingest(archive)[0].annotation_key
    with pytest.raises(IngestError, match="implementation"):
        _ingest(archive, [_annotation(key, implementation="sometimes")])


def test_invalid_explicit_outcome_raises() -> None:
    archive = [_archive_record(6)]
    key = _ingest(archive)[0].annotation_key
    with pytest.raises(IngestError, match="explicit_outcome"):
        _ingest(archive, [_annotation(key, explicit_outcome="maybe")])


def test_non_dict_memory_raises() -> None:
    bad = {**_archive_record(7), "memory": ["not", "a", "dict"]}
    with pytest.raises(IngestError, match="memory"):
        _ingest([bad])


def test_missing_archive_field_raises() -> None:
    bad = dict(_archive_record(8))
    del bad["review_comment"]
    with pytest.raises(IngestError, match="review_comment"):
        _ingest([bad])

    missing_pr = dict(_archive_record(8))
    del missing_pr["pr_number"]
    with pytest.raises(IngestError, match="pr_number"):
        _ingest([missing_pr])


def test_write_ingest_outputs_end_to_end(tmp_path: Path) -> None:
    archive = [_archive_record(1), _archive_record(2)]
    keys = [item.annotation_key for item in _ingest(archive)]
    annotation = [
        _annotation(keys[0], implementation="verbatim"),
        _annotation(keys[1]),
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
    key = _ingest(archive)[0].annotation_key
    archive_path = tmp_path / "archive.jsonl"
    archive_path.write_text(json.dumps(archive[0]), encoding="utf-8")
    annotation_path = tmp_path / "annotation.jsonl"
    annotation_path.write_text(
        json.dumps(_annotation(key, implementation="verbatim")),
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
