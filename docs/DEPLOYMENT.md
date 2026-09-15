# Deployment

**Status: implemented in Phase 18** (see verification note below).

## Quick start

Prerequisites: Docker + Docker Compose v2. Then:

```bash
cp .env.example .env      # fill in tokens/secrets (see §Env & secrets)
docker compose up --build -d
docker compose ps          # all services healthy/up
```

- Dashboard: http://localhost (frontend nginx; `/api/` proxied to the api)
- API docs (dev): http://localhost:8000/api/docs
- Prometheus: http://localhost:9090
- Grafana: http://localhost:3000 (login in `.env`)

`docker-compose.yml` runs: `postgres`, `redis`, `api` (FastAPI+uvicorn :8000),
`worker` (Celery + :8001 metrics exporter), `scheduler` (Celery beat),
`frontend` (nginx static + `/api` reverse proxy :80), `prometheus`, `grafana`.

## Topology

```text
             ┌─────────────┐ :80        ┌─────────┐
  Browser ──►│   frontend  │──/api/────►│   api   │ :8000
             │   (nginx)   │            └────┬────┘
             └─────────────┘       ┌─────────┼─────────┐
                                   ▼         ▼         ▼
                          ┌────────────┐  ┌────────┐  ┌──────────┐
                          │ postgres   │  │  redis │  │  worker  │ :8001
                          │ :5432      │  │ :6379  │  └────┬─────┘
                          └────────────┘  └────────┘       │  (queues)
                                                   ┌───────▼────────┐
                                                   │   scheduler    │
                                                   │ (beat: pop+)   │
                                                   └────────────────┘
   observability net:  api & worker ──scrape──► prometheus ──► grafana
```

Two compose networks: `adaptive` (app + data stores + frontend) and
`observability` (api/worker/prometheus/grafana). PostgreSQL/Redis are never
reachable from the frontend or metrics network.

## Images

- `infra/docker/backend/Dockerfile` — one Python 3.11 image serving
  **api/worker/scheduler** via compose `command`; installs `.[all]`
  (db+queue+llm+orchestration+monitoring+ml). Entrypoint runs
  `alembic upgrade head` then execs the command (skippable with
  `ADAPTIVE_SKIP_MIGRATIONS=true`; the api service owns migration).
- `infra/docker/frontend/Dockerfile` — two-stage: `node:20` builds the Vite
  bundle, `nginx:alpine` serves it and proxies `/api/` → `api:8000` with SPA
  fallback (`infra/docker/frontend/nginx.conf`).
- Prometheus/Grafana use stock images with provisioning from
  `infra/prometheus/` and `infra/grafana/`.

## Services

| Container | Runs | Healthcheck | Notes |
| --- | --- | --- | --- |
| `postgres` | 16-alpine | `pg_isready` | volume `pgdata` |
| `redis` | 7-alpine AOF | `redis-cli ping` | volume `redisdata`; broker+backend+priority zset + dead-letter log |
| `api` | uvicorn :8000 | `/health` | applies migrations; bearer-gated metrics; `/api/docs` in dev |
| `worker` | celery + `worker_exporter` :8001 | exporter `/health` | consumes all QUEUE.md §1 queues on `default,review_task,pr_ingestion,security,...,feedback` |
| `scheduler` | celery beat | restart-based | fires `queue.pop_and_stage` every `ADAPTIVE_SCHEDULER_INTERVAL_SECONDS` (default 30 s) |
| `frontend` | nginx :80 | wget `/` | SPA + `/api/` proxy |
| `prometheus` | prom/prometheus :9090 | — | scrapes `api:8000` + `worker:8001` with bearer token |
| `grafana` | grafana/grafana :3000 | — | provisioning from repo; dashboard `adaptive-review` |

## Env & secrets

Everything is read from the repository root `.env` (gitignored;
`.env.example` is the template). The app itself only ever receives `ADAPTIVE_*`
values:

- **API tokens**: dashboards + Prometheus authenticate with a bearer token. The
  app stores only SHA-256 digests (`ADAPTIVE_API_TOKEN_HASHES` — a JSON array)
  plus `ADAPTIVE_API_TOKEN_ROLES` (JSON digest→role). Prometheus holds the raw
  token as `ADAPTIVE_API_TOKEN`. Mint one with:
  ```
  python -c "import hashlib;print(hashlib.sha256(b'<token>').hexdigest())"
  ```
- **Webhook/security**: `ADAPTIVE_SECRET_KEY`, `ADAPTIVE_GITHUB_WEBHOOK_SECRET`.
- **GitHub App**: `ADAPTIVE_GITHUB_APP_ID` + private key (or PAT fallback);
  empty at boot is fine, ingestion needs them.
- **LLM**: `ADAPTIVE_LLM_PROVIDER=mock` boots fully offline; `openai`/`anthropic`
  need the corresponding keys.
- **Langfuse**: optional; unset keys keep tracing a no-op (OBSERVABILITY.md).

No secret is hardcoded in `.docker-compose.yml` or any image.

## Scale notes

- **Worker pools**: one worker container consumes all queues by default. For
  scale, run additional workers bound to a subset (e.g. `-Q security_queue`)
  and keep one `default` consumer for the beat-driven dispatcher.
- **Queue routing**: `TASK_ROUTES` in `app.queue.celery_app` pins tasks to
  named queues; `queue.pop_and_stage` is routed to `default`.
- **LLM rate limits**: tune `orchestrator_agent_timeout_seconds`,
  `llm_max_retries`, and worker concurrency (`-c`) for your provider budget.
- **Dead letters**: exhausted ingestion tasks land in Redis under
  `adaptive_review:dead_letter`; backpressure is bounded by
  `celery_max_retries` + `celery_retry_backoff_seconds`.

## Backup / restore (PostgreSQL)

```bash
docker compose exec postgres pg_dump -U adaptive adaptive_review > backup.sql
docker compose exec -T postgres psql -U adaptive adaptive_review < backup.sql
```

The data volume is `pgdata`; `redisdata` holds the AOF (broker resumes on
restart; in-flight work is governed by `acks_late` redelivery).

## Observability runbook

- **Queue backpressure**: Grafana reviews/status gauges or `redis-cli zcard
  adaptive_review:priority` — stalls show a growing priority zset and no
  published reviews.
- **Provider outage (LLM)**: bounded agent retries then diagnosed `FAILED`
  terminal state with a root cause; reviews are never silently completed.
- **Worker crash**: `acks_late` + `task_track_started` redeliver in-flight
  tasks; scheduler keeps popping only after a pop succeeded (dispatch failures
  re-raise, keeping the review acknowledged).
- **Dead letters**: inspect `adaptive_review:dead_letter` —
  `redis-cli llen adaptive_review:dead_letter`.

## Verification note (honesty)

The compose file, images, entrypoints and this runbook are static assets; the
boot-time unit/integration suite (docs/TESTING.md) never depends on a running
stack. A live `docker compose up --build` smoke (all 8 services healthy, one
webhook ingestion end-to-end, Prometheus scraping both targets, Grafana
dashboard rendering) must be executed on a Docker host as Phase 18 part 2 —
until it runs, every claim above about container behaviour is pending that
execution, not asserted.