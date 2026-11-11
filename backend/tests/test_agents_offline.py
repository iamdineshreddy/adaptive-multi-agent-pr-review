"""Offline (``mock``) provider heuristics for the live pipeline (Phase 18).

These tests pin the behaviour that makes ``ADAPTIVE_LLM_PROVIDER=mock`` usable
end to end: every built-in agent receives a schema-valid batch derived from the
diff, and the run reports zero tokens/cost because no model was called.
"""

from __future__ import annotations

import json
import uuid

import pytest

from app.agents.contract import AgentScope, ChangeKind, FileSlice
from app.agents.llm import MockLLMProvider, build_llm_provider
from app.agents.offline import OfflineHeuristicProvider
from app.agents.runner import run_agent
from app.config.settings import Settings


def _scope(patch: str, file_path: str = "app/service.py") -> AgentScope:
    return AgentScope(
        review_id=uuid.uuid4(),
        repository_id=uuid.uuid4(),
        pr_number=7,
        pr_title="Offline provider test",
        pr_description="",
        base_ref="main",
        head_ref="feature",
        language="python",
        changed_files=(
            FileSlice(
                file_path=file_path,
                patch=patch,
                new_start=1,
                new_end=20,
                change_kind=ChangeKind.MODIFIED,
            ),
        ),
    )


def test_mock_mode_builds_offline_provider() -> None:
    provider = build_llm_provider(Settings(llm_provider="mock"))
    assert isinstance(provider, OfflineHeuristicProvider)
    assert isinstance(provider, MockLLMProvider)


async def test_security_agent_finds_injection_and_secret() -> None:
    scope = _scope(
        "@@ -1,2 +1,4 @@\n"
        " import sqlite3\n"
        '+password = "hunter2secret"\n'
        "+eval(user_input)\n"
        "+cursor.execute(\"SELECT * FROM users WHERE name = '%s'\" % name)\n"
    )
    result = await run_agent("security", scope)
    categories = {f.category for f in result.findings}
    assert "security/secrets" in categories
    assert "security/code-injection" in categories
    assert "security/sql-injection" in categories
    assert result.stats.provider == "mock/heuristic"
    assert result.stats.tokens_in == 0
    assert result.stats.tokens_out == 0
    assert result.stats.cost_usd == 0.0


async def test_quality_agent_finds_broad_except() -> None:
    scope = _scope(
        "@@ -1,2 +1,5 @@\n def f():\n+    try:\n+        work()\n"
        "+    except Exception:\n+        pass\n"
    )
    result = await run_agent("quality", scope)
    assert [f.category for f in result.findings] == ["quality/error-handling"]


async def test_performance_agent_finds_query_in_loop() -> None:
    scope = _scope(
        "@@ -1,2 +1,4 @@\n def load(rows, db):\n+    for row in rows:\n"
        '+        db.query("select id from items where id = ?", row.id)\n'
    )
    result = await run_agent("performance", scope)
    assert [f.category for f in result.findings] == ["performance/query"]


async def test_architecture_agent_finds_layer_inversion() -> None:
    scope = _scope(
        "@@ -1,2 +1,3 @@\n import os\n+from app.api import router\n+global cache\n",
        file_path="app/domain/service.py",
    )
    result = await run_agent("architecture", scope)
    categories = {f.category for f in result.findings}
    assert "architecture/layering" in categories
    assert "architecture/coupling" in categories


async def test_standards_agent_finds_naming_and_tabs() -> None:
    scope = _scope(
        "@@ -1,2 +1,3 @@\n import os\n+def GetThing():\n+\treturn os.getcwd()\n"
    )
    result = await run_agent("standards", scope)
    categories = {f.category for f in result.findings}
    assert "standards/naming" in categories
    assert "standards/formatting" in categories


async def test_clean_diff_returns_no_findings() -> None:
    scope = _scope(
        "@@ -1,2 +1,3 @@\n def add(a, b):\n-    return a - b\n+    return a + b\n"
    )
    provider = OfflineHeuristicProvider()
    result = await run_agent("security", scope, provider=provider)
    assert result.findings == []
    assert len(provider.calls) == 1


async def test_queued_responses_take_precedence() -> None:
    provider = OfflineHeuristicProvider([json.dumps({"findings": []})])
    result = await provider.complete(
        system_prompt="agent: security", user_prompt="+eval(x)"
    )
    assert json.loads(result.content) == {"findings": []}


@pytest.mark.parametrize(
    "agent_key", ["security", "quality", "performance", "architecture", "standards"]
)
async def test_every_agent_returns_valid_empty_batch(agent_key: str) -> None:
    """No pattern match must still be a valid batch, not a provider error."""
    scope = _scope("@@ -1,2 +1,3 @@\n def add(a, b):\n+    return a + b\n")
    result = await run_agent(agent_key, scope)
    assert result.agent_key == agent_key
    assert result.stats.findings_valid == 0
    assert result.stats.failed_results == 0
