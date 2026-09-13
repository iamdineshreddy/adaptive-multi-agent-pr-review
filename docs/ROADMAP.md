# Development Roadmap

Ordered phases with explicit exit criteria. **Status** is updated as each phase is
implemented and tested. Nothing is marked done unless the code exists and its tests
pass.

| Phase | Deliverable | Status |
| --- | --- | --- |
| 0 | Design & architecture docs (ARCHITECTURE, ARUM, DATABASE, API, QUEUE, AGENTS, RESEARCH, EXPERIMENTS, SECURITY) | **done** |
| 1 | Requirements freeze; folder scaffold; config (`pydantic-settings`); workspace/dependency layout | **done** |
| 2 | DB: SQLAlchemy models, asyncpg, Alembic migrations, pgvector, seed fixtures | **done** (schema + migrations + metadata tests; live-DB round-trip tests self-skip without PostgreSQL) |
| 3 | GitHub webhook service: signature verify, Pydantic validation, idempotency, PR ingestion | **done** (`POST /api/v1/webhooks/github`, HMAC-SHA256/SHA1, rate limits, ping/unknown-event handling, SqlReviewIngester with delivery-id idempotency, priority scoring per QUEUE.md §2; dispatch is log-only until the Phase 4 broker — see below; 27 webhook tests + live-DB ingestion test that self-skips without PostgreSQL) |
| 4 | Queue: Celery app, Redis broker, priority zset, queues, retries, dead-letter | **done** (Celery app + Redis broker, queue routes from QUEUE.md §1, `RedisPriorityStore` zset scheduling, bounded backoff retries, `dead_letters` table + Review→FAILED with `failure_reason`, scheduler pop task; the orchestrator hand-off it pops for is Phase 6) |
| 5 | Agents: security, quality, performance, architecture, standards — structured output, LLM provider abstraction | **done** (`backend/app/agents/`: Pydantic `AgentFinding` contract + strict batch parse that drops-and-counts invalid items via `failed_results`; `LLMProvider` abstraction (OpenAI / Anthropic / mock) with bounded retry + fallback and per-call token/cost accounting; versioned system prompts with role-audit, delimited data blocks and `neutralise_instruction_markers`; unified-diff windowing per changed file (`diff_utils`); agent registry + `run_agent`; FR-1.3 GitHub REST client (`backend/app/github/`) with retryable/permanent error taxonomy and `build_scope_from_github`; `agents.run_agent` Celery task routed to `review_task_queue` as the Phase 6 fan-out seam. Persistence of findings/metrics and changed-file priority factors are Phase 6/7) |
| 6 | Supervisor + LangGraph orchestration: states/transitions, fan-out, retry/failure paths | **done** (`backend/app/orchestrator/`: LangGraph topology matching AGENTS.md §2 — `SUPERVISOR_PLAN → AGENT_FAN_OUT → RETRY_AGENTS` loop with bounded retries (max 2), hard per-agent timeout → permanent `FAILED` with `root_cause`, partial valid findings always preserved; JSON-safe state; `InProcessAgentRunner` (unit-tested classification) + `CeleryFanOutRunner` (production, joins `agents.run_agent` across the per-agent queues); `OrchestratorStore` boundary with `Memory` (tests) + `Sql` (PostgreSQL) implementations writing review status transitions (`status_history`), `supervisor_notes`, per-agent `review_tasks` (`queue_name`→agent queue, retry_count, last_error, scope) and `agent_metrics`; `orchestrator.run_review` Celery task on `review_task_queue` with live DB+GitHub scope rebuild (`scope_builder`); `queue.pop_and_dispatch` hands the popped priority review to the orchestrator; terminal mapping: graph COMPLETE with findings → `DECIDING` (checkpoint for the Phase 8 decision layer), no findings → `COMPLETED`, permanent failure → `FAILED`. Migration `0004_orchestrator_notes`; `orchestration` extra (`langgraph`). 43 orchestrator/queue tests + self-skipping live-DB SQL store tests) |
| 7 | Finding consolidation + cross-agent redundancy detection (embeddings + cosine + proximity) | **done** (`backend/app/consolidation/` — broker/DB-free kernel with DTOs in `datatypes.py`; embeddings provider protocol with deterministic hash-based `MockEmbeddingsProvider` (default, `text-embedding-3-small`-shaped 1536-dim L2-normalised) and lazy `OpenAIEmbeddingsProvider`, `build_embeddings_provider(settings)`; deterministic UUIDv5 findings/groups for reproducibility (EXPERIMENTS.md §5); redundancy = greedy hybrid: embedding cosine ≥ threshold (default 0.85) + category alignment (equal/prefix) + same file + line proximity (default max_line_gap 5) — only multi-member groups (cross-agent duplicates) persist `finding_groups` rows, singletons keep `duplicate_group` NULL, representative = highest confidence; research-safe method string `hybrid: embedding cosine + category + proximity`. Orchestrator wiring: `_persist` now consolidates candidates and lands on `DECIDING` (the Phase 8 checkpoint; no `completed_at`), permanent agent failures still persist partial valid findings then `FAILED`, consolidation/provider/persistence failures → `FAILED` with `root_cause`, all-invalid → `COMPLETED`; `OrchestratorStore` gains `create_findings` / `create_finding_group` / `create_embeddings` / `update_finding_duplicates` (bi-directional FK solved by findings → groups → duplicate-group write-back) for Memory + Sql; SQL embeddings use a server-side pgvector text-literal cast (`'[...]'::vector`) until the asyncpg codec lands in Phase 14; config `embeddings_provider=mock|openai`, `consolidation_similarity_threshold`, `consolidation_max_line_gap`. Migration `0005_embeddings_hnsw` (HNSW cosine index). 249 tests: 240 pass, 9 live-dependency self-skip) |
| 8 | ARUM: features, weights, scoring, LightGBM training path, logistic ablation, reproducibility log | todo |
| 9 | Review budget controller + safety gates | todo |
| 10 | Repository memory + RAG (pgvector retrieval of findings/standards/decisions) + temporal decay | todo |
| 11 | Developer feedback loop (API + GitHub implicit signals), memory updates, weight learning | todo |
| 12 | Iterative review engine (diff vs last-reviewed, resolve/stale/target, re-score) | todo |
| 13 | Frontend dashboard (React + TS + Tailwind): queues, reviews, findings, feedback, memory, metrics | todo |
| 14 | Observability: Langfuse traces, Prometheus metrics, Grafana dashboards, structured logs | todo |
| 15 | Security hardening: auth, authz, rate limiting, injection defences, secret handling, audit | todo |
| 16 | Testing: unit, integration, API, queue, agent, DB, RAG, adaptive, webhook; failure scenarios | todo |
| 17 | Experiments: dataset (`github-codereview`) preprocessing, baselines, ablation, results, plots | todo |
| 18 | Deployment: docker-compose (api/worker/scheduler/redis/postgres/frontend/prometheus/grafana) | todo |
| 19 | Research documentation: ARCHITECTURE.md refinement, RESEARCH.md, EXPERIMENTS.md, final README | todo |

