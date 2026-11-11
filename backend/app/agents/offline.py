"""Deterministic offline provider for ``ADAPTIVE_LLM_PROVIDER=mock``.

The queue-level ``MockLLMProvider`` in :mod:`app.agents.llm` only answers from a
pre-queued response list, which is right for unit tests but means a live stack
booted with the ``mock`` provider fails every agent with "ran out of queued
responses". This provider keeps the product runnable without an API key by
scanning the rendered diff for a small, documented set of rule patterns and
emitting schema-valid findings.

It is **not** an LLM and makes no claim to review quality: it exists to
exercise the real pipeline (agents -> consolidation -> ARUM -> gates ->
publisher) offline. Runs are recorded with ``provider="mock/heuristic"`` and
zero tokens/cost because no model call happens. Real review quality requires
``ADAPTIVE_LLM_PROVIDER=openai`` or ``anthropic`` with a key.
"""

from __future__ import annotations

import json
import re
from dataclasses import dataclass

from app.agents.llm import LLMResult, MockLLMProvider, TokenUsage

_FILE_HEADER = re.compile(
    r"^\d+\.\s+(?P<path>.+?)\s+\[(?P<kind>[a-z]+)"
    r"(?:;\s*new lines (?P<start>\d+)-(?P<end>\d+))?\]$"
)
_HUNK = re.compile(r"^@@ -\d+(?:,\d+)? \+(\d+)(?:,\d+)? @@")
_AGENT_KEY = re.compile(r"agent:\s*(?P<key>[a-z]+)")

_CALL_IN_LOOP = re.compile(
    r"(?i)(\.query\(|\.filter\(|\.all\(\)|\.first\(\)|"
    r"\.get\(|\.post\(|\.put\(|fetch\(|execute\()"
)
_LOOP = re.compile(r"^\s*(for|while)\s+")


@dataclass(frozen=True)
class _Rule:
    """One deterministic detection pattern."""

    pattern: re.Pattern[str]
    category: str
    severity: str
    confidence: float
    title: str
    description: str
    suggested_fix: str
    rule_id: str


def _rule(
    pattern: str,
    category: str,
    severity: str,
    confidence: float,
    title: str,
    description: str,
    suggested_fix: str,
    rule_id: str,
) -> _Rule:
    return _Rule(
        re.compile(pattern),
        category,
        severity,
        confidence,
        title,
        description,
        suggested_fix,
        rule_id,
    )


