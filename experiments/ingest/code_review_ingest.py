"""Ingest a CodeReview (CodeRefinement) archive into the raw corpus (Phase 17 part 2).

Mechanical fields are mapped from the archive; research fields
(category/severity/confidence/reviewer) and outcome evidence come **only** from
an annotation overlay keyed by the mechanical ``review_id`` (see
``datasets/README.md`` for the protocol). Records the overlay does not fully
cover are written to the excluded file with a machine-readable reason; nothing
is inferred.

Usage:
    python experiments/ingest/code_review_ingest.py archive.jsonl \\
        --out raw.jsonl --excluded excluded.jsonl --manifest manifest.json
    python experiments/ingest/code_review_ingest.py archive.jsonl \\
        --annotation annotation.jsonl --out raw.jsonl --excluded excluded.jsonl \\
        --manifest manifest.json
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path
from typing import Any

sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "backend"))

from app.experiments.ingest import (  # noqa: E402
    IngestError,
    ingest,
    write_ingest_outputs,
)
from app.experiments.io import read_corpus_jsonl  # noqa: E402


def _main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "archive",
        type=Path,
        help="CodeReview archive JSONL (Zenodo 6900648, cf. datasets/README.md)",
    )
    parser.add_argument(
        "--annotation",
        type=Path,
        help="annotation overlay JSONL keyed by review_id (protocol in datasets/README.md)",
    )
    parser.add_argument("--out", type=Path, default=Path("raw.jsonl"),
                        help="included raw JSONL (default: raw.jsonl)")
    parser.add_argument("--excluded", type=Path, default=Path("excluded.jsonl"),
                        help="excluded records JSONL (default: excluded.jsonl)")
    parser.add_argument("--manifest", type=Path, default=Path("manifest.json"),
                        help="ingest manifest JSON (default: manifest.json)")
    args = parser.parse_args(argv)

    if not args.archive.is_file():
        raise SystemExit(f"archive not found: {args.archive}")

    archive: list[dict[str, Any]] = read_corpus_jsonl(args.archive)
    annotation: list[dict[str, Any]] | None = None
    annotation_path: str | None = None
    if args.annotation is not None:
        if not args.annotation.is_file():
            raise SystemExit(f"annotation not found: {args.annotation}")
        annotation = read_corpus_jsonl(args.annotation)
        annotation_path = str(args.annotation)

    try:
        records = ingest(archive, annotation=annotation)
    except IngestError as exc:
        raise SystemExit(f"ingest error: {exc}")

    write_ingest_outputs(
        records,
        out=args.out,
        excluded=args.excluded,
        manifest=args.manifest,
        source_archive=str(args.archive),
        annotation_path=annotation_path,
    )

    included = sum(1 for item in records if item.exclusion_reason is None)
    print(f"archive records: {len(records)}  included: {included}  "
          f"excluded: {len(records) - included}")
    reasons: dict[str, int] = {}
    for item in records:
        if item.exclusion_reason is not None:
            reasons[item.exclusion_reason] = reasons.get(item.exclusion_reason, 0) + 1
    for reason, count in sorted(reasons.items()):
        print(f"  excluded[{reason}]: {count}")
    print(f"wrote {args.out}  {args.excluded}  {args.manifest}")
    return 0


if __name__ == "__main__":
    raise SystemExit(_main())