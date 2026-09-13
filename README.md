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
GitHub Pull Request
        ↓
     Webhook
        ↓
     FastAPI
        ↓
   Priority Queue
        ↓
   Celery Workers
        ↓
 ┌─────────────────────────────┐
 │       Supervisor Agent      │
 └─────────────┬───────────────┘
               ↓
       Agent Task Queue
               ↓
 ┌───────┬────────┬──────────┬────────────┐
 ↓       ↓        ↓          ↓            ↓
Security Quality Performance Architecture Standards
 │       │        │          │            │
 └───────┴────────┴─────┬────┴────────────┘
                       ↓
             Finding Consolidation
                       ↓
             Redundancy Filtering
                       ↓
            Adaptive Decision Layer (ARUM)
                       ↓
             Adaptive Review Budget
                       ↓
             GitHub Review Comment
                       ↓
               Developer Action
                       ↓
        Accept / Reject / Modify / Dismiss / Ignore
                       ↓
             PostgreSQL Memory
                       ↓
                RAG Retrieval
                       ↓
          ┌──────────────────────┐
          │  Adaptive Learning   │
          └──────────┬───────────┘
                     ↓
             Next Pull Request
                     ↺
```

Full details: `docs/ARCHITECTURE.md`.

---

## Feature summary

- GitHub webhook ingestion with signature verification
- Pull Request priority queue (configurable weights, documented scoring)
- Celery + Redis worker architecture (async, parallel, retryable)
- Five specialized review agents + a Supervisor agent
- LangGraph-based orchestration with explicit states/transitions/failure paths
- Structured finding data model (no hidden chain-of-thought exposed)
- Cross-agent semantic redundancy detection (embeddings + cosine similarity)
- Adaptive Review Utility Model (ARUM) with configurable weights
- Adaptive review budget controller with safety gates for critical findings
- Developer feedback loop (feedback and "code changed" kept conceptually separate)
- Repository-specific adaptive memory with temporal decay
- pgvector RAG over historical findings, standards, and resolutions
- Targeted iterative review across PR updates
- Agent disagreement handling
- FastAPI + SQLAlchemy + asyncpg + Alembic
- Langfuse / Prometheus / Grafana observability
- React + TypeScript + Tailwind dashboard
- Docker-based deployment (docker-compose)
- Experiment pipeline with baseline comparisons and ablation studies

---

## Repository layout

```text
backend/
    app/
        api/            FastAPI route handlers
        agents/         Specialized review agents + Supervisor
        orchestration/  LangGraph state machines
        queue/          Celery tasks, priorities, retries
        adaptive/       ARUM, review budget, temporal memory
        rag/            Embeddings, vector retrieval, pgvector
        github/         Webhook verification, PR client, comment publisher
        database/       SQLAlchemy engine, sessions, Alembic
        models/         ORM models
        schemas/        Pydantic schemas
        services/       Domain services
        security/       Auth, secrets, rate limiting, sanitisation
        monitoring/     Metrics, tracing, logging
        config/         Settings (pydantic-settings)
    tests/
frontend/
    src/
        components/
        pages/
        services/
        hooks/
        types/
worker/             Celery app entrypoints / beat scheduler
infra/
    docker/          Dockerfiles + docker-compose
    prometheus/
    grafana/
