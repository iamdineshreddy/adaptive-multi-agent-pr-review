"""Render experiment summary figures from the harness artifacts (EXPERIMENTS.md §4).

Reads ``experiments/results/summary.csv`` + ``summary.json`` (produced by
``run_all.py``) and writes ``plot_metrics.png`` and ``plot_deltas_vs_b5.png``
next to them via ``app.experiments.plots``.

Usage:
    python experiments/run/plot_summary.py [--results-dir experiments/results]
                                           [--label "VERIFICATION-ONLY — smoke corpus"]
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "backend"))

from app.experiments.plots import (  # noqa: E402
    PlotsExtraUnavailableError,
    load_summary,
    render_figures,
)

DEFAULT_RESULTS = Path(__file__).resolve().parents[1] / "results"
DEFAULT_LABEL = "VERIFICATION-ONLY — bundled smoke corpus, not research data"


def _main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--results-dir", type=Path, default=DEFAULT_RESULTS,
                        help="directory holding summary.csv / summary.json")
    parser.add_argument(
        "--label",
        type=str,
        default=DEFAULT_LABEL,
        help="footnote drawn on every figure (honesty/verification marker)",
    )
    args = parser.parse_args(argv)

    data = load_summary(args.results_dir / "summary.csv",
                        args.results_dir / "summary.json")
    if not data.rows:
        raise SystemExit("summary.csv holds no mode rows; run run_all.py first")

    try:
        written = render_figures(
            data, args.results_dir, verify_only_label=args.label
        )
    except PlotsExtraUnavailableError as exc:
        raise SystemExit(str(exc))

    for path in written:
        print(f"wrote {path}")
    print(f"footnote: {args.label}")
    return 0


if __name__ == "__main__":
    raise SystemExit(_main())