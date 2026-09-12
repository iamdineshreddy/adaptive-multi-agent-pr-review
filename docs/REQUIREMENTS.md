# Requirements (Frozen — Phase 1)

This document freezes the requirements for the **Adaptive Multi-Agent AI System for
GitHub Pull Request Review**. It is derived from the project brief and the Phase 0
design docs (`docs/ARCHITECTURE.md`, `docs/ARUM.md`, `docs/DATABASE.md`, `docs/API.md`,
`docs/QUEUE.md`, `docs/AGENTS.md`). If a requirement changes, this document changes
first and is version-bumped.

## 1. Functional requirements

### FR-1 GitHub integration
- FR-1.1 Accept GitHub `pull_request` webhook events (`opened`, `synchronize`,
  `ready_for_review`, `reopened`) without blocking on AI work.
- FR-1.2 Verify `X-Hub-Signature-256` and reject invalid signatures.
- FR-1.3 Fetch PR metadata, changed files, diffs, and commits via the GitHub API.
- FR-1.4 Publish review comments as a single review, line-anchored, rate-limited.
- FR-1.5 Never expose GitHub tokens; secrets from env/secrets manager.

### FR-2 Queueing and priority
- FR-2.1 Multi-PR support via Celery + Redis with the queues in `docs/QUEUE.md`.
- FR-2.2 Priority score from documented, configurable weights; `risk_class`
  mapped to review budget.
- FR-2.3 Retry with backoff, dead-letter, and `FAILED` terminal state with reason.

### FR-3 Orchestration and agents
- FR-3.1 LangGraph state machine covering the review lifecycle and its failure paths.
- FR-3.2 Five specialised agents (security, quality, performance, architecture,
  standards) plus supervisor, executing in parallel.
- FR-3.3 Structured, Pydantic-validated agent output; no chain-of-thought exposure.
- FR-3.4 Agent disagreement detection with supervisor escalation.

### FR-4 Findings and selection
- FR-4.1 Finding data model per `docs/DATABASE.md` §3.7.
- FR-4.2 Cross-agent semantic redundancy grouping (embeddings + cosine + proximity).
- FR-4.3 ARUM scoring (8 features, configurable weights, versioned).
- FR-4.4 Review budget controller with safety gates protecting critical findings.
- FR-4.5 Full decision trace recorded for every publish/suppress decision.

### FR-5 Feedback and memory
- FR-5.1 Capture explicit developer outcomes (ACCEPTED/FIXED/MODIFIED/REJECTED/
  DISMISSED/IGNORED/DISCUSSION) via API.
- FR-5.2 Treat "code changed" as a signal, never as auto-acceptance.
- FR-5.3 Update per-repository memory with temporal decay (τ, λ configurable).
- FR-5.4 RAG retrieval over past findings, decisions, and standards (pgvector).

### FR-6 Iterative review
- FR-6.1 Diff-aware re-review: resolve/stale/keep findings per new commit.
- FR-6.2 Targeted agent invocation on changed regions only.
- FR-6.3 Recompute ARUM for affected findings; publish only delta.

### FR-7 Observability and admin
- FR-7.1 Langfuse LLM traces; Prometheus metrics; Grafana dashboards.
- FR-7.2 Dashboard: queues, reviews, findings, feedback, memory, metrics.
- FR-7.3 Rerun endpoint and repository settings endpoint with authorisation.

## 2. Non-functional requirements

### NFR-1 Performance
- NFR-1.1 Webhook→queued response < 500 ms p95 under load.
- NFR-1.2 Parallel agent execution; per-queue isolation; back-pressure gauges.

### NFR-2 Reliability
- NFR-2.1 Idempotent webhook deliveries and task dispatch.
- NFR-2.2 Provider failures → fallback + bounded retries; review never silently
  reported complete.
- NFR-2.3 A `synchronize` supersedes an in-flight review of the same PR (repo lock).

### NFR-3 Security
- NFR-3.1 No secrets in code/logs; structured logging with redaction.
- NFR-3.2 Repository isolation enforced at query level.
- NFR-3.3 Prompt-injection defences for untrusted diffs/PR text.
- NFR-3.4 Rate limiting + input validation on public endpoints.

### NFR-4 Quality & maintainability
- NFR-4.1 PEP 8, type hints, Pydantic validation, async DB/HTTP I/O.
- NFR-4.2 All modules unit/integration tested (see `docs/ROADMAP.md` Phase 16).
- NFR-4.3 No placeholder functions presented as features.

### NFR-5 Research integrity
- NFR-5.1 No fabricated metrics; "pending experimental validation" unless run.
- NFR-5.2 Reproducible experiments; versioned ARUM weights; decision traces.
- NFR-5.3 Baselines + ablation pipeline in one codebase (mode switches).

## 3. Out of scope (explicit)

- Auto-merging PRs or auto-approving reviews.
- Replacing human reviewers or assigning legal/process decisions.
- Running arbitrary code contained in PR diffs.
- Publishing unsolicited comments on unrelated code outside the PR diff.

## 4. Requirement → PHASE mapping

Each requirement is traced to one or more roadmap phases in `docs/ROADMAP.md`.
Compliance is demonstrated by tests added in that phase.

| Req | Phase(s) |
| --- | --- |
| FR-1 | 3, 11 (publisher part of 3? publisher queue 11) |
| FR-2 | 4 |
| FR-3 | 5, 6 |
| FR-4 | 7, 8, 9 |
| FR-5 | 10, 11 |
| FR-6 | 12 |
| FR-7 | 13, 14 |
| NFR-1,2 | 4, 14, 18 |
| NFR-3 | 3, 11, 15 |
| NFR-4,5 | 16, 17 |