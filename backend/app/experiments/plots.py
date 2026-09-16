"""Experiment plot rendering core (Phase 17 part 2, docs/EXPERIMENTS.md §4/§8).

Reads the harness's ``summary.csv`` + ``summary.json`` artifacts (produced by
``experiments/run/run_all.py``) and renders two matplotlib figures:

- ``plot_metrics.png`` — grouped bars of precision / recall / F1 per mode
  (B1..B5 and the A1..A6 ablations).
- ``plot_deltas_vs_b5.png`` — per-ablation deltas vs the B5 baseline (three ARUM
  §5 metrics + the selected-count delta), matching the printed delta table.

The data preparation layer is pure (stdlib csv/json); matplotlib is imported
lazily and raises :class:`PlotsExtraUnavailableError` with an install hint when
absent (same discipline as ``app.adaptive.training`` for the ``ml`` extra) so
the reading/validation logic is always testable offline.

Honesty contract (README integrity rule): figures carry a ``verify_only_label``
footnote. On the bundled sample corpus the label reads VERIFICATION-ONLY —
these values are a pipeline smoke check, never research numbers. Paper figures
await the ``github-codereview`` ingestion in Phase 17 part 2's dataset pass
(docs/EXPERIMENTS.md §8).
"""

from __future__ import annotations

import csv
import json
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from app.experiments.io import METRIC_KEYS

_ABLATION_ORDER = ("A1", "A2", "A3", "A4", "A5", "A6")


class PlotsExtraUnavailableError(ImportError):
    """The ``plots`` optional extra is required for figure rendering."""

    def __init__(self) -> None:
        super().__init__(
            "matplotlib not installed; install the 'plots' extra "
            "(pip install -e '.[plots]') for experiment figures"
        )


@dataclass(frozen=True)
class ModeMetrics:
    """One ``summary.csv`` row for a configured mode."""

    mode: str
    label: str
    metrics: dict[str, float | None]
    selected: int | None
    extra: dict[str, float | None]


@dataclass(frozen=True)
class SummaryData:
    """Parsed summary artifacts passed to the renderer."""

    provenance: dict[str, Any]
    rows: list[ModeMetrics]
    deltas: dict[str, dict[str, float | None]] = field(default_factory=dict)


def _to_float(value: object) -> float | None:
    if value is None:
        return None
    if isinstance(value, float):
        return value
    text = str(value).strip()
    if not text or text.lower() in {"n/a", "nan", "none"}:
        return None
    try:
        return float(text)
    except (TypeError, ValueError):
        return None


def load_summary(csv_path: Path, json_path: Path) -> SummaryData:
    """Parse the harness artifacts into :class:`SummaryData` (pure stdlib).

    Raises :class:`FileNotFoundError` when the artifacts were not produced yet
    (run ``experiments/run/run_all.py`` first) and :class:`ValueError` on a
    malformed CSV column layout.
    """
    if not csv_path.is_file() or not json_path.is_file():
        raise FileNotFoundError(
            f"experiment artifacts missing ({csv_path}, {json_path}); run "
            "experiments/run/run_all.py first"
        )

    rows: list[ModeMetrics] = []
    with csv_path.open(newline="", encoding="utf-8") as handle:
        reader = csv.DictReader(handle)
        for raw in reader:
            mode = raw.get("mode")
            label = raw.get("label")
            if not mode or not label:
                raise ValueError(f"{csv_path}: rows need 'mode' and 'label' columns")
            metrics = {key: _to_float(raw.get(key)) for key in METRIC_KEYS}
            extra: dict[str, float | None] = {}
            for key, value in raw.items():
                if key not in {"mode", "label", *METRIC_KEYS, "selected"}:
                    extra[key] = _to_float(value)
            rows.append(
                ModeMetrics(
                    mode=mode,
                    label=label,
                    metrics=metrics,
                    selected=_to_int(raw.get("selected")),
                    extra=extra,
                )
            )

    payload = json.loads(json_path.read_text(encoding="utf-8"))
    deltas: dict[str, dict[str, float | None]] = {}
    for key, entry in (payload.get("deltas_vs_b5") or {}).items():
        deltas[str(key)] = {
            str(name): _to_float(value) for name, value in entry.items()
        }
    return SummaryData(
        provenance=payload.get("corpus") or {},
        rows=rows,
        deltas=deltas,
    )


