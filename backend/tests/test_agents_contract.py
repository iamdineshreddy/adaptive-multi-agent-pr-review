"""Unit tests for the agent output contract (docs/AGENTS.md §4)."""

from __future__ import annotations

import json
import uuid

import pytest
from pydantic import ValidationError

from app.agents.contract import (
    MAX_FINDINGS_PER_BATCH,
    AgentFinding,
    AgentOutputError,
    AgentScope,
    ChangeKind,
    parse_batch,
    scope_validation_error,
)
from app.models.enums import Severity


def encode_findings(items: list[dict]) -> str:
    """Encode a findings list as the JSON envelope the model is asked to emit."""
    return json.dumps({"findings": items})


class TestAgentFindingValidation:
    def test_valid_finding(self, finding_data) -> None:
        finding = AgentFinding.model_validate(finding_data)
        assert finding.severity == Severity.HIGH
        assert finding.confidence == 0.9

    def test_severity_is_case_insensitive(self, finding_data) -> None:
        data = dict(finding_data)
        data["severity"] = "low"
        assert AgentFinding.model_validate(data).severity == Severity.LOW

    def test_unknown_severity_rejected(self, finding_data) -> None:
        data = dict(finding_data)
        data["severity"] = "catastrophic"
        with pytest.raises(ValidationError):
            AgentFinding.model_validate(data)

    def test_confidence_out_of_range_rejected(self, finding_data) -> None:
        data = dict(finding_data)
        data["confidence"] = 1.5
        with pytest.raises(ValidationError):
            AgentFinding.model_validate(data)

    def test_string_confidence_coerced(self, finding_data) -> None:
        data = dict(finding_data)
        data["confidence"] = "0.75"
        assert AgentFinding.model_validate(data).confidence == 0.75

    def test_boolean_confidence_rejected(self, finding_data) -> None:
        data = dict(finding_data)
        data["confidence"] = True
        with pytest.raises(ValidationError):
            AgentFinding.model_validate(data)

    def test_line_bounds_checked(self, finding_data) -> None:
        data = dict(finding_data)
        data["line_start"] = 20
        data["line_end"] = 10
        with pytest.raises(ValidationError):
            AgentFinding.model_validate(data)

    def test_extra_fields_rejected(self, finding_data) -> None:
        data = dict(finding_data)
        data["chain_of_thought"] = "first I thought ..."
        with pytest.raises(ValidationError):
            AgentFinding.model_validate(data)


class TestParseBatch:
    def test_valid_batch_all_kept(self, finding_data, sample_scope) -> None:
        result = parse_batch(
            encode_findings([finding_data, finding_data]),
            changed_paths=[s.file_path for s in sample_scope.changed_files],
            category_prefixes=["security"],
        )
        assert result.valid_count == 2
        assert result.invalid_count == 0
        assert result.raw_count == 2

    def test_non_json_raises(self) -> None:
        with pytest.raises(AgentOutputError):
            parse_batch(
                "this is not json",
                changed_paths=["app/auth.py"],
                category_prefixes=["security"],
            )

    def test_missing_findings_key_raises(self) -> None:
        with pytest.raises(AgentOutputError):
            parse_batch(
                '{"results": []}',
                changed_paths=["app/auth.py"],
                category_prefixes=["security"],
            )

    def test_file_not_in_changed_set_dropped(self, finding_data, sample_scope) -> None:
        payload = encode_findings([dict(finding_data, file_path="elsewhere.py")])
        result = parse_batch(
            payload,
            changed_paths=[s.file_path for s in sample_scope.changed_files],
            category_prefixes=["security"],
        )
        assert result.valid_count == 0
        assert result.invalid_count == 1
        assert "not in the changed files" in result.errors[0]

    def test_wrong_category_dropped(self, finding_data, sample_scope) -> None:
        payload = encode_findings(
            [dict(finding_data, category="quality/maintainability")]
        )
        result = parse_batch(
            payload,
            changed_paths=[s.file_path for s in sample_scope.changed_files],
            category_prefixes=["security"],
        )
        assert result.valid_count == 0
        assert result.invalid_count == 1

    def test_bare_list_accepted(self, finding_data, sample_scope) -> None:
        result = parse_batch(
            json.dumps([finding_data]),
            changed_paths=[s.file_path for s in sample_scope.changed_files],
            category_prefixes=["security"],
        )
        assert result.valid_count == 1

    def test_batch_cap_counts_overage(self, finding_data, sample_scope) -> None:
        items = [finding_data for _ in range(MAX_FINDINGS_PER_BATCH + 5)]
        result = parse_batch(
            encode_findings(items),
            changed_paths=[s.file_path for s in sample_scope.changed_files],
            category_prefixes=["security"],
        )
        assert result.valid_count == MAX_FINDINGS_PER_BATCH
        assert result.invalid_count == 5
        assert result.raw_count == MAX_FINDINGS_PER_BATCH + 5


class TestScopeRoundTrip:
    def test_to_dict_from_dict(self, sample_scope) -> None:
        restored = AgentScope.from_dict(sample_scope.to_dict())
        assert restored.review_id == str(sample_scope.review_id)
        assert restored.pr_number == sample_scope.pr_number
        assert restored.changed_paths == {"app/auth.py"}
        assert restored.changed_files[0].change_kind == ChangeKind.MODIFIED

    def test_empty_scope_flagged(self) -> None:
        empty = AgentScope(
            review_id=uuid.uuid4(),
            repository_id=uuid.uuid4(),
            pr_number=1,
            pr_title="t",
            pr_description="",
            base_ref="main",
            head_ref="feat",
            changed_files=(),
        )
        assert scope_validation_error(empty) == "scope has no changed files to review"
