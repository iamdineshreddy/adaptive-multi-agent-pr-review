"""Agent input/output contracts (docs/AGENTS.md §3-4).

Agents detect issues; they never decide what gets shown. The output contract is a
single, versioned Pydantic shape (``AgentFinding``) plus a batch envelope. Parsing
is strict: uncoercible values are dropped and counted (``failed_results``), a
non-JSON batch raises ``AgentOutputError`` so the caller can make one bounded repair
attempt, and nothing here trusts the model beyond the schema.
"""

from __future__ import annotations

import enum
import json
import uuid
from collections.abc import Mapping, Sequence
from dataclasses import dataclass, field
from typing import Any

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

from app.models.enums import Severity

# Hard caps so a hostile or broken model cannot flood the decision layer.
MAX_FINDINGS_PER_BATCH = 100
MAX_REASON_SUMMARY_CHARS = 300


class ChangeKind(enum.StrEnum):
    """How a file changed in the reviewed diff."""

    ADDED = "added"
    MODIFIED = "modified"
    REMOVED = "removed"
    RENAMED = "renamed"
    UNKNOWN = "unknown"


class AgentOutputError(Exception):
    """A batch could not be parsed into AgentFindings (triggers one repair attempt)."""


class AgentScopeConfigurationError(ValueError):
    """The review scope is unusable (no changed files, unknown agent, etc.)."""


class AgentFinding(BaseModel):
    """Structured finding emitted by an agent (docs/AGENTS.md §4).

    Field names match the ``findings`` table columns so persistence is mechanical.
    There is deliberately no chain-of-thought field: ``reason_summary`` is an
    evidence-only sentence, not a reasoning trace.
    """

    model_config = ConfigDict(extra="forbid")

    file_path: str
    line_start: int | None = None
    line_end: int | None = None
    category: str
    severity: Severity
    confidence: float = Field(ge=0.0, le=1.0)
    title: str = Field(min_length=1, max_length=240)
    description: str = Field(min_length=1)
    evidence: dict[str, Any] = Field(default_factory=dict)
    suggested_fix: str | None = None
    reason_summary: str = Field(min_length=1, max_length=MAX_REASON_SUMMARY_CHARS)
    related_categories: list[str] = Field(default_factory=list)

    @field_validator("severity", mode="before")
    @classmethod
    def coerce_severity(cls, value: object) -> Severity:
        """Accept any case/spelling of the known severity values, else invalidate."""
        if isinstance(value, Severity):
            return value
        if isinstance(value, str):
            normalised = value.strip().upper()
            if normalised in {member.value for member in Severity}:
                return Severity(normalised)
        raise ValueError("severity must be one of INFO/LOW/MEDIUM/HIGH/CRITICAL")

    @field_validator("confidence", mode="before")
    @classmethod
    def coerce_confidence(cls, value: object) -> float:
        """Coerce string/bool-ish numbers; anything outside 0..1 is invalidated."""
        if isinstance(value, bool):
            raise ValueError("confidence must be a numeric value")
        try:
            number = float(value)  # type: ignore[arg-type]
        except (TypeError, ValueError) as exc:
            raise ValueError("confidence must be a number in 0..1") from exc
        return number

    @model_validator(mode="after")
    def _check_line_bounds(self) -> AgentFinding:
        if (
            self.line_start is not None
            and self.line_end is not None
            and self.line_end < self.line_start
        ):
            raise ValueError("line_end must be >= line_start")
        return self

    def changed_file_key(self) -> str:
        """Files are matched by path (post-rename path for renames)."""
        return self.file_path


class AgentFindingsBatch(BaseModel):
    """Envelope the model is instructed to produce (one JSON object)."""

    model_config = ConfigDict(extra="forbid")

    findings: list[AgentFinding] = Field(default_factory=list)


@dataclass(frozen=True)
class FileSlice:
    """One trimmed diff window for an agent (docs/AGENTS.md §3).

    ``patch`` is the model-facing diff text with line numbers mapped back to the
    new-file absolute positions via ``new_start``/``new_end``.
    """

    file_path: str
    patch: str
    new_start: int | None
    new_end: int | None
    change_kind: ChangeKind
    old_path: str | None = None


