# HTTP API Specification

FastAPI backend. All request/response bodies are validated with Pydantic. Unless noted,
endpoints require authentication; authentication model is documented in
`docs/SECURITY.md`.

## Conventions

- Base path: `/api/v1`
- Content type: `application/json`
- Errors:
  ```json
  { "detail": { "code": "...", "message": "...", "field_errors": [...] } }
  ```
- IDs: `review_id`, `repository_id`, `finding_id` are UUIDs.
- Pagination: `?limit=` + `?offset=` (or cursor for large result sets).
- Rate limits apply to webhook and feedback endpoints.

---

## 1. GitHub webhook

### `POST /api/v1/webhooks/github`

Receives GitHub `pull_request` events (`opened`, `synchronize`,
`ready_for_review`, `reopened`).

- Headers: `X-GitHub-Event`, `X-GitHub-Delivery`, `X-Hub-Signature-256`.
- Signature verified (HMAC-SHA256) against the configured webhook secret.
- Body validated against Pydantic schema; malformed → `422`.
- Idempotent by `X-GitHub-Delivery`; a duplicate delivery returns the same
  `review_id` and does not create a second review run.

Responses:
- `202 Accepted` → `{ "review_id": "...", "delivery_id": "...", "queued": true }`
- `400` invalid payload shape, `401` bad signature, `422` schema rejection,
  `429` rate limit.

The endpoint performs **no AI work**: it persists the review run and returns before
any analysis. The broker enqueue intent is recorded in structured logs until the
Phase 4 queue lands (see `docs/ROADMAP.md`).

---

## 2. Reviews

### `GET /api/v1/reviews/{review_id}`

Review record with status, timestamps, priority, risk class, iteration count,
feedback aggregate.

### `GET /api/v1/reviews/{review_id}/findings`

Paginated findings with `publication_status` filter option
(`all` | `published` | `suppressed` | `resolved`), including ARUM features/logic
(score, weights used, version) for observability.

### `GET /api/v1/reviews/{review_id}/iterations`

Per-iteration summary (changed files, agents invoked, tokens, latency, published).

### `POST /api/v1/reviews/{review_id}/rerun`

Manually re-enqueues the review (re-runs the full state machine). Requires
`operator` role. Returns `202` + new run tracking.

---

## 3. Repositories

### `GET /api/v1/repositories/{repository_id}`

Repository record + settings + priority overrides.

### `GET /api/v1/repositories/{repository_id}/memory`

The repository-specific adaptive memory snapshot: category accept/reject shares,
component activity, strictness, active ARUM weight overrides, decay parameters.

### `PATCH /api/v1/repositories/{repository_id}/settings`

Update review settings (budget caps, safety gates, weights, decay). Audited
(`operator` role).

---

## 4. Feedback

### `POST /api/v1/feedback`

Body:
```json
{
  "review_id": "…",
  "finding_id": "…",
  "outcome": "FIXED",
  "author_login": "dev1",
  "commit_sha": "…",
  "details": { "reaction": "thumbs_up" }
}
```
`outcome` ∈ `ACCEPTED | FIXED | MODIFIED | REJECTED | DISMISSED | IGNORED | DISCUSSION`.

This is the **explicit** feedback path. The system also ingests implicit signals from
GitHub activity, but those never auto-labelled `ACCEPTED`/`FIXED` without an explicit
outcome (see `docs/ARUM.md` § / architecture §4.6).

---

## 5. Metrics & health

### `GET /api/v1/metrics`

Dashboard metrics (auth `viewer`+): queue depths, reviews per state, findings
published/suppressed, redundancy rate, feedback counts, latency, token/cost rolling sums.

### `GET /api/v1/health`

Liveness + dependency status:
```json
{
  "status": "ok",
  "dependencies": { "postgres": "ok", "redis": "ok", "github": "ok", "llm": "degraded" }
}
```

---

## 6. Auth model (design)

Public surface minimal: webhook endpoint authenticates by signature; dashboard/API
endpoints use API keys (or OAuth app) issued to operators; feedback endpoint requires
a valid token scoped to `feedback:write` for the repository. Full policy in
`docs/SECURITY.md`.

---

## 7. Endpoint matrix (planned → implemented)

Implementation will be tracked phase by phase in `docs/ROADMAP.md`. Endpoints listed
here that are not yet implemented must not be claimed as working.