_RULES: dict[str, tuple[_Rule, ...]] = {
    "security": (
        _rule(
            r"(?i)\b(execute|executemany)\s*\(\s*f?[\"'].*\b(select|insert|update|delete)\b",
            "security/sql-injection",
            "critical",
            0.9,
            "SQL query built by interpolation",
            "A SQL statement is assembled from a string/format expression and "
            "executed; user-controlled input can change the query.",
            "Use a parameterised query (placeholder + bound parameters).",
            "sql-string-execute",
        ),
        _rule(
            r"\beval\s*\(|\bexec\s*\(",
            "security/code-injection",
            "critical",
            0.9,
            "Dynamic code execution",
            "eval/exec executes attacker-influenced data as code.",
            "Replace dynamic execution with an explicit parser or dispatch table.",
            "dynamic-exec",
        ),
        _rule(
            r"(?i)subprocess\.\w+\([^)]*shell\s*=\s*True|os\.system\s*\(",
            "security/command-injection",
            "high",
            0.85,
            "Shell command built from input",
            "A shell is invoked with string interpolation, enabling command "
            "injection if any part is user-controlled.",
            "Pass an argument list with shell=False.",
            "shell-injection",
        ),
        _rule(
            r"(?i)\b(password|passwd|secret|api_key|token)\b\s*=\s*[\"'][^\"']{4,}[\"']",
            "security/secrets",
            "critical",
            0.8,
            "Hardcoded credential",
            "A credential-like value is embedded in source; anyone with the "
            "repository can read it.",
            "Load the value from a secret manager or environment variable.",
            "hardcoded-secret",
        ),
        _rule(
            r"(?i)verify\s*=\s*False",
            "security/config",
            "high",
            0.8,
            "TLS verification disabled",
            "Certificate verification is turned off, allowing man-in-the-middle "
            "traffic to be accepted.",
            "Remove verify=False and trust the configured CA bundle.",
            "tls-verify-off",
        ),
        _rule(
            r"render_template_string\s*\(|Markup\s*\(",
            "security/xss",
            "high",
            0.75,
            "Unescaped template rendering",
            "User-controlled content is rendered without escaping and can inject "
            "markup/script.",
            "Render a static template with autoescaping enabled.",
            "template-xss",
        ),
        _rule(
            r"pickle\.loads\s*\(|yaml\.load\s*\(",
            "security/dependency",
            "high",
            0.7,
            "Unsafe deserialisation",
            "Deserialising untrusted bytes with pickle/yaml.load can execute "
            "arbitrary code.",
            "Use a safe loader (yaml.safe_load) or a non-executable format.",
            "unsafe-deserialise",
        ),
    ),
    "quality": (
        _rule(
            r"^\s*except(\s+Exception)?\s*:\s*$",
            "quality/error-handling",
            "medium",
            0.8,
            "Bare or swallowed exception",
            "The handler catches everything without recording or re-raising, so "
            "failures become invisible.",
            "Catch the expected exception type and log or re-raise.",
            "broad-except",
        ),
        _rule(
            r"(?i)\b(TODO|FIXME|XXX)\b",
            "quality/maintainability",
            "low",
            0.6,
            "Unresolved task marker",
            "A TODO/FIXME marker is committed without a tracked follow-up.",
            "Link the marker to an issue or resolve it before merge.",
            "todo-marker",
        ),
        _rule(
            r"^\s*print\s*\(",
            "quality/maintainability",
            "low",
            0.55,
            "Debug print in application code",
            "Print statements bypass structured logging and are easy to leave on "
            "in production.",
            "Use the project logger at an appropriate level.",
            "debug-print",
        ),
        _rule(
            r"==\s*None|!=\s*None",
            "quality/maintainability",
            "low",
            0.5,
            "Identity comparison via equality",
            "Comparing to None with ==/!= is ambiguous for overloaded equality.",
            "Use 'is None' / 'is not None'.",
            "none-equality",
        ),
    ),
    "performance": (
        _rule(
            r"(?i)\bSELECT\s+\*\s+FROM\b",
            "performance/query",
            "medium",
            0.6,
            "SELECT * fetches unused columns",
            "Selecting every column transfers and materialises data the caller "
            "does not use.",
            "Select only the columns required by the caller.",
            "select-star",
        ),
        _rule(
            r"range\s*\(\s*len\s*\(",
            "performance/loop",
            "low",
            0.55,
            "Index loop over a sequence",
            "range(len(...)) adds indexing overhead and obscures intent.",
            "Iterate the sequence directly (or use enumerate).",
            "range-len",
        ),
        _rule(
            r"(?i)\btime\.sleep\s*\(",
            "performance/api",
            "low",
            0.5,
            "Blocking sleep on the request path",
            "A fixed sleep holds a worker and adds latency; it is usually a "
            "symptom of polling or a race workaround.",
            "Replace with an event/await or a bounded retry with backoff.",
            "blocking-sleep",
        ),
    ),
    "architecture": (
        _rule(
            r"from\s+app\.api|from\s+app\.webhooks",
            "architecture/layering",
            "high",
            0.65,
            "Inner layer imports the delivery layer",
            "Importing API/webhook modules from a library or domain module "
            "inverts the dependency direction.",
            "Depend on an interface in the domain layer; inject the adapter.",
            "layer-inversion",
        ),
        _rule(
            r"sys\.path\.(append|insert)\s*\(",
            "architecture/coupling",
            "medium",
            0.6,
            "Import path patched at runtime",
            "Mutating sys.path makes imports depend on execution order.",
            "Package the code and import it normally.",
            "sys-path-mutation",
        ),
        _rule(
            r"^\s*global\s+\w+",
            "architecture/coupling",
            "low",
            0.5,
            "Mutable module-level global",
            "A global mutated from a function hides shared state and couples callers.",
            "Pass state explicitly or encapsulate it in a service object.",
            "global-state",
        ),
    ),
    "standards": (
        _rule(
            r"^\t|\t",
            "standards/formatting",
            "info",
            0.5,
            "Tab indentation",
            "The repository standard is spaces; tabs render inconsistently "
            "across tooling.",
            "Reformat with the project formatter (spaces).",
            "tab-indent",
        ),
        _rule(
            r"^\s*def\s+[A-Z]",
            "standards/naming",
            "low",
            0.6,
            "Function name is not snake_case",
            "Function names are expected to be snake_case per the Python "
            "convention used in this repository.",
            "Rename to snake_case.",
            "function-naming",
        ),
        _rule(
            r"^\s*class\s+[a-z]",
            "standards/naming",
            "low",
            0.6,
            "Class name is not PascalCase",
            "Class names are expected to be PascalCase.",
            "Rename to PascalCase.",
            "class-naming",
        ),
    ),
}

_LOOP_CALL_RULES = (
    _rule(
        r"(?i)\.(query|filter|all|first|fetch|execute)\s*\(",
        "performance/query",
        "high",
        0.75,
        "Database call inside a loop",
        "A query is issued once per iteration (N+1); cost grows with the input "
        "collection.",
        "Batch the lookup (join/prefetch/in-query) outside the loop.",
        "n-plus-one",
    ),
    _rule(
        r"(?i)\.(get|post|put|patch)\s*\(|requests\.\w+\s*\(|httpx\.\w+\s*\(",
        "performance/api",
        "medium",
        0.65,
        "Network call inside a loop",
        "Each iteration performs a remote call, multiplying latency.",
        "Batch the calls or move them outside the loop.",
        "remote-call-in-loop",
    ),
)


