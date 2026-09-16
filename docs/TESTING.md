# Testing

Phase 16 organizes and hardens the repository suite. Coverage is measured per
phase; the current baseline is **85% line coverage over `backend/app`
(5378 statements, 820 uncovered)**, with the bulk of uncovered code in the
PostgreSQL/Redis-backed Sql+Redis implementations and the LLM/orchestration
paths that need a live database or provider and are exercised by the gated
integration lane below (never by fakes).

## Lanes

| Lane | Where | Runs by default? |
| --- | --- | --- |
| Unit / core logic | `tests/test_*.py` (no integration/skip marker) | yes |
| API contract | `tests/test_api_*.py`, `tests/test_webhooks.py`, `tests/test_health.py` | yes |
| Queue tasks (celery wrappers) | `tests/test_queue_tasks.py` | yes |
| Experiment harness (selection layer) | `tests/test_experiments_*.py` | yes |
| Worker metrics exporter | `tests/test_monitoring_worker_exporter.py` | yes |
| Live-service integration (PostgreSQL) | `tests/test_db_integration.py`, `tests/test_orchestrator_db.py`, `tests/test_consolidation_db.py` | self-skip without a server |
| Live-service pipeline (PostgreSQL + Redis) | `tests/test_integration_pipeline.py`, `tests/test_integration_crash_recovery.py` | self-skip without services |
| Compose boot smoke (Phase 18 stack) | `tests/test_integration_compose_smoke.py` | self-skip without a Docker CLI/daemon |

## Running the gate

```bash
python -m pytest -q            # 534 passed / 20 skipped (10 live-DB, 1 pipeline, 3 crash-recovery, 6 compose smoke)
python -m ruff check .
python -m ruff format --check .
python -m mypy app
```

## Live-service integration (Docker-based verification)

The DB/queue integration lanes require real services and **self-skip** (with a
clear reason) when none are reachable:

- PostgreSQL at `ADAPTIVE_DATABASE_URL` — schema round-trips, webhook ingest
  idempotency + priority, queue failure helpers, dead letters, orchestrator
  store transitions.
- PostgreSQL **and** Redis at `ADAPTIVE_REDIS_URL` — the end-to-end pipeline:
  webhook → `SqlReviewIngester` → priority zset (`stage_review_to_priority` with
  the DB score loader) → `pop_and_dispatch` hand-off
  (`tests/test_integration_pipeline.py`).
- Worker-crash / restarted-in-flight recovery (same services,
  `tests/test_integration_crash_recovery.py`, Phase 16 part 2): a worker crash
  between the zset pop and the fan-out dispatch leaves the review off the zset
  while the DB row stays the system of record; restart recovery re-stages
  through the ingest path (`stage_review_to_priority` + DB score loader) and the
  review reaches fan-out exactly once. Three scenarios: crash-then-recover from
  the DB, no-duplicate hand-off after restart, and priority order preserved
  across recovery. Authored and gate-validated (self-skips with a clear reason
  without services); **execution is pending the Phase 18 stack** — no executed
  result is claimed for it until then.
- Compose boot smoke (Phase 18 part 2, `tests/test_integration_compose_smoke.py`):
  boots the full `docker-compose.yml` topology and asserts the published surface
  — API/worker-exporter `/health`, frontend SPA, both Prometheus targets `up`
  under the bearer gate, Grafana UI, 401 on the metrics endpoints without a
  token, and the webhook route wired (401 unsigned; a signed accepted 202 when
  the host exports `ADAPTIVE_GITHUB_WEBHOOK_SECRET`). Self-skips without a
  Docker CLI/daemon; no boot result is claimed until it runs on a Docker host.

This is the "broker delivery" seam promised in Phase 4: the providers used are
the production ones — no mocks. Docker Compose wiring for these services is
Phase 18.

## Failure scenarios

Execution-layer failure modes are unit-tested without a broker:

- Agent fan-out: transient failure → bounded retry, exhausted retries →
  diagnosed `FAILED` with partial findings preserved, hard per-agent timeout,
  permanent failures skipped from retry (`tests/test_orchestrator_graph.py`).
- LLM providers: retryable vs. permanent classification, fallback
  (`tests/test_agents_llm.py`).
- Queue: `pop_and_dispatch` re-raises on dispatch failure; dead-letter +
  `MaxRetriesExceededError` after `ADAPTIVE_CELERY_MAX_RETRIES`; retry countdown
  jitter (`tests/test_queue_tasks.py`, `tests/test_queue.py`).
- Decision/redundancy layers: provider failures diagnose the review
  (`tests/test_consolidation_service.py`, `tests/test_adaptive_service.py`).
- Security gates: 401/403/413 matrix (`tests/test_security_auth.py`,
  `tests/test_api_feedback.py`).

## Notes

- `tests/test_security_auth.py` and the API suites override `get_principal`
  (and rate limiter / store) via `app.dependency_overrides`; the bearer-token
  gate is exercised directly against the router in the same files.
- The experiment harness suite (`tests/test_experiments_*.py`) pins the label
  matrix (Auth.md rules), the ARUM §5 metric formulas, the B1..B5/ablation mode
  switches and the runner's determinism and gate behaviour over hand-built
  corpora. The bundled sample run (`experiments/run/run_all.py`) is a smoke
  verification, not research data (see `docs/EXPERIMENTS.md`).
- The worker exporter suite (`tests/test_monitoring_worker_exporter.py`) pins
  the separate `worker:8001` scrape endpoint (bearer gate, health probe, shared
  rollup→gauge pipeline) that Phase 18 compose wires; deployment assets
  themselves are static and their boot smoke is pending a Docker host
  (`docs/DEPLOYMENT.md`).
- Coverage numbers are regenerated with:

  ```bash
  python -m coverage run --source=app -m pytest -q
  python -m coverage report -m
  ```