def _to_int(value: object) -> int | None:
    parsed = _to_float(value)
    return int(parsed) if parsed is not None else None


def _matplotlib() -> Any:
    try:
        # Patches: this is the lazy renderer path; imports are the dependency seam.
        import matplotlib  # noqa: F401
        import matplotlib.pyplot as plt
    except ImportError as exc:  # pragma: no cover - exercised when extra absent
        raise PlotsExtraUnavailableError() from exc
    return plt


def render_figures(
    data: SummaryData,
    out_dir: Path,
    *,
    verify_only_label: str,
) -> list[Path]:
    """Render both figures into ``out_dir``; returns the written paths.

    ``verify_only_label`` (e.g. "VERIFICATION-ONLY — smoke corpus") is drawn as
    an explicit footnote on every figure so a rendered artifact can never be
    mistaken for a research plot.
    """
    plt = _matplotlib()
    written: list[Path] = []
    out_dir.mkdir(parents=True, exist_ok=True)

    written.append(
        _render_metrics(plt, data, out_dir, verify_only_label=verify_only_label)
    )
    written.append(
        _render_deltas(plt, data, out_dir, verify_only_label=verify_only_label)
    )
    plt.close("all")
    return written


def _render_metrics(
    plt: Any,
    data: SummaryData,
    out_dir: Path,
    *,
    verify_only_label: str,
) -> Path:
    modes = [row.mode for row in data.rows]
    metrics = ("precision", "recall", "f1")
    values = {
        metric: [_to_float(row.metrics.get(metric)) for row in data.rows]
        for metric in metrics
    }

    fig, ax = plt.subplots(figsize=(11, 5))
    width = 0.28
    positions = range(len(modes))
    for offset, metric in enumerate(metrics):
        x = [pos + (offset - 1) * width for pos in positions]
        series = [0.0 if v is None else float(v) for v in values[metric]]
        bars = ax.bar(x, series, width, label=metric.title())
        ax.bar_label(
            bars,
            labels=["" if v is None else f"{float(v):.3f}" for v in values[metric]],
            fontsize=8,
            padding=2,
        )
    ax.set_xticks([pos for pos in positions], [modes[i] for i in positions])
    ax.set_xlabel("Mode")
    ax.set_ylabel("Score")
    ax.set_ylim(0, 1)
    ax.set_title("ARUM §5 metrics per configured mode")
    ax.legend(ncol=3)
    _footnote(fig, verify_only_label)
    path = out_dir / "plot_metrics.png"
    fig.savefig(path, dpi=150, bbox_inches="tight")
    plt.close(fig)
    return path


def _render_deltas(
    plt: Any,
    data: SummaryData,
    out_dir: Path,
    *,
    verify_only_label: str,
) -> Path:
    modes = [str(m) for m in _ABLATION_ORDER if m in data.deltas]
    metrics = ("delta_precision", "delta_recall", "delta_f1")
    deltas = {
        metric: [_to_float(data.deltas[m].get(metric)) for m in modes]
        for metric in metrics
    }

    fig, ax = plt.subplots(figsize=(11, 5))
    width = 0.28
    if modes:
        positions = range(len(modes))
        for offset, metric in enumerate(metrics):
            y = [pos - (offset - 1) * width for pos in positions]
            series = [0.0 if v is None else float(v) for v in deltas[metric]]
            display = metric.replace("delta_", "")
            ax.barh(y, series, width, label=display.title())
        ax.set_yticks([pos for pos in positions], [modes[i] for i in positions])
        ax.set_xlabel("Delta vs B5")
        ax.axvline(0.0, color="grey", linewidth=0.8)
    else:
        ax.text(
            0.5,
            0.5,
            "no ablation deltas available",
            ha="center",
            va="center",
            transform=ax.transAxes,
        )
    ax.set_title("Ablation delta vs B5 baseline (ARUM §5)")
    ax.legend(ncol=3)
    _footnote(fig, verify_only_label)
    path = out_dir / "plot_deltas_vs_b5.png"
    fig.savefig(path, dpi=150, bbox_inches="tight")
    plt.close(fig)
    return path


def _footnote(fig: Any, verify_only_label: str) -> None:
    fig.text(0.01, 0.005, verify_only_label, fontsize=8, color="darkred", ha="left")
