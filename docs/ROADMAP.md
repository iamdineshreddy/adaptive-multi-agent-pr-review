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
| 5 | Agents: security, quality, performance, architecture, standards — structured output, LLM provider abstraction | todo |
| 6 | Supervisor + LangGraph orchestration: states/transitions, fan-out, retry/failure paths | todo |
| 7 | Finding consolidation + cross-agent redundancy detection (embeddings + cosine + proximity) | todo |
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

Phase 4 scope note: the **priority zset and scheduler are implemented and wired**
(webhook → `pr_ingestion_queue` → zset → `queue.pop_and_stage`), but the popped
review is handed to the orchestrator in Phase 6 (LangGraph). Until then
`queue.pop_and_stage` records the hand-off intent and returns the review id —
it does not pretend to run agents. Without a broker, dispatch falls back to the
Phase 3 log-only provider via `queue_dispatch_provider=logging` (default is
`celery`, which requires a reachable broker and surfaces enqueue failure as a 500).

## Cross-cutting requirements (apply every phase)

- PEP 8 + type hints + Pydantic validation.
- Async I/O for DB/HTTP; CPU/LLM work in workers.
- Structured JSON logging (no secrets).
- No fabricated metrics/experiments (research integrity).
- Code review of the diff of each phase before marking done.