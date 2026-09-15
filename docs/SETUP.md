# Setup

Covers local development and the Phase 18 full-stack compose boot.

## Prerequisites

- Python 3.11+ (backend), Node 20+ (frontend build), Docker + Docker Compose
  v2 (optional — full-stack boot only)
- Git

## Backend (local)

```bash
cd backend
python -m venv .venv
.\.venv\Scripts\activate          # pwsh; or source bin/activate on POSIX
pip install -e ".[dev,all]"      # dev tooling + every runtime extra
```

Optional runtime extras separately: `.[db]` `.[queue]` `.[llm]`
`.[monitoring]` — the offline default surface needs only the base deps
(`fastapi`, `uvicorn`, `pydantic`, `structlog`, `httpx`); DB, queue, LLM and
prometheus/langfuse seams degrade to explicit no-op/`unavailable` without their
extras.

Environment: copy `../.env.example` to `.env` (the app reads `.env` from the
`backend` working directory; `ADAPTIVE_` prefixed values). Nothing runs without
secrets in development — mock providers are the default.

```bash
# Postgres + Redis (compose for local infra only)
docker compose up -d postgres redis

# schema
alembic upgrade head

# api
uvicorn app.main:app --reload
```

## Frontend (local)

```bash
cd frontend
npm install
npm run dev          # http://localhost:5173, proxies /api → :8000
```

`npm run build` + `npm run preview` for the production bundle.

## Full-stack compose (Phase 18)

```bash
cp .env.example .env   # fill tokens/secrets (docs/DEPLOYMENT.md §Env & secrets)
docker compose up --build -d
```

Serves dashboard + API + worker + scheduler + prometheus + grafana
(docs/DEPLOYMENT.md). Boot requires a Docker host.

## Tests

```bash
cd backend
pytest -q                  # unit + gated live-service lanes (self-skip w/o infra)
ruff check . && ruff format --check . && mypy app
```

Docs/TESTING.md is the canonical gate description.