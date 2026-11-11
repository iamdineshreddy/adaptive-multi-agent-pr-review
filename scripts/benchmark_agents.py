"""Benchmark runner for the five review agents (docs/LLM_AGENT_BENCHMARK_REPORT.md).

Runs each of the five agents over a single controlled PR scope and records
execution success, findings, severity/confidence, latency, tokens, cost, errors
and retries. Supports the offline heuristic provider (default) and, when keys are
present, OpenAI/Anthropic. Never fabricates measurements: missing keys cause a
clear "pending" report instead of mock substitution.

Usage:
    python scripts/benchmark_agents.py --provider mock --out experiments/results/benchmark_mock.json
    python scripts/benchmark_agents.py --provider openai --diff datasets/samples/pr_diff.txt
"""

from __future__ import annotations

import argparse
import asyncio
import json
import os
import sys
import time
from pathlib import Path
from typing import Any
from uuid import uuid4

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "backend"))

from app.agents.contract import AgentScope, ChangeKind, FileSlice
from app.agents.llm import build_llm_provider
from app.agents.runner import run_agent
from app.config.settings import Settings, get_settings

AGENTS = ["security", "quality", "performance", "architecture", "standards"]


def _sample_scope(diff_text: str) -> AgentScope:
    return AgentScope(
        review_id=uuid4(),
        repository_id=uuid4(),
        pr_number=1,
        pr_title="Benchmark PR",
        pr_description="Controlled diff for agent benchmarking.",
        base_ref="main",
        head_ref="feat/benchmark",
        language="python",
        changed_files=(
            FileSlice(
                file_path="src/app.py",
                patch=diff_text,
                new_start=1,
                new_end=40,
                change_kind=ChangeKind.MODIFIED,
            ),
        ),
    )


def _settings_for(provider: str) -> Settings:
    base = get_settings()
    data = base.model_dump()
    data["llm_provider"] = provider
    if provider == "mock":
        data["llm_provider"] = "mock"
    return Settings(**data)


async def main_async(args: argparse.Namespace) -> dict[str, Any]:
    diff_text = Path(args.diff).read_text(encoding="utf-8") if args.diff else _DEFAULT_DIFF
    scope = _sample_scope(diff_text)
    settings = _settings_for(args.provider)
    provider = build_llm_provider(settings)

    report: dict[str, Any] = {
        "provider": args.provider,
        "provider_name": getattr(provider, "name", args.provider),
        "diff_source": str(args.diff) if args.diff else "built-in",
        "agents": {},
        "total_latency_ms": 0,
        "total_tokens_in": 0,
        "total_tokens_out": 0,
        "total_cost_usd": 0.0,
    }

    overall_start = time.perf_counter()
    for key in AGENTS:
        start = time.perf_counter()
        entry: dict[str, Any] = {"agent": key, "success": False}
        try:
            result = await run_agent(key, scope, provider=provider)
            elapsed_ms = int((time.perf_counter() - start) * 1000)
            entry.update(
                {
                    "success": True,
                    "findings": len(result.findings),
                    "severity_counts": {},
                    "latency_ms": elapsed_ms,
                    "tokens_in": result.stats.tokens_in,
                    "tokens_out": result.stats.tokens_out,
                    "cost_usd": result.stats.cost_usd,
                    "findings_detail": [
                        {
                            "category": f.category,
                            "severity": f.severity,
                            "confidence": f.confidence,
                            "file_path": f.file_path,
                            "line_start": f.line_start,
                        }
                        for f in result.findings
                    ],
                }
            )
            for f in result.findings:
                sev = str(f.severity)
                entry["severity_counts"][sev] = entry["severity_counts"].get(sev, 0) + 1
            report["total_tokens_in"] += result.stats.tokens_in
            report["total_tokens_out"] += result.stats.tokens_out
            report["total_cost_usd"] = round(
                report["total_cost_usd"] + result.stats.cost_usd, 8
            )
        except Exception as exc:  # noqa: BLE001 - benchmark must record failures
            entry["error"] = f"{type(exc).__name__}: {exc}"
            entry["latency_ms"] = int((time.perf_counter() - start) * 1000)
        report["agents"][key] = entry

    report["total_latency_ms"] = int((time.perf_counter() - overall_start) * 1000)
    if hasattr(provider, "aclose"):
        await provider.aclose()
    return report


_DEFAULT_DIFF = """@@ -1,20 +1,25 @@\n def login(user, password):\n-    query = \"SELECT * FROM users WHERE name = '\" + user + \"'\"\n+    query = f\"SELECT * FROM users WHERE name = '{user}'\"\n     cursor.execute(query)\n+    os.system('rm -rf ' + user_input)\n+    secret = 'AKIAIOSFODNN7EXAMPLE'\n+    for i in range(1000):\n+        print(i)\n+    data = pickle.loads(blob)\n     return True\n"""


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--provider", default="mock", choices=["mock", "openai", "anthropic"])
    parser.add_argument("--diff", default=None, help="path to a unified diff file")
    parser.add_argument("--out", default=None, help="output JSON path")
    args = parser.parse_args()

    # Guard: refuse paid providers without keys.
    if args.provider == "openai" and not os.getenv("ADAPTIVE_OPENAI_API_KEY"):
        print("ADAPTIVE_OPENAI_API_KEY is not set; cannot run openai benchmark.", flush=True)
        return 2
    if args.provider == "anthropic" and not os.getenv("ADAPTIVE_ANTHROPIC_API_KEY"):
        print("ADAPTIVE_ANTHROPIC_API_KEY is not set; cannot run anthropic benchmark.", flush=True)
        return 2

    report = asyncio.run(main_async(args))
    text = json.dumps(report, indent=2, default=str)
    if args.out:
        Path(args.out).parent.mkdir(parents=True, exist_ok=True)
        Path(args.out).write_text(text, encoding="utf-8")
        print(f"wrote {args.out}")
    else:
        print(text)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
