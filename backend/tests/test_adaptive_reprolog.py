"""Unit tests for the reproducibility log (docs/EXPERIMENTS.md §5, ARUM.md §10)."""

from __future__ import annotations

import json
import uuid

import pytest

from app.adaptive.features import ArumFeatures
from app.adaptive.reprolog import (
    FileDecisionTrace,
    MemoryDecisionTrace,
    compute_inputs_hash,
)
from app.adaptive.scoring import Decision, utility
from app.adaptive.weights import ArumWeights

REVIEW_ID = str(uuid.uuid4())


def _decision(finding_id: str) -> Decision:
    weights = ArumWeights()
    features = ArumFeatures(
        severity=0.8,
        confidence=0.9,
        agent_agreement=1.0,
        historical_actionability=0.0,
        repository_relevance=0.0,
        context_relevance=0.0,
        redundancy=0.5,
        historical_rejection=0.0,
    )
    return Decision(
        review_id=REVIEW_ID,
        finding_id=finding_id,
        group_id=str(uuid.uuid4()),
        arum_version=weights.version,
        features=features,
        utility=utility(features, weights),
        weights=weights,
        inputs_hash=compute_inputs_hash(finding_id),
    )


def test_memory_trace_appends_and_reads() -> None:
    trace = MemoryDecisionTrace()
    trace.append(_decision("f1"))
    trace.append(_decision("f2"))
    rows = trace.read_all()
    assert len(rows) == 2
    assert rows[0]["finding_id"] == "f1"
    assert {*rows[0]} <= {
        "trace_version",
        "review_id",
        "finding_id",
        "group_id",
        "arum_version",
        "features",
        "utility",
        "weights",
        "inputs_hash",
        "budget_cap",
        "safety_gate",
    }


def test_file_trace_round_trip(tmp_path: pytest.TempPathFactory) -> None:
    path = tmp_path / "results" / "decisions.jsonl"
    trace = FileDecisionTrace(path)
    trace.append(_decision("f1"))
    trace.append(_decision("f2"))

    assert path.exists()
    assert path.name == "decisions.jsonl"
    lines = path.read_text(encoding="utf-8").strip().splitlines()
    assert len(lines) == 2
    # JSON lines are valid JSON, sorted keys, stable across appends.
    assert json.loads(lines[0])["finding_id"] == "f1"
    assert json.loads(lines[1])["finding_id"] == "f2"


def test_file_trace_read_missing_file_is_empty(
    tmp_path: pytest.TempPathFactory,
) -> None:
    trace = FileDecisionTrace(tmp_path / "nope.jsonl")
    assert trace.read_all() == []


def test_inputs_hash_deterministic_across_calls() -> None:
    trace = MemoryDecisionTrace()
    a = _decision("f1")
    assert a.inputs_hash == compute_inputs_hash("f1")
    trace.append(_decision("f1"))
    assert trace.read_all()[0]["inputs_hash"] == a.inputs_hash
