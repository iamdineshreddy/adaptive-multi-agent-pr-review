# System Architecture

This document specifies the architecture of the **Adaptive Multi-Agent AI System for
GitHub Pull Request Review**. It is the primary Phase 1 deliverable and is refined
in Phase 19 to match the implemented system (publication and GitHub comment
posting remain explicitly pending — §4.5).

---

## 1. Design principles

1. **Detection / Decision separation** — raw agent output is never published directly;
   it passes through consolidation, redundancy filtering, and the adaptive decision layer.
2. **Asynchronous everywhere** — the GitHub webhook handler never blocks on AI inference.
   Everything after ingestion is queue-driven via Celery + Redis.
3. **A PR is an immutable, inspectable unit** — every PR review gets a derived review ID
   and a lifecycle state persisted in PostgreSQL.
4. **Measurable selection** — finding selection uses ARUM scoring (weights, calibrated
   and trained), not an LLM ranking prompt.
5. **Repository isolation** — memory, standards, and learned preferences are scoped per
   repository. Nothing learned in repo A leaks to repo B.
6. **Failure is a first-class state** — every step has explicit retry, poison, and
   recovery paths.
7. **No hidden chain-of-thought** — agents emit Pydantic-validated structured findings
   with concise, evidence-only reasoning summaries for explainability.

---

## 2. Component inventory

| ID | Component | Role |
| --- | --- | --- |
| A | GitHub Webhook Service | Verifies signatures, validates payloads, enqueues PRs |
| B | PR Priority Queue | Computes priority score, orders PR ingestion |
| C | Review Orchestrator | Drives the review state machine (LangGraph) |
| D | Agent Task Queue | Fan-out of per-agent work items |
| E | Specialized Review Agents | Security / Quality / Performance / Architecture / Standards |
| F | Finding Consolidation Layer | Normalises findings from JSON into the finding model |
| G | Redundancy Detection Layer | Semantic grouping of duplicate findings |
| H | Adaptive Review Utility Model (ARUM) | Scores and ranks findings for publication |
| I | Review Budget Controller | Caps published findings per risk class, honours safety gates |
| J | GitHub Review Publisher | Writes review comments adhering to diff line anchors + rate limits — **pending (later phase); findings today are selected and scheduled, never posted** |
| K | Developer Feedback Collector | Captures developer outcomes from PR activity and API |
| L | Repository-Specific Review Memory | Per-repo historical aggregates, standards, preferences |
| M | RAG Retrieval System | pgvector retrieval of similar findings/decisions/standards |
| N | Adaptive Learning Module | Updates ARUM weights and memory from feedback (never auto-promoted) |
| O | Iterative Review Engine | Diff-aware re-review across new commits |
| P | PostgreSQL | Primary store (SQLAlchemy + asyncpg + Alembic) |
| Q | Redis | Broker, result backend, priority zset, dead-letter log |
| R | Celery Workers | Execute queues (ingestion, agents, decision, publisher, feedback) |
| S | FastAPI APIs | HTTP surface for webhooks, dashboard reads, feedback, metrics, health |
| T | Observability System | Langfuse (LLM traces), Prometheus/Grafana (metrics + logs; worker-side exporter) |
| U | Dashboard (frontend) | React + TypeScript + Tailwind SPA over the Phase 13 read APIs |
| V | Experiment Harness | Label pre-processing + baseline/ablation runner over the same `app.adaptive` code |
| W | Deployment Stack | Docker Compose: postgres/redis/api/worker/scheduler/frontend/prometheus/grafana |

---

## 3. Component diagram