docs/               Design and research documentation
experiments/        Experiment scripts, metrics, plots
datasets/           Dataset registration and processing scripts
```

---

## Current status

**Phase 10 complete — repository memory + RAG + temporal decay; the four previously-zeroed ARUM features (`historical_actionability`, `historical_rejection`, `repository_relevance`, `context_relevance`) now score from developer-feedback memory and pgvector retrieval of standards/resolved findings. Publication happens in the phase that actually publishes.**

- Phase 0: full design documentation (`docs/`).
- Phase 1: requirements frozen (`docs/REQUIREMENTS.md`), backend package installed
  (`adaptive-review` 0.1.0, editable), application shell with `/health`,
  `pydantic-settings` config layer with production secret guard, lint/mypy/tests green.
- Phase 2: SQLAlchemy 2.0 async models for all 15 tables (`backend/app/models/`),
  async engine/session factory (`backend/app/database/`), Alembic migrations with
  pgvector (offline SQL validated via `alembic upgrade head --sql`), delivery-id
  idempotency column (`0002_github_delivery_id`). 22 tests: 20 pass (2 live-DB
  round-trips self-skip until PostgreSQL is reachable), plus health/settings tests.
- Phase 3: `POST /api/v1/webhooks/github` (`backend/app/webhooks/`) — HMAC-SHA256/SHA1
  signature verification, Pydantic validation (422), rate limiting, delivery-id
  idempotency, PR ingestion into PostgreSQL, and priority scoring per `docs/QUEUE.md`
  §2. Dispatch is log-only until the Phase 4 broker. 50 tests: 47 pass, 3 live-DB
  tests self-skip without PostgreSQL. FR-1.3 (GitHub API client) is intentionally
  deferred to Phase 5 — see `docs/ROADMAP.md`.
- Phase 4: Celery app + Redis broker (`backend/app/queue/`) with the QUEUE.md §1
  routes, Redis sorted-set priority scheduling (`RedisPriorityStore`), bounded
  backoff retries, a durable `dead_letters` table + terminal Review→`FAILED` with
  `failure_reason` (`0003_queue_dead_letter`), and `CeleryDispatcher` publishing to
  `pr_ingestion_queue`. Scheduler pop-and-stage hands off to the Phase 6
  orchestrator. 66 tests: 60 pass (6 live-dependency tests self-skip without
  PostgreSQL/Redis).
- Phase 5: Specialized review agents (`backend/app/agents/`) — a strict Pydantic
  `AgentFinding` output contract with batch validation that drops-and-counts invalid
  findings (`failed_results`), an `LLMProvider` abstraction (OpenAI / Anthropic /
  mock) with bounded retry + fallback and per-call token/cost accounting, versioned
  system prompts with injection defences (delimited data blocks, role audit,
  instruction-marker neutralisation), unified-diff windowing per changed file, an
  agent registry + `run_agent`, a prompt-injection evaluation harness, and the FR-1.3
  GitHub REST client (`backend/app/github/`) with retryable/permanent error taxonomy
  and scope building. `agents.run_agent` Celery task routes to `review_task_queue` as
  the Phase 6 fan-out seam. 151 tests: 145 pass (6 live-dependency tests self-skip
  without PostgreSQL/Redis).
- Phase 6: Supervisor + LangGraph orchestration (`backend/app/orchestrator/`) — the
  AGENTS.md §2 topology (`SUPERVISOR_PLAN → AGENT_FAN_OUT → RETRY_AGENTS`) with
  bounded agent retries (max 2), a hard per-agent timeout that diagnoses permanent
  `FAILED` with `root_cause` while keeping partial valid findings, an
  `OrchestratorStore` boundary (`Memory` for tests, `Sql` for PostgreSQL) persisting
  status transitions (`status_history`), `supervisor_notes`, per-agent `review_tasks`
  and `agent_metrics`, an `orchestrator.run_review` Celery task on
  `review_task_queue` with live DB + GitHub scope rebuild, and `queue.pop_and_dispatch`
  handing the popped priority review to the orchestrator. Terminal mapping: COMPLETE
  with findings → `DECIDING` (Phase 8 checkpoint), no findings → `COMPLETED`,
  permanent failure → `FAILED`. Migration `0004_orchestrator_notes`. 201 tests:
  193 pass (8 live-dependency tests self-skip without PostgreSQL/Redis).
- Phase 7: Finding consolidation + cross-agent redundancy detection
  (`backend/app/consolidation/`) — a broker/DB-free kernel that normalises raw
  agent findings, embeds them via a pluggable provider (`MockEmbeddingsProvider`
  by default, deterministic and L2-normalised; `OpenAIEmbeddingsProvider` when
  configured) and groups duplicates with a greedy hybrid rule (embedding cosine ≥
  `consolidation_similarity_threshold`, category-aligned, same file, line ranges
  within `consolidation_max_line_gap`). Only multi-agent duplicates (member_count
  ≥ 2) persist `finding_groups` rows with a research-safe method string;
  singletons stay ungrouped. Findings/groups/embeddings persist through the
  `OrchestratorStore` (Memory + Sql; `update_finding_duplicates` resolves the
  bi-directional FK; SQL embeddings use a server-side pgvector literal cast until
  the asyncpg codec in Phase 14). Success lands on the `DECIDING` checkpoint
  (Phase 8); permanent failures still keep partial valid findings. Migration
  `0005_embeddings_hnsw`. 249 tests: 240 pass (9 live-dependency tests self-skip
  without PostgreSQL/Redis).
- Phase 8: ARUM decision layer (`backend/app/adaptive/`) — the `DECIDE (ARUM)`
  step of AGENTS.md §2. Eight normalised [0,1] features per ARUM.md §2 (severity,
  confidence, agent agreement, historical actionability, repository relevance,
  context relevance, redundancy, historical rejection; RAG/feedback-memory
  features read repo-memory snapshots and default to 0.0 until Phase 10 feeds
  them), versioned `ArumWeights` (`v1` heuristic prior, strict repo overrides),
  weighted-linear `utility` + deterministic ranking into `Decision` records, an
  offline learning path (`fit_logistic` pure-stdlib ablation, primary LightGBM
  via the `ml` extra with an honest `MlExtraUnavailableError` when absent), and a
  reproducibility log (deterministic inputs hash; `MemoryDecisionTrace` in tests,
  JSON-lines `FileDecisionTrace` in production). The orchestrator scores every
  consolidated candidate, writes `finding.arum_features/arum_utility/arum_version`
  + `reviews.arum_version`, appends decisions to the trace, and stays on `DECIDING`.
  Decision-layer failures diagnose `FAILED` with `root_cause` while preserving
  partial valid findings. 296 tests: 287 pass (9 live-dependency tests self-skip
  without PostgreSQL/Redis).
- Phase 9: review budget controller + safety gates (`backend/app/adaptive/selection.py`).
  `RiskClass` on the review record resolves a per-review publication cap
  (`review_budget_low/medium/high` = 5/10/20; unknown risk-class → high cap with a
  supervisor note). `select_decisions` fills the cap by ARUM rank after three
  never-silent gates: critical severity → mandatory, high severity + high confidence
  → protected from truncation, low confidence + low severity + high redundancy →
  suppressed; mandatory/protected selections count against the cap so criticals push
  publication above it. Every decision carries `budget_cap` + `safety_gate`
  (`critical_mandatory` / `high_confidence_high_severity` / `low_value_high_redundancy`),
  and `selected` lands in the reproducibility trace (`TRACE_VERSION` → 2). Wiring:
  selected representatives → `publication_status=scheduled`, suppressed/truncated →
  `suppressed`, duplicate-group members suppressed alongside their representative;
  the review records `budget_cap` + `selected_count`/`suppressed_count` + a supervisor
  note ("ARUM selected X of Y candidates — publication itself is a later phase"). The
  review still ends on the `DECIDING` checkpoint — the PUBLISH phase is what publishes.
  New config: `arum_gate_high_confidence`, `arum_gate_low_confidence`,
  `arum_gate_high_redundancy`. 306 tests: 297 pass (9 live-dependency tests self-skip
  without PostgreSQL/Redis).
- Phase 10: repository memory + RAG + temporal decay (`backend/app/adaptive/memory.py`,
  `backend/app/adaptive/rag.py`). Developer feedback (`developer_feedback`) aggregates
  into the cached `repository_memory.snapshot`: decayed per-category
  `accepted_share`/`rejected_share` (ACCEPTED/FIXED = actionability, REJECTED/DISMISSED =
  rejection; exp decay with `arum_temporal_decay_days` tau, 3·tau horizon, events counted
  at exact category + parent prefix). RAG embeds active coding standards and resolves
  context from previously *resolved* same-path findings: each candidate's representative
  is embedded once and retrieved twice (repository relevance, context relevance) with a
  store-agnostic `top_k_similar` core. The orchestrator builds the snapshot lazily,
  syncs standards idempotently (`ON CONFLICT (resource_type, content_hash)`), and feeds
  all four previously-zeroed memory/RAG features into ARUM scoring — a true cold start
  stays honest with 0.0 features and an explanatory supervisor note. New config:
  `arum_rag_top_k`, `arum_rag_min_similarity`. 325 tests: 316 pass (9 live-dependency
  tests self-skip without PostgreSQL/Redis).

Remaining phases (11–19) are tracked in `docs/ROADMAP.md`. Features not yet implemented
are NOT claimed.

---

## Documentation index

| Document | Purpose |
| --- | --- |
| `docs/ARCHITECTURE.md` | System architecture, component breakdown, data flow |
| `docs/ARUM.md` | Adaptive Review Utility Model design |
| `docs/DATABASE.md` | PostgreSQL ER design, indexes, migrations |
| `docs/API.md` | HTTP API specification |
| `docs/QUEUE.md` | Queue architecture and priority policy |
| `docs/AGENTS.md` | Agent design, prompts policy, outputs |
| `docs/ROADMAP.md` | Development phases and status |
| `docs/EXPERIMENTS.md` | Evaluation methodology, baselines, ablation |
| `docs/RESEARCH.md` | Research framing and integrity statement |
| `docs/SECURITY.md` | Security model and threat considerations |
| `docs/SETUP.md` | Local development setup (after implementation) |
| `docs/DEPLOYMENT.md` | Docker deployment (after implementation) |

---

## Research integrity

The complete project incl. dataset (`github-codereview`), baselines, ablation studies,
and reproducible experiment scripts. **No experimental results will be fabricated.**
If an experiment has not been executed, the corresponding report states
"Results pending experimental validation".

See `docs/RESEARCH.md` for the contribution statement and citation policy.