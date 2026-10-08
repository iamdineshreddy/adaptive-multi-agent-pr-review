"""Build the annotation aid for a CodeReview archive (seeded sample).

Three outputs, all mechanical — **no judgement is ever prefilled**:

1. ``sample.jsonl``       the seeded sample of the archive (the candidate pool
                          the research corpus is drawn from), by default a
                          **PR-cluster** sample: PR ids are shuffled with the
                          seed and taken until at least ``--sample`` rows are
                          collected, then all of those PRs' comments are
                          emitted in source-file order. Row-level reservoir
                          sampling remains available via ``--sample-mode rows``.
                          PR clustering preserves multi-comment PRs, which is
                          what makes redundancy grouping (``corpus._group_key``)
                          measurable at all.
2. ``annotation_required.jsonl``
                          one row per comment the overlay must cover, with the
                          context an annotator needs: the per-comment
                          ``review_id`` (the archive ``annotation_key``), repo,
                          PR link, comment, hunk, before/after region and the
                          archive ids.
3. ``annotation_draft.jsonl``
                          overlay skeleton keyed by that same id with **every**
                          judgement field set to ``null`` for the annotator to
                          fill (protocol: ``datasets/README.md``).

Plus ``aid_manifest.json`` recording source sha256, seed, sample mode/size,
counts and an explicit integrity statement. Outputs are gitignored
(``datasets/annotation/``) because they contain archive text.

Usage:
    python experiments/ingest/annotation_aid.py ref-test.jsonl \
        --out-dir datasets/annotation --sample 500 --seed 0
"""

from __future__ import annotations

import argparse
import hashlib
import json
import random
import sys
from pathlib import Path
from typing import Any

sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "backend"))

from app.experiments.ingest import (  # noqa: E402
    EXCLUSION_ANNOTATION_REQUIRED,
    ingest,
    review_id,
)

