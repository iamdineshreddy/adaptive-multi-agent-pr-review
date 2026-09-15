"""Reproduce the full baseline + ablation experiment table (EXPERIMENTS.md §5).

Runs B1..B5 and the six B5 ablations over one labelled corpus using the
production selection machinery (``app.experiments.runner``), and writes:

- ``results/runs/<MODE>.json`` — the full per-mode reproducibility payload,
- ``results/summary.csv`` — one row per mode with the ARUM §5 metrics,
- ``results/summary.json`` — delta table of every ablation vs B5.

Usage:
    python experiments/run/run_all.py [--corpus experiments/samples/reviews_sample.jsonl]
                                      [--seed 0] [--results-dir experiments/results]
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path
from typing import Any

sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "backend"))

from app.experiments.corpus import Corpus, build_corpus
from app.experiments.io import (
    compare_to_baseline,
    read_corpus_jsonl,
    write_json,
    write_runs_json,
    write_summary_csv,
)

DEFAULT_CORPUS = (
    Path(__file__).resolve().parents[1] / "samples" / "reviews_sample.jsonl"
)
DEFAULT_RESULTS = Path(__file__).resolve().parents[1] / "results"


def _main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--corpus", type=Path, default=DEFAULT_CORPUS,
                        help="raw JSONL corpus (default: bundled samples)")
    parser.add_argument("--seed", type=int, default=0, help="reproducibility seed")
    parser.add_argument("--results-dir", type=Path, default=DEFAULT_RESULTS,
                        help="output directory (default: experiments/results)")
    args = parser.parse_args(argv)

    from app.experiments.modes import all_modes
    from app.experiments.runner import run_mode

    records = read_corpus_jsonl(args.corpus)
    corpus = build_corpus(records, source=str(args.corpus))

    runs: dict[str, dict[str, Any]] = {}
    for config in all_modes():
        runs[config.key] = run_mode(corpus, config, seed=args.seed)
        run = runs[config.key]
        print(
            f"{config.key:<3} {config.label:<58} "
            f"P={run['metrics']['precision']:.3f} "
            f"R={run['metrics']['recall']:.3f} "
            f"F1={run['metrics']['f1']:.3f} "
            f"red={run['metrics']['redundancy_reduction']:.3f} "
            f"sel={run['selection']['selected_count']}"
        )

    deltas = compare_to_baseline(runs, baseline_key="B5")
    write_runs_json(args.results_dir, runs)
    write_summary_csv(
        args.results_dir / "summary.csv",
        [runs[k] for k in ("B1", "B2", "B3", "B4", "B5", "A1", "A2",
                           "A3", "A4", "A5", "A6")],
        extra_columns=("candidates",),
    )
    write_json(
        args.results_dir / "summary.json",
        _summary_payload(corpus, args.seed, runs, deltas),
    )
    _print_deltas(deltas)
    return 0


def _summary_payload(
    corpus: Corpus,
    seed: int,
    runs: dict[str, dict[str, Any]],
    deltas: dict[str, dict[str, float | None]],
) -> dict[str, Any]:
    return {
        "corpus": corpus.provenance,
        "seed": seed,
        "configured_modes": list(runs.keys()),
        "deltas_vs_b5": deltas,
        "integrity": (
            "Numbers reflect an executed run over the listed corpus. The "
            "bundled sample is a smoke corpus for pipeline verification, NOT "
            "a research dataset; paper numbers await the github-codereview "
            "ingestion (datasets/README.md) and its licensing review."
        ),
    }


def _print_deltas(deltas: dict[str, dict[str, float | None]]) -> None:
    print("\nDelta vs B5:")
    for key, entry in deltas.items():
        fmt = ", ".join(
            f"{name}={value:+.3f}" if value is not None else f"{name}=n/a"
            for name, value in entry.items()
        )
        print(f"  {key}: {fmt}")


if __name__ == "__main__":
    raise SystemExit(_main())