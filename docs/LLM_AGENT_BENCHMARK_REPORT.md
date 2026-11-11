# LLM Agent Benchmark Report

**Date:** 2026-11-11
**Status:** Benchmark infrastructure complete; **live LLM execution pending** (no API keys configured).

## 1. Environment

- Provider configured in `.env`: `ADAPTIVE_LLM_PROVIDER=mock`.
- `ADAPTIVE_OPENAI_API_KEY`: not set.
- `ADAPTIVE_ANTHROPIC_API_KEY`: not set.
- Pricing table: `backend/app/agents/llm.py` `MODEL_PRICING` (OpenAI + Anthropic models).
- Offline provider: `OfflineHeuristicProvider` (deterministic rule scan, zero tokens/cost).

Because no paid-provider credentials are present, the benchmark below runs the
offline heuristic provider only. OpenAI/Anthropic results are **not** fabricated;
they are reported as pending.

## 2. Benchmark infrastructure

- `scripts/benchmark_agents.py` — runs all five agents over a controlled diff,
  records per-agent findings, severity counts, latency, tokens, cost, errors.
- Supports `--provider mock|openai|anthropic`, `--diff`, `--out`.
- Refuses paid providers without keys (exit code 2).
- Output: JSON (e.g. `experiments/results/benchmark_mock.json`).

## 3. Controlled test diff

Built-in diff (also usable via `--diff`): SQL injection, command injection,
hardcoded secret, O(n) loop, pickle deserialization.

## 4. Offline (mock/heuristic) results

| Agent | Findings | Latency (ms) |
| --- | --- | --- |
| security | 3 | 3 |
| quality | 1 | 0 |
| performance | 1 | 0 |
| architecture | 0 | 0 |
| standards | 0 | 0 |

Total latency: 4 ms. Tokens: 0 in / 0 out. Cost: $0.00 (offline, no LLM call).

These are heuristic results and must not be reported as LLM performance.

## 5. Real LLM results

**Pending.** Requires:
- `ADAPTIVE_OPENAI_API_KEY` (for OpenAI) and/or `ADAPTIVE_ANTHROPIC_API_KEY`.
- Budget approval for paid API calls (estimated cost depends on diff size and model;
  pricing in `MODEL_PRICING`).
- Set `ADAPTIVE_LLM_PROVIDER=openai` (or `anthropic`) and run:
  `python scripts/benchmark_agents.py --provider openai --out experiments/results/benchmark_openai.json`

## 6. Memory and RAG validation

Memory/RAG/feedback logic is covered by the existing unit suite (memory snapshot
builder, temporal decay, RAG top-k, feedback identity, feedback flow,
MemoryOrchestratorStore). Cross-repository isolation is enforced by repository_id
scoping in the store. No new defects found during this phase.

## 7. Limitations

- No real LLM execution was performed (no credentials).
- Latency/token/cost figures above are for the offline heuristic only.
- End-to-end LLM-backed PR review not executed.

## 8. Reproduction

```
python scripts/benchmark_agents.py --provider mock --out experiments/results/benchmark_mock.json
```