@dataclass(frozen=True)
class AgentScope:
    """The frozen input envelope for one agent execution (mirrors JSONB ``scope``).

    ``review_id``/``repository_id`` are UUIDs in the database; they serialise to
    strings inside ``review_tasks.scope`` JSONB and are re-parsed on load.
    """

    review_id: uuid.UUID | str
    repository_id: uuid.UUID | str
    pr_number: int
    pr_title: str
    pr_description: str
    base_ref: str
    head_ref: str
    language: str | None = None
    changed_files: tuple[FileSlice, ...] = field(default_factory=tuple)
    repo_standards: tuple[str, ...] = field(default_factory=tuple)
    rag_context: tuple[str, ...] = field(default_factory=tuple)
    prompt_version: str = "1.0"

    @property
    def changed_paths(self) -> frozenset[str]:
        """Paths an agent is allowed to reference (post-rename paths)."""
        return frozenset(s.file_path for s in self.changed_files)

    def to_dict(self) -> dict[str, Any]:
        """JSON-safe round-trip for ``review_tasks.scope`` (docs/AGENTS.md §3)."""
        return {
            "review_id": str(self.review_id),
            "repository_id": str(self.repository_id),
            "pr_number": self.pr_number,
            "pr_title": self.pr_title,
            "pr_description": self.pr_description,
            "base_ref": self.base_ref,
            "head_ref": self.head_ref,
            "language": self.language,
            "prompt_version": self.prompt_version,
            "changed_files": [
                {
                    "file_path": s.file_path,
                    "patch": s.patch,
                    "new_start": s.new_start,
                    "new_end": s.new_end,
                    "change_kind": s.change_kind.value,
                    "old_path": s.old_path,
                }
                for s in self.changed_files
            ],
            "repo_standards": list(self.repo_standards),
            "rag_context": list(self.rag_context),
        }

    @classmethod
    def from_dict(cls, data: Mapping[str, Any]) -> AgentScope:
        """Rebuild a scope from ``review_tasks.scope`` JSONB."""
        raw_files = data.get("changed_files") or []
        slices = tuple(
            FileSlice(
                file_path=str(item["file_path"]),
                patch=str(item.get("patch", "")),
                new_start=_as_optional_int(item.get("new_start")),
                new_end=_as_optional_int(item.get("new_end")),
                change_kind=_coerce_change_kind(item.get("change_kind")),
                old_path=item.get("old_path"),
            )
            for item in raw_files
            if isinstance(item, dict) and item.get("file_path")
        )
        return cls(
            review_id=str(data["review_id"]),
            repository_id=str(data["repository_id"]),
            pr_number=int(data["pr_number"]),
            pr_title=str(data.get("pr_title", "")),
            pr_description=str(data.get("pr_description", "")),
            base_ref=str(data.get("base_ref", "main")),
            head_ref=str(data.get("head_ref", "")),
            language=data.get("language"),
            changed_files=slices,
            repo_standards=tuple(str(x) for x in data.get("repo_standards") or []),
            rag_context=tuple(str(x) for x in data.get("rag_context") or []),
            prompt_version=str(data.get("prompt_version", "1.0")),
        )


@dataclass
class BatchParseResult:
    """Outcome of parsing one model response into AgentFindings."""

    findings: list[AgentFinding] = field(default_factory=list)
    raw_count: int = 0
    valid_count: int = 0
    invalid_count: int = 0
    errors: list[str] = field(default_factory=list)


@dataclass
class AgentRunStats:
    """Per-execution accounting (mirrors ``agent_metrics`` columns)."""

    agent_key: str
    prompt_version: str
    model: str
    provider: str
    tokens_in: int
    tokens_out: int
    cost_usd: float
    findings_raw: int
    findings_valid: int
    failed_results: int
    duration_ms: int
    repaired: bool = False
    error: str | None = None

    def to_dict(self) -> dict[str, Any]:
        d: dict[str, Any] = {
            "agent_key": self.agent_key,
            "prompt_version": self.prompt_version,
            "model": self.model,
            "provider": self.provider,
            "tokens_in": self.tokens_in,
            "tokens_out": self.tokens_out,
            "cost_usd": self.cost_usd,
            "findings_raw": self.findings_raw,
            "findings_valid": self.findings_valid,
            "failed_results": self.failed_results,
            "duration_ms": self.duration_ms,
            "repaired": self.repaired,
        }
        if self.error is not None:
            d["error"] = self.error
        return d


