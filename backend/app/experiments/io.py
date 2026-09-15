"""Experiment I/O: corpus reading, run JSON, summary CSV/JSON (EXPERIMENTS.md §5)."""

from __future__ import annotations

import csv
import json
from collections.abc import Mapping, Sequence
from pathlib import Path
from typing import Any

METRIC_KEYS = (
    "precision",
    "recall",
    "f1",
    "false_positive_rate",
    "comment_reduction",
    "redundancy_reduction",
    "acceptance_rate",
    "actionability",
)


class CorpusFileError(ValueError):
    """Malformed corpus file."""


def read_corpus_jsonl(path: Path) -> list[dict[str, Any]]:
    """Read a JSONL corpus into raw records (raises on malformed lines)."""
    records: list[dict[str, Any]] = []
    with path.open(encoding="utf-8") as handle:
        for line_number, line in enumerate(handle, start=1):
            stripped = line.strip()
            if not stripped:
                continue
            try:
                record = json.loads(stripped)
            except json.JSONDecodeError as exc:
                raise CorpusFileError(
                    f"{path}:{line_number}: invalid JSON: {exc.msg}"
                ) from exc
            if not isinstance(record, dict):
                raise CorpusFileError(
                    f"{path}:{line_number}: expected a JSON object per line"
                )
            records.append(record)
    return records


def write_json(path: Path, payload: Mapping[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", encoding="utf-8") as handle:
        json.dump(payload, handle, indent=2, ensure_ascii=False)
        handle.write("\n")


def write_runs_json(results_dir: Path, runs: Mapping[str, Mapping[str, Any]]) -> None:
    for key, run in runs.items():
        write_json(results_dir / "runs" / f"{key}.json", run)


def write_summary_csv(
    path: Path,
    runs: Sequence[Mapping[str, Any]],
    *,
    extra_columns: Sequence[str] = (),
) -> None:
    columns = ["mode", "label"] + list(METRIC_KEYS) + ["selected"] + list(extra_columns)
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", newline="", encoding="utf-8") as handle:
        writer = csv.writer(handle)
        writer.writerow(columns)
        for run in runs:
            metrics = run["metrics"]
            writer.writerow(
                [run["mode"], run["label"]]
                + [_fmt(metrics.get(key)) for key in METRIC_KEYS]
                + [run["selection"]["selected_count"]]
                + [run.get(key, "") for key in extra_columns]
            )


def compare_to_baseline(
    runs: Mapping[str, Mapping[str, Any]],
    *,
    baseline_key: str = "B5",
) -> dict[str, dict[str, float | None]]:
    """Delta table: each run's metric minus the baseline's (EXPERIMENTS.md §4).

    Only metrics that are numeric and non-null on both sides are compared; the
    selection count delta is reported separately as ``delta_selected``.
    """
    baseline = runs.get(baseline_key)
    if baseline is None:
        return {}
    deltas: dict[str, dict[str, float | None]] = {}
    for key, run in runs.items():
        if key == baseline_key:
            continue
        entry: dict[str, float | None] = {}
        base_sel = int(baseline["selection"]["selected_count"])
        run_sel = int(run["selection"]["selected_count"])
        entry["delta_selected"] = run_sel - base_sel
        for metric in METRIC_KEYS:
            left = run["metrics"].get(metric)
            right = baseline["metrics"].get(metric)
            if isinstance(left, (int, float)) and isinstance(right, (int, float)):
                entry[metric] = round(float(left) - float(right), 6)
            else:
                entry[metric] = None
        deltas[key] = entry
    return deltas


def write_summary_json(path: Path, payload: Mapping[str, Any]) -> None:
    write_json(path, payload)


def _fmt(value: object) -> str:
    if isinstance(value, float):
        return f"{value:.6f}"
    if value is None:
        return "n/a"
    return str(value)
