"""Unit tests for the experiment plotting core (Phase 17 part 2).

``load_summary`` is pure stdlib (csv/json) and always runs; ``render_figures``
needs the ``plots`` extra (matplotlib) and self-skips with a clear reason when
absent, mirroring the live-service self-skip discipline. Figures carry the
verification-only footnote so rendered artifacts are never mistaken for
research plots (docs/EXPERIMENTS.md §8).
"""

from __future__ import annotations

from pathlib import Path

import pytest

from app.experiments.io import METRIC_KEYS
from app.experiments.plots import (
    PlotsExtraUnavailableError,
    load_summary,
    render_figures,
)

MODE_KEYS = ("B1", "B2", "B3", "B4", "B5", "A1", "A2", "A3", "A4", "A5", "A6")


def _write_artifacts(tmp_path: Path, *, with_deltas: bool = True) -> tuple[Path, Path]:
    csv_path = tmp_path / "summary.csv"
    json_path = tmp_path / "summary.json"

    header = ["mode", "label", *METRIC_KEYS, "selected", "candidates"]
    lines = [",".join(header)]
    for i, mode in enumerate(MODE_KEYS):
        precision = 1.0 - 0.05 * i if mode.startswith("B") else 0.9
        row = [
            mode,
            f"mode {mode}",
            f"{precision:.6f}",
            "0.700000",
            "0.824000",
            "0.050000",
            "0.300000",
            "0.500000",
            "0.125000",
            "0.400000",
            "4",
            "10",
        ]
        lines.append(",".join(row))
    csv_path.write_text("\n".join(lines) + "\n", encoding="utf-8")

    deltas = (
        {
            "deltas_vs_b5": {
                mode: {
                    "delta_precision": 0.01 * i,
                    "delta_recall": 0.02 * i,
                    "delta_f1": -0.03 * i,
                    "delta_selected": i - 1,
                }
                for i, mode in enumerate(MODE_KEYS)
                if mode.startswith("A")
            }
        }
        if with_deltas
        else {"deltas_vs_b5": {}}
    )
    json_path.write_text(
        __import__("json").dumps(
            {"corpus": {"source": "fake.jsonl", "sample": True}, **deltas}
        ),
        encoding="utf-8",
    )
    return csv_path, json_path


def test_load_summary_parses_rows_and_metrics(tmp_path: Path) -> None:
    csv_path, json_path = _write_artifacts(tmp_path)
    data = load_summary(csv_path, json_path)
    assert [row.mode for row in data.rows] == list(MODE_KEYS)
    b5 = next(row for row in data.rows if row.mode == "B5")
    assert b5.metrics["precision"] == pytest.approx(0.8)
    assert b5.metrics["f1"] == pytest.approx(0.824)
    assert b5.selected == 4
    assert b5.extra["candidates"] == 10
    assert data.provenance["source"] == "fake.jsonl"


def test_load_summary_parses_deltas_only_for_ablations(tmp_path: Path) -> None:
    csv_path, json_path = _write_artifacts(tmp_path)
    data = load_summary(csv_path, json_path)
    assert set(data.deltas) == {"A1", "A2", "A3", "A4", "A5", "A6"}
    # A1 is the ablation at enumerate index 5 (B1..B5 first), A6 at index 10.
    assert data.deltas["A1"]["delta_precision"] == pytest.approx(0.05)
    assert data.deltas["A6"]["delta_selected"] == pytest.approx(9)


def test_load_summary_empty_deltas_not_required(tmp_path: Path) -> None:
    csv_path, json_path = _write_artifacts(tmp_path, with_deltas=False)
    data = load_summary(csv_path, json_path)
    assert data.deltas == {}
    rows = data.rows
    assert len(rows) == len(MODE_KEYS)


def test_load_summary_handles_na_and_blank_cells(tmp_path: Path) -> None:
    csv_path = tmp_path / "summary.csv"
    csv_path.write_text(
        "mode,label,precision,recall,f1,false_positive_rate,"
        + "comment_reduction,redundancy_reduction,acceptance_rate,"
        + "actionability,selected\n"
        + "B5,baseline,n/a,,0.5,,,0.6,,,5\n",
        encoding="utf-8",
    )
    json_path = tmp_path / "summary.json"
    json_path.write_text('{"corpus": {}, "deltas_vs_b5": {}}', encoding="utf-8")
    data = load_summary(csv_path, json_path)
    row = data.rows[0]
    assert row.metrics["precision"] is None
    assert row.metrics["recall"] is None
    assert row.metrics["f1"] == pytest.approx(0.5)
    assert row.metrics["redundancy_reduction"] == pytest.approx(0.6)
    assert row.selected == 5


def test_load_summary_missing_artifacts_raises(tmp_path: Path) -> None:
    missing = tmp_path / "none.csv"
    with pytest.raises(FileNotFoundError, match="run_all.py"):
        load_summary(missing, tmp_path / "none.json")


def test_load_summary_rejects_row_without_mode(tmp_path: Path) -> None:
    csv_path = tmp_path / "summary.csv"
    csv_path.write_text("label\nno-mode\n", encoding="utf-8")
    json_path = tmp_path / "summary.json"
    json_path.write_text("{}", encoding="utf-8")
    with pytest.raises(ValueError, match="mode"):
        load_summary(csv_path, json_path)


def test_render_figures_writes_two_pngs_with_footnote(
    tmp_path: Path,
) -> None:
    pytest.importorskip("matplotlib")
    csv_path, json_path = _write_artifacts(tmp_path)
    out_dir = tmp_path / "figs"
    data = load_summary(csv_path, json_path)
    written = render_figures(
        data, out_dir, verify_only_label="VERIFICATION-ONLY — smoke"
    )
    assert [path.name for path in written] == [
        "plot_metrics.png",
        "plot_deltas_vs_b5.png",
    ]
    for path in written:
        assert path.is_file()
        assert path.stat().st_size > 0


def test_render_figures_without_matplotlib_raises_clearly(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    def _raise() -> None:
        raise PlotsExtraUnavailableError()

    monkeypatch.setattr("app.experiments.plots._matplotlib", _raise)
    with pytest.raises(PlotsExtraUnavailableError):
        render_figures(
            object(),  # type: ignore[arg-type]
            Path("unused"),
            verify_only_label="x",
        )
