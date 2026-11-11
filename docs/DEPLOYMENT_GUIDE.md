# Deployment Guide

See `docs/DEPLOYMENT.md` for the full guide. Quick start:

```
$env:ADAPTIVE_API_TOKEN = "sk-dev-demo-2026"
docker compose up -d
```

Services: api (8000), worker exporter (8001), frontend (80), prometheus (9090),
grafana (3000). PostgreSQL and Redis are internal to the compose network.

For local (non-Docker) development see `docs/SETUP.md`.
