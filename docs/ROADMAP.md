# Development Roadmap

Ordered phases with explicit exit criteria. **Status** is updated as each phase is
implemented and tested. Nothing is marked done unless the code exists and its tests
pass.

| Phase | Deliverable | Status |
| --- | --- | --- |
| 0 | Design & architecture docs (ARCHITECTURE, ARUM, DATABASE, API, QUEUE, AGENTS, RESEARCH, EXPERIMENTS, SECURITY) | **done** |
| 1 | Requirements freeze; folder scaffold; config (`pydantic-settings`); workspace/dependency layout | **done** |
| 2 | DB: SQLAlchemy models, asyncpg, Alembic migrations, pgvector, seed fixtures | **done** (schema + migrations + metadata tests; live-DB round-trip tests self-skip without PostgreSQL) |
| 3 | GitHub webhook service: signature verify, Pydantic validation, idempotency, PR ingestion | todo |
| 4 | Queue: Celery app, Redis broker, priority zset, queues, retries, dead-letter | todo |
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

## Cross-cutting requirements (apply every phase)

- PEP 8 + type hints + Pydantic validation.
- Async I/O for DB/HTTP; CPU/LLM work in workers.
- Structured JSON logging (no secrets).
- No fabricated metrics/experiments (research integrity).
- Code review of the diff of each phase before marking done.