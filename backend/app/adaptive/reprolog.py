"""Reproducibility log for ARUM decisions (docs/EXPERIMENTS.md §5, ARUM.md §10).

Every scored candidate is recorded with the full decision context: ARUM
version, the eight features, the exact weights, budget/gate fields (Phase 9),
the redundancy group id, and a hash of the inputs. Rerunning the same review
with the same model version reproduces the same trace (deterministic).
"""

from __future__ import annotations

import hashlib
import json
from pathlib import Path
from typing import Any, Protocol

from app.adaptive.scoring import Decision


def compute_inputs_hash(*parts: object) -> str:
    """Deterministic SHA-256 over the decision's inputs.

    Joins ``str(part)`` with ``|`` so a stable tuple order yields a stable hash.
    """
    digest = hashlib.sha256()
    for part in parts:
        digest.update(str(part).encode("utf-8"))
        digest.update(b"|")
    return digest.hexdigest()


class DecisionTrace(Protocol):
    """Where decision records go (memory in unit tests, file in production)."""

    def append(self, decision: Decision) -> None: ...

    def read_all(self) -> list[dict[str, Any]]: ...


class MemoryDecisionTrace:
    """In-process trace sink for unit tests and local debugging."""

    def __init__(self) -> None:
        self._rows: list[dict[str, Any]] = []

    def append(self, decision: Decision) -> None:
        self._rows.append(decision.to_dict())

    def read_all(self) -> list[dict[str, Any]]:
        return list(self._rows)


class FileDecisionTrace:
    """Append-only JSON Lines sink under ``experiments/results/``.

    One line per decision; read back in insertion order. The directory is
    created on first append.
    """

    def __init__(self, path: str | Path) -> None:
        self._path = Path(path)

    def append(self, decision: Decision) -> None:
        self._path.parent.mkdir(parents=True, exist_ok=True)
        line = json.dumps(decision.to_dict(), sort_keys=True)
        with self._path.open("a", encoding="utf-8") as handle:
            handle.write(line + "\n")

    def read_all(self) -> list[dict[str, Any]]:
        if not self._path.exists():
            return []
        rows: list[dict[str, Any]] = []
        with self._path.open("r", encoding="utf-8") as handle:
            for line in handle:
                stripped = line.strip()
                if stripped:
                    rows.append(json.loads(stripped))
        return rows
