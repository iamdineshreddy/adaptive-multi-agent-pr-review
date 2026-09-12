# Deployment

**Status: pending implementation.** Updated in Phase 18.

Planned contents:

- `infra/docker/` Dockerfiles: `api`, `worker`, `scheduler (beat)`, `frontend`,
  `prometheus`, `grafana`
- `docker-compose` topology (postgres, redis, api, worker, scheduler, frontend,
  observability) with healthchecks, volumes, networks
- Environment/secrets layout (no secrets in compose)
- Scaling notes: worker pools, queue routing, LLM rate-limit tuning
- Prometheus scrape config + Grafana provisioning (dashboards documented)
- Backup/restore for PostgreSQL
- Observability runbook (queue backpressure, provider outage, dead letters)