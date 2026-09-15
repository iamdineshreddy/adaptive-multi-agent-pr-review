# Observability (Phase 14)

Covers structured logs, Prometheus metrics exposition, the metrics you can
dashboard on, and (gated) Langfuse LLM trace export.

## Structured logs

Every module logs through `structlog`. `app.monitoring.logsetup` is the single
place that shapes that output (`configure_logging`, wired from
`app.main.create_app`):

- ISO timestamps, `level`, event name, key/value context
- JSON rendering by default (`ADAPTIVE_LOG_LEVEL`, default `info`)
- A **redaction processor**: any event key containing `api_key`, `apikey`,
  `secret`, `token`, `pat`, `password`, `authorization`, `signature`,
  `credential`, or `private_key` has its value replaced with `[REDACTED]` before
  anything is written. Key names stay so a leaking key is visible in log scans.

Example event (JSON): `{"event":"webhook_accepted","level":"info","timestamp":"...","review_id":"...","action":"opened"}`

## Prometheus metrics

`GET /api/v1/monitoring/prometheus` serves the Prometheus text format from a
dedicated registry (`app.monitoring.prometheus`). All values are real:

- **Counters** — incremented at genuine call sites:
  - `adaptive_review_webhook_events_total{event, action, outcome}` — every
    processed `pull_request` webhook delivery (webhook → accepted)
  - `adaptive_review_feedback_recorded_total{outcome_label}` — every recorded
    developer-feedback event (ARUM outcome label)
- **Store-derived gauges** — pulled from `OrchestratorStore.metrics_rollup()` at
  each scrape (never cached or fabricated):
  - `adaptive_review_reviews_by_status{status}`
  - `adaptive_review_findings_by_status{status}`
  - `adaptive_review_feedback_by_outcome{outcome}`
  - `adaptive_review_redundancy_rate`
  - `adaptive_review_agent_tokens_total`, `adaptive_review_agent_cost_usd_total`
  - `adaptive_review_agent_latency_seconds{role=avg|max}`

When `prometheus-client` is not installed (it ships in the `monitoring` extra)
the module is a no-op facade and the endpoint reports an explicit
`unavailable` body rather than fake data. The flag `ADAPTIVE_PROMETHEUS_ENABLED`
toggles exposition independently.

## Grafana

Provisioning lives under `infra/grafana/provisioning/`:

- `datasources/prometheus.yml` — Prometheus datasource (default)
- `dashboards/adaptive-review.json` — an overview dashboard (webhook delivery
  rate, reviews by status, agent token/cost panels) referencing only the real
  metric names above

Prometheus scrape config: `infra/prometheus/prometheus.yml` polls the API
(`/api/v1/monitoring/prometheus`). Since Phase 15, the monitoring endpoint is
behind the dashboard bearer-token gate, so Prometheus must present one:
`authorization: { type: Bearer, credentials: <sk-... > }` where the token's
digest is provisioned in `ADAPTIVE_API_TOKEN_HASHES` (all verbs are read-only,
so a `viewer` token suffices). Full compose wiring lands in Phase 18.

## Langfuse LLM traces

`ADAPTIVE_LANGFUSE_PUBLIC_KEY` / `ADAPTIVE_LANGFUSE_SECRET_KEY` /
`ADAPTIVE_LANGFUSE_HOST` configure the tracing seam in `app/monitoring.langfuse`.
It is **strictly gated**: without a public key (or without the `langfuse` SDK,
which ships in the `monitoring` extra) every entry point is a no-op and the
offline/mock provider surface never touches the SDK or the network.

Two seams:

- **Per-call spans** — `build_llm_provider` wraps the production resilient
  provider in `TracedLLMProvider` (`llm_call` span with model, prompt/
  completion tokens, cost, latency, success/error). The `mock` provider path is
  never wrapped.
- **Per-execution traces** — `run_agent` opens an `agent_execution` trace
  (tags: agent key + review id), so every agent execution is one trace whether
  it runs in-process or through the Celery fan-out task.

Because tracing is gated, unit tests (which use mock providers and no keys) are
byte-for-byte equivalent with and without the SDK installed.