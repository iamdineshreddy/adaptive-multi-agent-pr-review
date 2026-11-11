# Known Limitations

- **Live LLM evaluation pending:** no OpenAI/Anthropic API keys configured.
  Offline heuristic provider is used; results are not LLM performance.
- **Agent-layer metrics** (latency, tokens, cost) not measured for real LLMs.
- **NDCG@5 and MRR** unavailable on the real corpus (no graded relevance labels);
  measurable only on the synthetic stress corpus.
- **Memory/RAG end-to-end** (review → feedback → re-review) validated at unit level
  only; live two-PR scenario pending.
- **Cross-repository isolation** validated at store level, not against a live DB.
- **Iteration delta** uses changed-file scope plumbing; dedicated GitHub diff-fetch
  seam is a documented later wiring pass.
- **Implicit GitHub signals** (reactions, dismissals) are documented capability only.
- **Frontend** is read-only dashboard; feedback submission via API only.
- **Real corpus** does not activate adaptive mechanisms (budget, critical gates,
  redundancy groups ≥3); synthetic stress corpus covers those.
