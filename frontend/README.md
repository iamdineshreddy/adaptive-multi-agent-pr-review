# Adaptive PR Review Dashboard (Phase 13)

React + TypeScript + Tailwind CSS (Vite) dashboard for the adaptive multi-agent
PR review backend. Reads only through the Phase 13 dashboard read API
(`backend/app/api/dashboard.py`), proxied under `/api` in development.

## Pages

| Route | Purpose |
| --- | --- |
| `/` | Queue/status summary counts by review status |
| `/reviews` | Newest-first review list with PR context, status badges |
| `/reviews/:reviewId` | Review detail: findings (severity, ARUM utility, publication status) + iteration history |

## Conventions

- One source of truth for the API contract is `src/types/index.ts`; the service
  layer (`src/services/api.ts`) is the only place that talks to the network.
- No fake/stubbed data: every page renders real API responses and shows loading
  / error states instead of mock rows (repo-wide "no fabricated data" rule).
- Tailwind v4 via the `@tailwindcss/vite` plugin (no `tailwind.config.js`; the
  single `@import "tailwindcss"` in `src/index.css` is the entry point).

## Development

Requirements: Node >= 22.

```sh
npm install
npm run dev       # http://localhost:5173 (proxies /api -> http://localhost:8000)
npm run build     # tsc -b && vite build (type-checked production bundle)
npm run preview   # serve the built bundle
```

The backend must be running for data to appear (`make run-uvicorn`). Without a
PostgreSQL instance the reviews endpoints will fail to connect; the dashboard
then shows the API error rather than fabricated rows.