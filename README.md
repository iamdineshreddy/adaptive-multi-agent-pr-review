# Adaptive Multi-Agent AI System for GitHub Pull Request Review

A production-oriented research prototype that reviews GitHub Pull Requests with an
asynchronous, queue-driven, multi-agent architecture. The system separates **issue
detection** from **finding selection** and adapts to developer feedback through
repository-specific review memory.

This project exists to solve two problems:

1. Detect potential issues in a Pull Request.
2. Decide which detected issues are actually worth showing to developers.

**Problem 2 is the main research contribution.**

---

## Why this is not a "basic AI code reviewer"

Existing AI code-review systems generate findings but commonly suffer from:

- too many comments per PR
- duplicate findings from different agents/prompts
- generic recommendations that ignore repository context
- false positives and low-priority noise
- repeated comments for previously rejected issues
- no learning from developer feedback

This system treats **selection** as a first-class, measurable research problem rather
than an afterthought on top of an LLM prompt.

---

## Core research idea

A strict separation is enforced architecturally between:

| Layer | Question | Artefact |
| --- | --- | --- |
| **Detection** | "What problems might exist?" | Candidate findings (raw, unfiltered) |
| **Decision** | "Which findings should actually be shown?" | Published findings (budget-constrained, ranked, deduplicated) |

The decision layer is implemented as a measurable scoring mechanism called the
**Adaptive Review Utility Model (ARUM)** — not as an unstructured "ask the LLM to rank
everything" prompt.

See `docs/ARUM.md`.

---

## High-level architecture

```text
GitHub PR (webhook event)
        |
        v
  Webhook Service  (HMAC verify, validate, idempotency)
        |
        v
  FastAPI  ->  priority score -> pr_ingestion_queue (Celery/Redis)
        |
        v
  Priority zset  ->  scheduler pops highest -> orchestrator.run_review
        |
        v
  Orchestrator (LangGraph state machine)
    1. Supervisor plans the fan-out
    2. Agent task queue -> [security | quality | performance | architecture | standards]
                          (parallel workers, bounded retries, hard timeouts)
    3. Finding consolidation + cross-agent redundancy detection
    4. ARUM decision layer (8 features, weighted utility, deterministic rank)
    5. Adaptive review budget + safety gates (critical findings never silenced)
        |
        v
  DECIDING checkpoint  (publication to GitHub arrives in a later phase)
        |
        v
  Developer feedback  -> repository memory (decay) -> RAG -> weight learning
        |
        v
  Next review  -> iterative re-review of changed regions only
```

Full details: `docs/ARCHITECTURE.md`.

---

## Feature summary

- GitHub webhook ingestion with signature verification
- Pull Request priority queue (configurable weights, documented scoring)
- Celery + Redis worker architecture (async, parallel, retryable, dead letters)
- Five specialized review agents + a Supervisor, LangGraph-orchestrated
- Structured finding data model (no hidden chain-of-thought exposed)
- Cross-agent semantic redundancy detection (embeddings + cosine + proximity)
- ARUM with eight dimensional features, versioned weights, deterministic ranking
- Adaptive review budget controller with non-negotiable safety gates
- Developer feedback loop (feedback and "code changed" kept conceptually separate)
- Repository-specific adaptive memory with temporal decay
- pgvector RAG over historical findings, standards, and resolutions
- Targeted iterative review across PR updates (resolved / stale / keep)
- Agent disagreement handling
- FastAPI + SQLAlchemy + asyncpg + Alembic
- Bearer-token auth/authz on the dashboard surface (Phase 15)
- Langfuse / Prometheus / Grafana observability (+ a worker-scrape exporter)
- React + TypeScript + Tailwind dashboard
- Docker Compose deployment (api / worker / scheduler / redis / postgres /
  frontend / prometheus / grafana)
- Reproducible experiment harness (label pre-processing, baselines, ablations)

---

## Quick start

See `docs/SETUP.md` and `docs/DEPLOYMENT.md`.

Local:

```bash
cd backend
python -m venv .venv
pip install -e ".[dev,all]"
cp ../.env.example .env
alembic upgrade head                # needs PostgreSQL
uvicorn app.main:app --reload       # http://localhost:8000/api/docs
pytest -q                           # 490 passed / 11 skipped (no infra)
```

Full stack (needs a Docker host):

```bash
cp .env.example .env                # fill tokens/secrets
docker compose up --build -d        # dashboard :80, grafana :3000, prometheus :9090
```

---

## Repository layout

