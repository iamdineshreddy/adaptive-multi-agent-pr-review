"""Experiment results endpoint (read-only).

Serves the offline selection-layer experiment artifacts (B1-B5, A1-A6) so the
dashboard can display real measured metrics without fabricating values.
"""

from __future__ import annotations

import csv
import json
from pathlib import Path
from typing import Annotated, Any

from fastapi import APIRouter, Depends, HTTPException

from app.config.settings import get_settings
from app.security.auth import get_principal

router = APIRouter(tags=["experiments"], dependencies=[Depends(get_principal)])


def _results_dir() -> Path:
    settings = get_settings()
    raw = getattr(settings, "experiments_results_dir", "experiments/results")
    return Path(raw)


@router.get("/experiments/summary")
async def experiment_summary() -> dict[str, Any]:
    rdir = _results_dir()
    csv_path = rdir / "summary.csv"
    json_path = rdir / "summary.json"
    if not csv_path.exists():
        raise HTTPException(status_code=404, detail="experiment results not found")
    rows: list[dict[str, str]] = []
    with csv_path.open(encoding="utf-8", newline="") as handle:
        reader = csv.DictReader(handle)
        for row in reader:
            rows.append(row)
    summary_json = {}
    if json_path.exists():
        summary_json = json.loads(json_path.read_text(encoding="utf-8"))
    return {
        "modes": rows,
        "execution": summary_json.get("execution"),
        "corpus": summary_json.get("corpus"),
        "deltas_vs_b5": summary_json.get("deltas_vs_b5"),
    }