@dataclass
class AgentResult:
    """Complete result of one agent execution (persistable by the orchestrator)."""

    agent_key: str
    scope: AgentScope
    findings: list[AgentFinding]
    stats: AgentRunStats


def parse_batch(
    content: str,
    *,
    changed_paths: Sequence[str],
    category_prefixes: Sequence[str],
) -> BatchParseResult:
    """Strictly parse + validate a model response (docs/AGENTS.md §4).

    - Non-JSON content raises ``AgentOutputError`` (the one-repair path).
    - Valid JSON with invalid items drops them and counts ``failed_results``.
    - For robustness the envelope may be the batch object itself or wrapped under
      a ``findings`` key; a bare list is also accepted.
    """
    try:
        data = json.loads(content)
    except (json.JSONDecodeError, TypeError) as exc:
        raise AgentOutputError(
            f"agent returned non-JSON output: {_short(content)}"
        ) from exc

    items = _extract_items(data)
    if items is None:
        raise AgentOutputError("agent output had no parseable 'findings' array")

    result = BatchParseResult(raw_count=len(items))
    valid_paths = _normalise_changed_paths(changed_paths)
    categories = tuple(category_prefixes)

    for item in items[:MAX_FINDINGS_PER_BATCH]:
        try:
            if not isinstance(item, dict):
                raise ValueError("each finding must be a JSON object")
            finding = AgentFinding.model_validate(item)
            if finding.file_path not in valid_paths:
                raise ValueError(
                    f"file '{finding.file_path}' is not in the changed files"
                )
            if categories and not _category_allowed(finding.category, categories):
                raise ValueError(
                    f"category '{finding.category}' is outside "
                    f"{' or '.join(categories)}"
                )
        except Exception as exc:  # noqa: BLE001 - per-item validation failure
            result.invalid_count += 1
            result.errors.append(str(exc))
            continue
        result.findings.append(finding)
        result.valid_count += 1

    # Anything past the cap is counted as failed so it is observable.
    overage = len(items) - MAX_FINDINGS_PER_BATCH
    if overage > 0:
        result.invalid_count += overage
        result.errors.append(f"trimmed {overage} findings over the batch cap")
    return result


def scope_validation_error(scope: AgentScope) -> str | None:
    """Return a human-readable reason why the scope cannot be run, or ``None``."""
    if not scope.changed_files:
        return "scope has no changed files to review"
    if not scope.head_ref:
        return "scope is missing the head ref"
    return None


def _extract_items(data: Any) -> list[Any] | None:
    """Accept ``{"findings": [...]}``, a bare ``[...]``, or a batch-wrap."""
    if isinstance(data, list):
        return data
    if not isinstance(data, dict):
        return None
    if "findings" in data and isinstance(data["findings"], list):
        return data["findings"]
    return None


def _normalise_changed_paths(paths: Sequence[str]) -> frozenset[str]:
    return frozenset(paths)


def _category_allowed(category: str, allowed: Sequence[str]) -> bool:
    """True when ``category`` is an allowed value or descends from one.

    Each entry in ``allowed`` is either an exact category (``security/xss``) or
    a broader prefix (``security``); both forms accept the category itself.
    """
    for entry in allowed:
        base = entry.rstrip("/")
        if category == base or category.startswith(base + "/"):
            return True
    return False


def _short(content: str, limit: int = 160) -> str:
    compact = " ".join(content.split())
    if len(compact) <= limit:
        return compact
    return compact[:limit] + "…"


def _as_optional_int(value: Any) -> int | None:
    if value is None or value == "":
        return None
    try:
        return int(value)
    except (TypeError, ValueError):
        return None


def _coerce_change_kind(value: Any) -> ChangeKind:
    if value is None:
        return ChangeKind.UNKNOWN
    for member in ChangeKind:
        if member.value == str(value).lower():
            return member
    return ChangeKind.UNKNOWN