# Judgement fields the annotator must supply; left null on purpose.
DRAFT_JUDGEMENT_FIELDS = (
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

INTEGRITY = (
    "every judgement field in annotation_draft.jsonl is null: this tool "
    "prefills nothing (file_path, category, severity, confidence, reviewer, "
    "round, implementation, explicit_outcome, actionable, suggested_fix, memory)",
    "annotation_required.jsonl rows carry archive text verbatim; no text is "
    "rewritten, truncated or summarised here",
    "sample.jsonl is a seeded PR-cluster sample (all comments of the selected "
    "PRs, in source-file order); the same source, seed, mode and sample size "
    "reproduce it byte for byte",
    "records the overlay does not fully cover stay excluded with a "
    "machine-readable reason after pass 2 (datasets/README.md)",
)


def _sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as fh:
        for chunk in iter(lambda: fh.read(1 << 20), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _iter_rows(path: Path):
    """Yield archive rows in file order, streaming (archives reach 5+ GB)."""
    with path.open(encoding="utf-8") as fh:
        for line_no, line in enumerate(fh, start=1):
            line = line.strip()
            if not line:
                continue
            try:
                yield json.loads(line)
            except json.JSONDecodeError as exc:
                raise SystemExit(f"{path}:{line_no}: invalid JSON: {exc}") from exc


def _reservoir(
    rows: Any, k: int, seed: int
) -> tuple[list[dict[str, Any]], int]:
    """Seeded reservoir sample over rows in file order (Algorithm R).

    Streams: only ``k`` rows are retained, so the source is never held in
    memory. Returns ``(sample, rows_scanned)``.
    """
    rng = random.Random(seed)
    sample: list[dict[str, Any]] = []
    scanned = 0
    for index, row in enumerate(rows):
        scanned = index + 1
        if index < k:
            sample.append(row)
            continue
        swap = rng.randint(0, index)
        if swap < k:
            sample[swap] = row
    return sample, scanned


def _pr_index(path: Path) -> tuple[list[str], dict[str, int], int]:
    """Distinct PR ids in file order, their row counts, and rows scanned.

    One streaming pass over the archive; only ids are held in memory.
    """
    order: list[str] = []
    counts: dict[str, int] = {}
    scanned = 0
    for row in _iter_rows(path):
        scanned += 1
        rid = review_id(row)
        if rid not in counts:
            order.append(rid)
            counts[rid] = 0
        counts[rid] += 1
    return order, counts, scanned


def _cluster_sample(
    order: list[str], counts: dict[str, int], target: int, seed: int
) -> tuple[list[str], int]:
    """Seeded PR-cluster selection: shuffle PR ids, take until >= ``target`` rows.

    Returns ``(chosen_pr_ids, rows_they_hold)``. Clustering keeps every comment
    of a selected PR, so multi-comment PRs — the only place redundancy groups
    can form — survive sampling.
    """
    rng = random.Random(seed)
    shuffled = list(order)
    rng.shuffle(shuffled)
    chosen: list[str] = []
    rows = 0
    for rid in shuffled:
        if rows >= target:
            break
        chosen.append(rid)
        rows += counts[rid]
    return chosen, rows


def _pr_url(repo: Any, pr: Any) -> str:
    return f"https://github.com/{repo}/pull/{pr}"


def _context_row(item: Any, entry: dict[str, Any]) -> dict[str, Any]:
    """Archive context for one comment — mechanical fields only."""
    record = item.record
    pr = entry.get("pr_number", entry.get("ghid"))
    return {
        "review_id": item.annotation_key,
        "pr_review_id": item.review_id,
        "repo": record["repo"],
        "pr_number": pr,
        "pr_url": _pr_url(record["repo"], pr),
        "language": entry.get("lang", entry.get("language")),
        "comment": record["comment"],
        "line_start": record["line_start"],
        "line_end": record["line_end"],
        "hunk": entry.get("hunk"),
        "old_hunk": entry.get("old_hunk"),
        "code_before": entry.get("old", entry.get("code_before")),
        "code_after": entry.get("new", entry.get("code_after")),
        "archive_ids": entry.get("ids"),
        "archive_comment_id": entry.get("comment_id"),
    }


def _draft_row(item: Any) -> dict[str, Any]:
    draft: dict[str, Any] = {"review_id": item.annotation_key}
    for field in DRAFT_JUDGEMENT_FIELDS:
        draft[field] = None
    return draft


def _write_jsonl(path: Path, rows: list[dict[str, Any]]) -> None:
    with path.open("w", encoding="utf-8", newline="\n") as fh:
        for row in rows:
            fh.write(json.dumps(row, ensure_ascii=False) + "\n")


def _main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "archive",
        type=Path,
        help="CodeReview archive JSONL (Zenodo 6900648, cf. datasets/README.md)",
    )
    parser.add_argument(
        "--out-dir",
        type=Path,
        default=Path("datasets/annotation"),
        help="output directory (default: datasets/annotation)",
    )
    parser.add_argument(
        "--sample",
        type=int,
        default=500,
        help="rows to sample for annotation (default: 500)",
    )
    parser.add_argument(
        "--sample-mode",
        choices=("prs", "rows"),
        default="prs",
        help="'prs' = seeded PR-cluster sample, all comments of the selected "
        "PRs (default; preserves multi-comment PRs so redundancy groups can "
        "form); 'rows' = seeded row-level reservoir sample",
    )
    parser.add_argument(
        "--seed",
        type=int,
        default=0,
        help="sampling seed; same seed + source + mode + size = same sample "
        "(default: 0)",
    )
    args = parser.parse_args(argv)

    if args.sample <= 0:
        raise SystemExit("--sample must be a positive integer")
    if not args.archive.is_file():
        raise SystemExit(f"archive not found: {args.archive}")

    source_hash = _sha256_file(args.archive)
    if args.sample_mode == "prs":
        order, counts, scanned = _pr_index(args.archive)
        chosen, _ = _cluster_sample(order, counts, args.sample, args.seed)
        chosen_set = set(chosen)
        sample = [
            row for row in _iter_rows(args.archive) if review_id(row) in chosen_set
        ]
        algorithm = (
            "PR-cluster: distinct PR ids shuffled with the seed and taken until "
            ">= --sample rows; every comment of those PRs is kept, emitted in "
            "source-file order"
        )
    else:
        sample, scanned = _reservoir(_iter_rows(args.archive), args.sample, args.seed)
        algorithm = "row-level reservoir sampling (Algorithm R) over source-file order"
    sampled_prs = len({review_id(row) for row in sample})

    items = ingest(sample)  # pass 1: mechanical only, no overlay
    required = [
        (item, entry)
        for item, entry in zip(items, sample, strict=True)
        if item.exclusion_reason == EXCLUSION_ANNOTATION_REQUIRED
    ]

    out_dir = args.out_dir
    out_dir.mkdir(parents=True, exist_ok=True)
    sample_path = out_dir / "sample.jsonl"
    required_path = out_dir / "annotation_required.jsonl"
    draft_path = out_dir / "annotation_draft.jsonl"
    manifest_path = out_dir / "aid_manifest.json"

    _write_jsonl(sample_path, sample)
    _write_jsonl(
        required_path, [_context_row(item, entry) for item, entry in required]
    )
    _write_jsonl(draft_path, [_draft_row(item) for item, _ in required])

    manifest = {
        "tool": "experiments/ingest/annotation_aid.py",
        "source": {
            "path": str(args.archive),
            "bytes": args.archive.stat().st_size,
            "sha256": source_hash,
            "doi": "10.5281/zenodo.6900648",
            "license": "CC-BY-4.0",
        },
        "sampling": {
            "mode": args.sample_mode,
            "algorithm": algorithm,
            "seed": args.seed,
            "requested_rows": args.sample,
            "archive_rows": scanned,
            "sampled_rows": len(sample),
            "sampled_prs": sampled_prs,
        },
        "counts": {
            "annotation_required": len(required),
            "distinct_prs": len({item.review_id for item, _ in required}),
        },
        # Reference material for the annotator (values, not judgements):
        # nothing here is written into annotation_draft.jsonl.
        "annotation_reference": {
            "reviewer_values": [
                "security",
                "quality",
                "performance",
                "architecture",
                "standards",
            ],
            "reviewer_meaning": "which of the five agents would emit this "
            "finding (decision D5); not the annotator's identity",
            "category_taxonomy": "docs/AGENTS.md §1 (ARUM agent-category "
            "taxonomy)",
            "protocol": "datasets/README.md (overlay fields + rules)",
            "outcome_evidence": "implementation/explicit_outcome are "
            "annotations verified by the annotator, never inferred from the "
            "changed pair (docs/Auth.md)",
        },
        "outputs": {
            "sample.jsonl": _sha256_file(sample_path),
            "annotation_required.jsonl": _sha256_file(required_path),
            "annotation_draft.jsonl": _sha256_file(draft_path),
        },
        "integrity": list(INTEGRITY),
    }
    manifest_path.write_text(
        json.dumps(manifest, indent=2, sort_keys=True) + "\n", encoding="utf-8"
    )

    print(f"source: {args.archive} ({scanned} rows, sha256 {source_hash[:16]}…)")
    print(
        f"sample: {len(sample)} rows from {sampled_prs} PRs "
        f"({args.sample_mode} mode, seed {args.seed}, requested {args.sample})"
    )
    print(
        f"annotation_required: {len(required)} comments across "
        f"{manifest['counts']['distinct_prs']} PRs"
    )
    for path in (sample_path, required_path, draft_path, manifest_path):
        print(f"wrote {path}")
    return 0


if __name__ == "__main__":
    raise SystemExit(_main())