```text
backend/
    app/
        adaptive/       ARUM: features, weights, scoring, selection gates,
                        decay, RAG, feedback, learning
        agents/         Five review agents, prompts, LLM provider, diff tooling
        api/            FastAPI routes (dashboard, feedback, monitoring)
        consolidation/  Cross-agent redundancy grouping (embeddings + proximity)
        experiments/    Selection-layer experiment harness (modes, metrics, runner)
        github/         Webhook verification + GitHub REST client / scope building
        models/         SQLAlchemy ORM models + enums
        webhooks/       Ingest, priority scoring, dispatch, rate limiting
        orchestrator/   LangGraph state machine, store, iteration engine
        queue/          Celery app, tasks, priority zset, dead letters
        security/       Bearer tokens, authz, rate limiting
        middleware/     Body-limit enforcement
        database/       Async engine, session factory
        config/         pydantic-settings configuration
        monitoring/     Prometheus, Langfuse, structured logging
    tests/              490 tests (unit + gated live-service lanes)
    alembic/            Schema migrations
frontend/               React + TypeScript + Tailwind dashboard (Vite)
infra/
    docker/             Backend + frontend Dockerfiles, entrypoints, nginx conf
    prometheus/         Scrape config (api + worker exporters)
    grafana/            Provisioning + adaptive-review dashboard
docs/                   Design + research documentation
experiments/            Preprocessing, run_all, sample corpus (Results pending)
datasets/               Dataset registration + labelling rules
docker-compose.yml      Full platform stack
```

---

## Current status

All roadmap phases are implemented and gated except the parts that are
explicitly **deferred to a part 2** (documented in `docs/ROADMAP.md`, never
silently cut):

- **Phases 0–15** done: design, ingestion, queue, agents, orchestration,
  consolidation, ARUM, budget gates, memory+RAG, feedback+learning, iterative
  review, dashboard, observability, security.
- **Phase 16 part 1** done: QA gates codified — `docs/TESTING.md` (suite lanes,
  gate commands, live-service self-skip), failure-scenario coverage map, queue /
  webhook / pipeline test seams. **Part 2** (worker-crash-under-broker; broker
  transport run) needs the Phase 18 stack.
- **Phase 17 part 1** done: reproducible experiment harness (`app/experiments` +
  `experiments/`) with label pre-processing, B1–B5 + A1–A6 ablation modes over
  the real `app.adaptive` machinery, and 39 harness tests. **Part 2** (the
  `github-codereview` ingestion under license review and an executed results
  table + plots) awaits the real dataset.
- **Phase 18 part 1** done: Docker Compose topology, images, worker metrics
  exporter, beat scheduler, deployment runbook. **Part 2** (live
  `docker compose up` boot smoke + the Phase 16 part 2 broker run) needs a
  Docker host and has not been executed.
- **Phase 19** done: this README, `docs/ARCHITECTURE.md` refinement,
  `docs/RESEARCH.md`, `docs/EXPERIMENTS.md` finalisation.

**Nothing not implemented is claimed.** References to publication, GitHub comment
posting, and experimental numbers that have not been produced are labelled as
upcoming or "Results pending experimental validation".

Suite today: **490 passed / 11 skipped** (10 PostgreSQL, 1 PostgreSQL+Redis —
both lanes self-skip when the services are unreachable), ruff/mypy clean, 84%
line coverage over `backend/app`. See `docs/TESTING.md`.

---

## Documentation index

| Document | Purpose |
| --- | --- |
| `docs/ARCHITECTURE.md` | System architecture, components, data flow, decision log |
| `docs/ARUM.md` | Adaptive Review Utility Model design |
| `docs/DATABASE.md` | PostgreSQL ER design, indexes, migrations |
| `docs/API.md` | HTTP API specification |
| `docs/QUEUE.md` | Queue architecture and priority policy |
| `docs/AGENTS.md` | Agent design, prompts policy, outputs |
| `docs/ROADMAP.md` | Development phases, status, split notes |
| `docs/EXPERIMENTS.md` | Evaluation methodology, baselines, ablation |
| `docs/RESEARCH.md` | Research positioning, related work, integrity statement |
| `docs/SECURITY.md` | Security model and threat considerations |
| `docs/TESTING.md` | QA gates, suite lanes, coverage baseline |
| `docs/SETUP.md` | Local development setup |
| `docs/DEPLOYMENT.md` | Compose deployment + operations runbook |

---

## Research integrity

The complete project incl. dataset (`github-codereview`), baselines, ablation studies,
and reproducible experiment scripts is documented. **No experimental results will be
fabricated.** If an experiment has not been executed, the corresponding report states
"Results pending experimental validation". See `docs/RESEARCH.md` for the contribution
statement and citation policy.