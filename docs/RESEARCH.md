# Research Positioning & Integrity

## Contribution statement (defensible)

> This work proposes an integrated adaptive review architecture that separates issue
> detection from finding selection and combines repository-specific feedback memory,
> redundancy reduction, adaptive review budgeting, and targeted iterative analysis
> within an asynchronous GitHub Pull Request workflow.

We do **not** claim: "first AI code reviewer", "first multi-agent reviewer",
"no existing system does this", or "100% unique". Related work (AI code-review tools,
multi-agent LLM systems, learning-to-rank for QA, code review datasets) is cited and
compared against directly in the final paper.

## Novel aspect

The emphasis is a measurable separation of detection vs decision plus a feedback-loop
selection model (ARUM) evaluated with ablations — positioned as an integrated-system
contribution, not a "new unheard-of feature".

## Integrity rules

1. No fabricated experiments, metrics, citations, or claims.
2. Cite existing methods (e.g. learn-to-rank, pgvector, LangGraph, Langfuse patterns)
   and name alternatives we compare against.
3. Document limitations honestly (dataset distribution, LLM nondeterminism, cost).
4. Reproducibility: scripts + seeds + version pinning in `experiments/`.
5. Any unpublished result is labelled "pending experimental validation".

## Paper scaffolding (to be filled only with real results)

- Architecture diagram (AD for review lifecycle)
- ARUM formulation + inference
- Baselines / ablation tables
- Cost & latency analysis
- Ethical considerations (bias in datasets, review workload, suppression policy)