Phase gating rule: **no phase starts until the previous phase's tests pass.** When a
phase cannot be completed as specified, it is split and documented (never silently
cut).

Phase 3 scope note (deliberate deferral): **FR-1.3 (GitHub API client fetching PR
metadata, changed files, diffs, and commits)** is deliberately deferred to Phase 5,
where the ingestion worker needs that data for analysis. The Phase 3 webhook only
consumes what GitHub sends in the event payload; changed-file-driven priority
factors (security, dependency, component) therefore stay at their documented 0.0
baseline until then.

Phase 4 scope note: the **priority zset, scheduler, and orchestrator fan-out are
wired end-to-end** (webhook → `pr_ingestion_queue` → zset → `queue.pop_and_stage`
→ `queue.pop_and_dispatch` → `orchestrator.run_review` on `review_task_queue`).
Nobody pretends agents ran when they did not: unit + live-DB tests cover the
cores, and broker delivery itself is exercised in Phase 16 integration. Without
a broker, dispatch falls back to the Phase 3 log-only provider via
`queue_dispatch_provider=logging` (default is `celery`, which requires a
reachable broker and surfaces enqueue failure as a 500).

## Cross-cutting requirements (apply every phase)

- PEP 8 + type hints + Pydantic validation.
- Async I/O for DB/HTTP; CPU/LLM work in workers.
- Structured JSON logging (no secrets).
- No fabricated metrics/experiments (research integrity).
- Code review of the diff of each phase before marking done.