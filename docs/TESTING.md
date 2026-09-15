# Testing

Phase 16 organizes and hardens the repository suite. Coverage is measured per
phase; the current baseline is **84% line coverage over `backend/app`
(4413 statements, 702 uncovered)**, with the bulk of uncovered code in the
PostgreSQL/Redis-backed Sql+Redis implementations that need a live database and
are exercised by the gated integration lane below (never by fakes).

## Lanes

| Lane | Where | Runs by default? |
| --- | --- | --- |
| Unit / core logic | `tests/test_*.py` (no integration/skip marker) | yes |
| API contract | `tests/test_api_*.py`, `tests/test_webhooks.py`, `tests/test_health.py` | yes |
| Queue tasks (celery wrappers) | `tests/test_queue_tasks.py` | yes |
| Live-service integration (PostgreSQL) | `tests/test_db_integration.py`, `tests/test_orchestrator_db.py`, `tests/test_consolidation_db.py` | self-skip without a server |
| Live-service pipeline (PostgreSQL + Redis) | `tests/test_integration_pipeline.py` | self-skip without services |

## Running the gate

```bash
python -m pytest -q            # 446 passed / 11 skipped (10 live-DB, 1 pipeline)
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
- Coverage numbers are regenerated with:

  ```bash
  python -m coverage run --source=app -m pytest -q
  python -m coverage report -m
  ```