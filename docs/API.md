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

Implemented (Phase 15 part 2b, FR-7.3). Requires `operator` role. Creates a fresh
`Review` (`mode=MANUAL_RERUN`, `status=QUEUED`) for the same pull request with the
priority recomputed from stored PR state + repository urgency (shared scoring
family with the webhook path, `docs/QUEUE.md` §2), then hands it to the queue
dispatcher (`celery` provider or log-only fallback).

Responses:
- `202 Accepted` → `{ "review_id": "<new run>", "mode": "MANUAL_RERUN", "status": "QUEUED", "priority_score": 1.3, "risk_class": "MEDIUM", "pr_number": 42, "dispatch_note": "enqueued queue.enqueue_review" }`
- `401` → `missing_bearer_token` / `invalid_token`
- `403` → `insufficient_permission` (below `operator`) / `repository_out_of_scope` (scoped token)
- `404` → `review_not_found`

Every rerun appends an admin-audit row (`review.rerun`); see `GET /api/v1/audit-log`.

---

## 3. Repositories

### `GET /api/v1/repositories/{repository_id}`

Repository record + settings + priority overrides.

### `GET /api/v1/repositories/{repository_id}/memory`

The repository-specific adaptive memory snapshot: category accept/reject shares,
component activity, strictness, active ARUM weight overrides, decay parameters.

### `PATCH /api/v1/repositories/{repository_id}/settings`

Implemented (Phase 15 part 2b, FR-7.3). Requires `operator` role. Partially
updates `Repository.review_settings` (at least one field required). Field groups:

- `urgency` — float 0..1 (priority weight for this repository)
- `review_budget` — `{ low, medium, high }` ints ≥ 1 (per-risk-class ARUM budget caps)
- `safety_gates` — `{ high_confidence, low_confidence, high_redundancy }` floats 0..1
- `arum_weights` — `{ "w1_severity": 0.5, … }` overrides validated through the real
  ARUM override grammar (unknown weight keys → `422 invalid_settings`)
- `decay` — `{ temporal_decay_days, decay_lambda }` (memory decay for this repository)

Nested groups merge key-wise (a partial `review_budget`/`arum_weights` never
clobbers siblings); the merged result replaces `review_settings` on the repository
and is actively consumed by the orchestrator decision layer (`_decide_findings`
budget/gates/weights, `_refresh_repository_memory` decay — falling back to the
global config when unset).

Responses:
- `200` → `{ "repository_id": "…", "review_settings": { "urgency": 0.9, … } }`
- `401` → `missing_bearer_token` / `invalid_token`
- `403` → `insufficient_permission` (below `operator`) / `repository_out_of_scope`
- `404` → `repository_not_found`
- `422` → `invalid_settings` (unknown weight key) or schema rejection (empty body, out-of-range fields)

Every update appends an admin-audit row (`repository.settings.update`) carrying the
acting principal + before/after settings; see `GET /api/v1/audit-log`.

---

### `GET /api/v1/audit-log`

Implemented (Phase 15 part 2b, FR-7.3). Requires `admin` role. Append-only admin
audit trail, newest first.

Query parameters: `action` (exact action filter), `limit` (1–200, default 50),
`offset` (default 0). Scoped admin tokens only ever see rows for repositories
inside their scope.

Responses:
- `200` → `[ { "id": 7, "action": "repository.settings.update", "target_kind": "repository", "target_id": "…", "repository_id": "…", "principal_token_hash": "<sha256>", "principal_role": "operator", "principal_label": "ops", "before": {…}, "after": {…}, "created_at": "…" }, … ]`
- `401` → `missing_bearer_token` / `invalid_token`
- `403` → `insufficient_permission` (requires `admin`)

Actions recorded: `review.rerun`, `repository.settings.update` (future admin write
actions append under the same contract — `docs/SECURITY.md` §12).

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

Implemented (Phase 11): the endpoint resolves the repository **server-side from the
finding** (not trusted from the body), persists the row idempotently (deterministic
`feedback_id`), and rebuilds the repository memory snapshot on write (`repository_id`,
`memory_version` in the response reflect the refreshed state). Per-IP rate limited.
Since Phase 15 part 2 the endpoint additionally requires a bearer token (same
`sk-…` dashboard token; repo-scoped tokens may only post feedback for repositories
inside their scope) and sits behind the same router-level gate as the dashboard.

Responses:
- `201 Created` → `{ "feedback_id": "…", "recorded": true, "memory_version": 3 }`
- `401` → `missing_bearer_token` / `invalid_token` (Phase 15)
- `403` → `repository_out_of_scope` (scoped token outside its repositories)
- `404` → `finding_not_found` (finding not found for the given review)
- `413` → `body_too_large` (above `ADAPTIVE_MAX_REQUEST_BODY_BYTES`, Phase 15)
- `422` schema rejection, `429` rate limited.

A supplied `commit_sha` is stored as code-change evidence in `details`; it never
changes the outcome the developer gave (see below).

This is the **explicit** feedback path. The system also ingests implicit signals from
GitHub activity, but those never auto-labelled `ACCEPTED`/`FIXED` without an explicit
outcome (see `docs/ARUM.md` §5 / `docs/ARCHITECTURE.md` §4.6). Weight learning from
labelled feedback is an offline capability (`backend/app/adaptive/learning.py`),
documented in `docs/ARUM.md` §4.

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

## 6. Auth model (design → implemented)

Public surface minimal: webhook endpoint authenticates by signature; dashboard/
monitoring/feedback endpoints require an issued `sk-…` bearer token (digest-only
storage, roles `viewer`/`operator`/`admin`, optional repository scope). The full
gate (`get_principal`, `require_role`, `require_repo_access`) is in
`backend/app/security/`; provisioning `ADAPTIVE_API_TOKEN_HASHES` / `_ROLES` /
`_SCOPES` and policy details are in `docs/SECURITY.md` §4/§10–11.

---

## 7. Endpoint matrix (planned → implemented)

Implementation will be tracked phase by phase in `docs/ROADMAP.md`. Endpoints listed
here that are not yet implemented must not be claimed as working.