def _parse_files(user_prompt: str) -> list[tuple[str, list[tuple[int, str]]]]:
    """Extract ``(path, [(new_line, added_line)])`` from the rendered diff."""
    files: list[tuple[str, list[tuple[int, str]]]] = []
    current: list[tuple[int, str]] | None = None
    new_line = 0
    in_hunk = False
    for raw in user_prompt.splitlines():
        header = _FILE_HEADER.match(raw)
        if header:
            current = []
            files.append((header.group("path"), current))
            in_hunk = False
            new_line = int(header.group("start") or 0)
            continue
        if current is None:
            continue
        hunk = _HUNK.match(raw)
        if hunk:
            new_line = int(hunk.group(1))
            in_hunk = True
            continue
        if not in_hunk:
            continue
        if raw.startswith("+++") or raw.startswith("---"):
            continue
        if raw.startswith("+"):
            current.append((new_line, raw[1:]))
            new_line += 1
        elif raw.startswith(" "):
            new_line += 1
    return [(path, lines) for path, lines in files if lines]


class OfflineHeuristicProvider(MockLLMProvider):
    """Browse-free ``mock`` provider: rule scan over the prompt diff."""

    name = "mock/heuristic"
    _MAX_FINDINGS = 20

    def __init__(self, responses: list[object] | None = None) -> None:
        self._responses = list(responses or [])
        self.calls: list[dict[str, object]] = []

    async def complete(
        self,
        *,
        system_prompt: str,
        user_prompt: str,
        max_tokens: int | None = None,
    ) -> LLMResult:
        self.calls.append({"system_prompt": system_prompt, "user_prompt": user_prompt})
        if self._responses:
            queued = self._responses.pop(0)
            if isinstance(queued, BaseException):
                raise queued
            content = queued if isinstance(queued, str) else json.dumps(queued)
            return self._result(content)

        key = self._agent_key(system_prompt)
        findings = self._findings(key, user_prompt) if key else []
        return self._result(json.dumps({"findings": findings}))

    async def aclose(self) -> None:
        return None

    @staticmethod
    def _result(content: str) -> LLMResult:
        return LLMResult(
            content=content,
            model="mock-heuristic",
            provider=OfflineHeuristicProvider.name,
            usage=TokenUsage(prompt_tokens=0, completion_tokens=0),
            cost_usd=0.0,
        )

    @staticmethod
    def _agent_key(system_prompt: str) -> str | None:
        match = _AGENT_KEY.search(system_prompt)
        return match.group("key") if match else None

    def _findings(self, agent_key: str, user_prompt: str) -> list[dict[str, object]]:
        rules = _RULES.get(agent_key, ())
        findings: list[dict[str, object]] = []
        seen: set[tuple[str, int, str]] = set()
        for path, added in _parse_files(user_prompt):
            loop_indent: int | None = None
            for line_no, text in added:
                stripped = text.strip()
                if not stripped:
                    continue
                indent = len(text) - len(text.lstrip())
                if _LOOP.match(text):
                    loop_indent = indent
                elif loop_indent is not None and indent <= loop_indent:
                    loop_indent = None

                matched = False
                for rule in rules:
                    if not rule.pattern.search(text):
                        continue
                    finding = self._finding(path, line_no, text, rule)
                    key = (path, line_no, rule.category)
                    if key not in seen:
                        seen.add(key)
                        findings.append(finding)
                    matched = True
                    break
                if (
                    matched
                    or loop_indent is None
                    or indent <= loop_indent
                    or not _CALL_IN_LOOP.search(text)
                ):
                    continue
                for rule in _LOOP_CALL_RULES:
                    if not rule.pattern.search(text):
                        continue
                    finding = self._finding(path, line_no, text, rule)
                    key = (path, line_no, rule.category)
                    if key not in seen:
                        seen.add(key)
                        findings.append(finding)
                    break
                if len(findings) >= self._MAX_FINDINGS:
                    return findings
        return findings[: self._MAX_FINDINGS]

    @staticmethod
    def _finding(path: str, line_no: int, text: str, rule: _Rule) -> dict[str, object]:
        snippet = text.strip()[:200]
        return {
            "file_path": path,
            "line_start": line_no or None,
            "line_end": line_no or None,
            "category": rule.category,
            "severity": rule.severity,
            "confidence": rule.confidence,
            "title": rule.title,
            "description": rule.description,
            "evidence": {
                "snippet": snippet,
                "diff_hunk": None,
                "rule": rule.rule_id,
            },
            "suggested_fix": rule.suggested_fix,
            "reason_summary": f"{rule.rule_id}: `{snippet}`",
            "related_categories": [],
        }


__all__ = ["OfflineHeuristicProvider"]