```text
                          ┌──────────────────────────────────────────────┐
                          │                 GitHub (external)           │
                          │                                             │
                          │  webhook events · PR data · diffs · comments│
                          └──────────────▲──────────────────▲───────────┘
                                         │                  │
                 POST /webhooks/github   │                  │  GraphQL/REST client (PAT/App)
                                         │                  │
┌────────────────────────┐   verify   ┌──┴───────────────┐   │   ┌──────────────────────────┐
│       FastAPI API      │◄──────────►│ Webhook Service  │   │   │   GitHub Review Publisher  │
│  /reviews /findings    │            └───────┬──────────┘   │   └────────────▲─────────────┘
│  /feedback /metrics    │                    │ enqueue      │                │
└───────────┬────────────┘                    ▼              │                │
            │                        ┌──────────────────┐    │                │
            │ DB queries             │ PR Priority Queue│    │                │
            │                        └───────┬──────────┘    │                │
            ▼                                ▼                │                │
┌───────────────────────────┐   ┌────────────────────────┐   │                │
│      PostgreSQL            │◄──┤     Redis               │   │                │
│   SQLAlchemy + asyncpg     │   │  broker/result backend  │   │                │
│   pgvector · Alembic       │   └───────────┬────────────┘   │                │
└───────────▲───────────────┘               │                 │                │
            │                               ▼                 │                │
            │                 ┌───────────────────────────────────────┐        │
            │                 │               Celery Workers           │        │
            │                 │───────────────────────────────────────│        │
            │                 │ pr.routes_review · agents.run         │        │
            │                 │ decision.run · publisher.run           │        │
            │                 │ feedback.ingest · iteration.run        │        │
            │                 └───────┬──────────┬──────────┬──────────┘        │
            │                         │          │          │                    │
            ▼                         ▼          ▼          ▼                    ▼
┌─────────────────────────────┐  ┌────────────────────────┐           ┌──────────────┐
│  Repository Memory (per-repo)│  │    Review Orchestrator  │   ....   │ Observability │
│  historical aggregates,     │  │    (LangGraph state     │   ...    │ Langfuse      │
│  standards, temporal decay  │  │     machine)            │          │ Prometheus    │
└───────────▲─────────────────┘  └────────────────────────┘          │ Grafana       │
            │                                                         └──────────────┘
            └────── RAG / ARUM / Adaptive Learning read+write ────────┘
```

All components communicate through well-defined interfaces; the only synchronous
request/response path is **GitHub → API → queue**, plus the published review comments
to GitHub. Everything else is message-driven.

---

## 4. Data flow — lifecycle of a Pull Request

### 4.1 Ingestion (no AI work)

1. GitHub sends `pull_request` webhook event (`opened`, `synchronize`, `ready_for_review`).
2. Webhook Service verifies `X-Hub-Signature-256` (HMAC-SHA256) against the app secret.
3. Payload is validated with Pydantic; malformed events are rejected with `422`.
4. Repository is fetched or created; GitHub installation credentials are resolved.
5. PR row is upserted; a `review` row is created with a unique `review_id`.
6. Priority score is computed (see `docs/QUEUE.md`).
7. The review is enqueued on `pr_ingestion_queue` and returned `202 Accepted`.
   The webhook handler returns immediately — AI analysis never blocks this request.

### 4.2 Priority ordering

A Celery consumer on `pr_ingestion_queue` pushes the review onto the priority
mechanism (Redis sorted set keyed by `priority_score`, tie-broken by FIFO ordinal).
A scheduler pops by priority and dispatches the review to the orchestrator.

### 4.3 Orchestration state machine

States (persisted on the `review` row):

```text
RECEIVED → QUEUED → PROCESSING
     → AGENTS_RUNNING → CONSOLIDATING → DECIDING → PUBLISHED*
     → WAITING_FOR_FEEDBACK → ITERATING → DECIDING → PUBLISHED* (final)
RECEIVED/QUEUED/etc → FAILED (with retry) → CANCELLED

* PUBLISHED is the later-phase target of the PUBLISH step (component J). The
  implemented graph terminates on the DECIDING checkpoint (selection recorded,
  publication to GitHub pending) or ITERATING for settled rounds; nothing marks
  a review PUBLISHED today.
```

Transitions are enforced by the orchestrator; every state change is time-stamped.

