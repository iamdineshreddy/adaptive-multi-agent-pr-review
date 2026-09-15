"""Preprocess a code-review corpus into the labelled research dataset.

Applies the label rules in ``app/experiments/labeling`` (docs/EXPERIMENTS.md §1,
docs/Auth.md) to every record, writes the labelled artifact (raw record + the
``label`` / ``rule`` / ``included`` fields it resolved to) and a manifest
(labelled-count matrix, exclusion breakdown by rule, input provenance hash).

Usage:
    python experiments/preprocessing/preprocess.py <input.jsonl> \\
        --out <labelled.jsonl> --manifest <manifest.json>
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path
from typing import Any

sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "backend"))

from app.experiments.corpus import Corpus, build_corpus
from app.experiments.io import read_corpus_jsonl, write_json


def _main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("input", type=Path, help="raw JSONL corpus")
    parser.add_argument("--out", type=Path, default=Path("labelled.jsonl"),
                        help="labelled JSONL artifact (default: labelled.jsonl)")
    parser.add_argument("--manifest", type=Path, default=Path("manifest.json"),
                        help="manifest JSON (default: manifest.json)")
    args = parser.parse_args(argv)

    records = read_corpus_jsonl(args.input)
    corpus = build_corpus(records, source=str(args.input))
    _write_labeled(args.out, records, corpus)
    manifest = _manifest(corpus)
    write_json(args.manifest, manifest)
    _print_summary(corpus, records)
    return 0


def _write_labeled(
    out: Path, records: list[dict[str, Any]], corpus: Corpus
) -> None:
    out.parent.mkdir(parents=True, exist_ok=True)
    with out.open("w", encoding="utf-8") as handle:
        # corpus.rows are produced in record order (see build_corpus).
        for record, row in zip(records, corpus.rows, strict=True):
            payload = dict(record)
            payload["label"] = row.label
            payload["rule"] = row.rule
            payload["included"] = row.included
            handle.write(json.dumps(payload, ensure_ascii=False))
            handle.write("\n")


def _manifest(corpus: Corpus) -> dict[str, Any]:
    excluded_by_rule: dict[str, int] = {}
    for row in corpus.rows:
        if not row.included:
            excluded_by_rule[row.rule] = excluded_by_rule.get(row.rule, 0) + 1
    return {
        "corpus": corpus.provenance,
        "label_counts": dict(sorted(corpus.label_counts.items())),
        "excluded_by_rule": dict(sorted(excluded_by_rule.items())),
        "label_rules": (
            "docs/EXPERIMENTS.md §1; ACCEPTED/FIXED require verbatim or "
            "explicit acceptance evidence, never code-change alone (Auth.md)."
        ),
        "licensing": (
            "dataset licensing documented in datasets/README.md before any "
            "ingestion into this pipeline."
        ),
    }


def _print_summary(corpus: Corpus, records: list[dict[str, Any]]) -> None:
    names = ", ".join(sorted(corpus.label_counts))
    print(
        f"labelled {len(corpus.rows)} rows from {len(records)} raw records "
        f"(reviews: {len(corpus.reviews)})"
    )
    print(f"labels: {names}")
    print(f"excluded: {corpus.excluded_count} (not in acceptance accounting)")
    print(f"corpus sha256: {corpus.provenance['raw_sha256']}")


if __name__ == "__main__":
    raise SystemExit(_main())