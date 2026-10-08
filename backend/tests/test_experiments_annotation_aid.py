"""Annotation-aid tests (``experiments/ingest/annotation_aid.py``).

The aid is the tool the annotation pass will actually run against, so it is
gated on the properties that make it trustworthy: outputs are mechanical only
(every judgement field null), keys come from ``ingest.annotation_key`` (one
source of truth), PR-cluster sampling keeps a PR's comments together, and the
same source/seed/mode/size reproduces the outputs byte for byte.
"""

from __future__ import annotations

import json
import subprocess
import sys
from pathlib import Path

from app.experiments.ingest import annotation_key

_AID = (
    Path(__file__).resolve().parents[2] / "experiments" / "ingest" / "annotation_aid.py"
)
_JUDGEMENT_FIELDS = (
    "file_path",
    "category",
    "severity",
    "confidence",
    "reviewer",
    "round",
    "implementation",
    "explicit_outcome",
    "actionable",
    "suggested_fix",
    "memory",
)


def _row(repo: str, ghid: int, record_id: int, comment: str) -> dict[str, object]:
    return {
        "repo": repo,
        "ghid": ghid,
        "ids": [record_id, "a" * 40, "b" * 40],
        "lang": "py",
        "comment": comment,
        "old": "x = compute()",
        "new": "x = compute()  # reviewed",
        "old_hunk": "@@ -10,4 +10,5 @@ def f():",
        "hunk": "@@ -10,4 +10,5 @@ def f():",
        "oldf": "def f(): ...",
    }


def _archive() -> list[dict[str, object]]:
    """Two single-comment PRs plus one PR holding two comments."""
    return [
        _row("o/a", 1, 11, "Rename the local."),
        _row("o/a", 2, 12, "Drop the dead branch."),
        _row("o/b", 7, 13, "Guard the null case."),
        _row("o/b", 7, 14, "Add a regression test."),
        _row("o/c", 3, 15, "Extract the helper."),
    ]


def _run(tmp_path: Path, archive: list[dict[str, object]], *extra: str) -> Path:
    tmp_path.mkdir(parents=True, exist_ok=True)
    src = tmp_path / "archive.jsonl"
    src.write_text("\n".join(json.dumps(r) for r in archive), encoding="utf-8")
    out_dir = tmp_path / "aid"
    result = subprocess.run(
        [
            sys.executable,
            str(_AID),
            str(src),
            "--out-dir",
            str(out_dir),
            "--seed",
            "0",
            *extra,
        ],
        capture_output=True,
        text=True,
        timeout=120,
        cwd=Path(__file__).resolve().parents[1],
    )
    assert result.returncode == 0, result.stderr
    return out_dir


def _read_jsonl(path: Path) -> list[dict[str, object]]:
    return [
        json.loads(line)
        for line in path.read_text(encoding="utf-8").splitlines()
        if line
    ]


def test_aid_outputs_are_mechanical_only(tmp_path: Path) -> None:
    out = _run(tmp_path, _archive(), "--sample", "5")

    draft = _read_jsonl(out / "annotation_draft.jsonl")
    required = _read_jsonl(out / "annotation_required.jsonl")
    sample = _read_jsonl(out / "sample.jsonl")
    manifest = json.loads((out / "aid_manifest.json").read_text(encoding="utf-8"))

    assert len(sample) == len(draft) == len(required) == 5

    # nothing is prefilled: every judgement field is null
    for row in draft:
        assert set(row) == {"review_id", *_JUDGEMENT_FIELDS}
        assert all(row[field] is None for field in _JUDGEMENT_FIELDS)

    # one source of truth for keys: ingest.annotation_key
    keys = [row["review_id"] for row in draft]
    assert len(set(keys)) == len(keys)  # no comment can overwrite another
    assert set(keys) == {annotation_key(row) for row in sample}
    assert set(keys) == {row["review_id"] for row in required}

    # context rows give the annotator a real target, verbatim
    for row in required:
        assert row["pr_url"].startswith("https://github.com/")
        assert row["comment"] and row["code_before"] and row["hunk"]

    assert manifest["sampling"]["mode"] == "prs"
    assert manifest["sampling"]["seed"] == 0
    assert manifest["sampling"]["archive_rows"] == 5
    assert manifest["counts"]["annotation_required"] == 5
    assert manifest["integrity"], "an explicit integrity statement is required"


def test_aid_pr_cluster_keeps_a_prs_comments_together(tmp_path: Path) -> None:
    # ask for fewer rows than the archive holds: only some PRs are taken, but a
    # selected PR must arrive complete (that is what makes groups formable)
    out = _run(tmp_path, _archive(), "--sample", "3", "--sample-mode", "prs")

    required = _read_jsonl(out / "annotation_required.jsonl")
    prs: dict[str, int] = {}
    for row in required:
        prs[row["pr_review_id"]] = prs.get(row["pr_review_id"], 0) + 1

    assert sum(prs.values()) >= 3  # target is a floor, not a trim
    # o/b holds two comments: both or neither
    assert prs.get("o/b#7", 0) in (0, 2)


def test_aid_is_deterministic_for_the_same_seed(tmp_path: Path) -> None:
    first = _run(tmp_path / "one", _archive(), "--sample", "4")
    second = _run(tmp_path / "two", _archive(), "--sample", "4")

    for name in (
        "sample.jsonl",
        "annotation_required.jsonl",
        "annotation_draft.jsonl",
    ):
        assert (first / name).read_bytes() == (second / name).read_bytes(), name

    # the manifest differs only in where the source happened to live
    a = json.loads((first / "aid_manifest.json").read_text(encoding="utf-8"))
    b = json.loads((second / "aid_manifest.json").read_text(encoding="utf-8"))
    a["source"].pop("path")
    b["source"].pop("path")
    assert a == b