### 4.4 Agent execution

1. Supervisor Agent builds a PR summary (files, diffs, commit context, touched
   components) and emits the task plan.
2. Task plan items are enqueued to `review_task_queue` and fanned out to
   `security_queue`, `quality_queue`, `performance_queue`, `architecture_queue`,
   `standards_queue` (parallel, distinct Celery workers/queues).
3. Each agent retrieves the relevant diff slices + RAG context (repo standards,
   previous findings) and returns **structured findings** (Pydantic-validated JSON).
4. Agent results (including failures) are stored on `agent_metrics` and the findings
   table gets the raw records.

### 4.5 Consolidation → redundancy → decision → budget → publish

1. **Consolidation (F)**: results are normalised; chat-only or nonsensical output is
   rejected; fields are coerced into the finding model.
2. **Redundancy (G)**: embeddings are calculated; findings are grouped by semantic
   similarity + category + file/line proximity; a `duplicate_group` id is assigned.
3. **Adaptive Decision (H)**: each group is scored by ARUM (see `docs/ARUM.md`);
   findings are ranked.
4. **Budget (I)**: up to N findings are selected based on PR risk class and ARUM rank,
   honouring safety gates (critical/high-confidence findings protected).
5. **Checkpoint, not publication**: the review lands on `DECIDING` with selected
   representatives marked `publication_status=scheduled` and suppressed/truncated
   findings marked `suppressed`. The **PUBLISH step (J)** that posts GitHub review
   comments is a later phase and is never simulated — nothing sets `PUBLISHED` for a
   review until that step exists (see `docs/ROADMAP.md`).

### 4.6 Feedback capture

Developer actions are captured two ways:

- **Explicit API**: `POST /feedback` with `developer_outcome` ∈
  {ACCEPTED, FIXED, MODIFIED, REJECTED, DISMISSED, IGNORED, DISCUSSION}.
- **Implicit signals**: PR commits resolving a finding, replies/reactions to a comment,
  GitHub review dismissal.

Feedback is never auto-labelled from "code changed". What "code changed" tells us is
"the region moved / the finding should be re-evaluated" — an explicit outcome label is
required before ARUM memory counts it as acceptance.

### 4.7 Iteration (`synchronize` events)

On new commits, the **Iterative Review Engine (O)**:

1. Computes changed files/lines (PR diff vs. last reviewed commit).
2. Maps previous findings to changed regions → marks `RESOLVED` or `STALE`.
3. Matches unresolved findings to current code; scores persistence via ARUM.
4. Runs **targeted** agents only on changed/high-risk regions.
5. Publishes only new/updated/high-value findings (no full re-review).

See the iterative section in `docs/ARUM.md` and `docs/ROADMAP.md` (Phase 12).

---

## 5. Concurrency and scaling

- Multiple Celery worker nodes; one worker type per queue (or shared with resource
  quotas). Queues are isolated so agent-fan-out can scale independently.
- Agent tasks are CPU/LLM bound: tuned via `--concurrency`, `--prefetch-multiplier`,
  rate-limit per provider (tokens/min), and per-repo concurrency locks (Redis).
- A global per-repository lock prevents two reviews of the same PR from running
  concurrently (iteration safety); iterations supersede prior in-flight reviews.
- PostgreSQL is the system of record; Redis is non-authoritative (ephemeral).

---

## 6. Failure and retry model

| Failure | Detection | Handling |
| --- | --- | --- |
| LLM timeout / 5xx | Agent task exception | Retry with exponential backoff + jitter; alternate provider fallback; visible agent failure metric |
| Invalid LLM JSON | Pydantic validation error | One repair attempt re-requesting structured JSON; else record failed agent output |
| Webhook replay / dup | Idempotency key (delivery id) | Upsert is idempotent; duplicate delivery enqueued once |
| Queue consumer crash | Celery acks-late | Task requeued up to `max_retries`; on exhaustion → `FAILED` with `failure_reason` |
| GitHub API transient 5xx | Error type detection | Retry bounded; abort review on permanent 4xx auth failure (alerted) |
| Provider outage | Health checks | Circuit-breaker; queue pauses; workers back off |
| Poison tasks | Retry threshold | Moved to dead-letter log + alert; review marked `FAILED` |

Every retry is observable: `retry_count`, `last_error`, timestamps, and Prometheus
counters per queue.

---

## 7. Security architecture

- Webhook signature verification (HMAC-SHA256, constant-time compare).
- No tokens in code or logs; GitHub authentication via installation App or PAT from
  secrets manager / env (documented in `docs/SECURITY.md`).
- Repository code, diffs, PR bodies are **untrusted input**: prompt-injection defences
  (see `docs/AGENTS.md`), maximum context trimming, and no in-context "new instructions".
- Per-repo data isolation at database query level (repository_id scoping).
- Input validation on every public endpoint; rate limiting on webhook and feedback
  endpoints; structured logging without secrets.

---

## 8. Observability

| System | What it captures |
| --- | --- |
| Langfuse | LLM traces per agent: prompt, structured output, tokens, cost, latency (strictly gated on keys) |
| Prometheus | Webhook/feedback counters, reviews/findings/feedback gauges, redundancy rate, agent token/cost/latency; scraped from **both** the API and a worker-side exporter (`app.monitoring.worker_exporter`, `worker:8001`) so queue health stays observable with the API down |
| Grafana | Provisioned dashboard over the real metric names (adaptive-review) |
| Application logs | Structured JSON logs with `review_id`, `repository_id`, no secrets (redaction processor) |

Full wiring (scrape config, dashboards, compose) is documented in
`docs/OBSERVABILITY.md` and `docs/DEPLOYMENT.md`.

---

## 9. Documented decision log (why things are built this way)

1. **Why Celery over a custom worker pool?** Proven broker/result semantics, retries,
   priorities, rate limiting, and multi-queue fan-out with minimal bespoke code.
2. **Why LangGraph?** It gives an explicit, inspectable, resumable state machine for the
   review lifecycle (states/transitions/retry paths) which matches the iterative review
   requirement. It is used only for orchestration state, not for "calling LLMs
   arbitrarily".
3. **Why pgvector over a separate vector DB?** One less service, transactions with
   findings, and sufficient scale for a research prototype.
4. **Why ARUM is score-based, not LLM-ranked?** LLM ranking is non-deterministic and
   non-measurable. ARUM is a weighted model whose coefficients are trained/evaluated,
   making the selection decision reproducible and an object of study.
5. **Why safety gates?** Critical security findings must never be silenced by a budget
   heuristic; the gate is a hard policy, not a weight.
6. **Why `code changed ≠ accepted`?** A change can be a workaround, a revert, or an
   unrelated refactor. Auto-labelling acceptance corrupts the feedback signal used to
   train ARUM, so outcome labelling is explicit.
7. **Why the experiment harness reuses the product code?** Baselines/ablations are
   `dataclasses`-switch flags over the *same* features/scoring/selection modules the
   product runs — a hand-wired alternate pipeline would measure a different system.
8. **Why a worker-side Prometheus exporter (`worker:8001`)?** Scraping the queue's own
   process keeps health signal when the API container is down; it serves the identical
   auth gate + rollup→gauge pipeline so the two endpoints cannot diverge.
9. **Why one backend image for api/worker/scheduler?** Identical dependency sets,
   minimal surface; compose overrides only the `command`. The scheduler is Celery
   beat firing `queue.pop_and_stage` on a configured interval; migrations run once in
   the api entrypoint (`ADAPTIVE_SKIP_MIGRATIONS` on the other runtimes).
10. **Why dual compose networks?** PostgreSQL/Redis stay off the frontend and metrics
    networks; only api/worker straddle the observability network for scraping.

See `docs/EXPERIMENTS.md` for how each architectural choice above is evaluated.