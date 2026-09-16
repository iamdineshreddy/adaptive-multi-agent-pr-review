# Security Model

Security requirements for the whole system. Implementation lands in Phase 15; this
doc pins the requirements now.

## 1. Threat model (summary)

- External attacker can post arbitrary webhook payloads, craft GitHub events, or send
  fabricated feedback.
- Repository content is untrusted: PR bodies, diffs, comments can contain prompt
  injection and malicious instructions.
- Operators may misconfigure secrets or over-permission tokens.
- Public endpoints are subject to spam/DoS via repeated PR events.

## 2. Webhook integrity

- Verify `X-Hub-Signature-256` (HMAC-SHA256, constant-time compare) with the app
  secret, per-repo secrets supported. Reject with `401` on mismatch.
- Accept known event names only; validate everything with Pydantic.

## 3. Secrets management

- No secrets in code, env commits, or logs.
- GitHub tokens: application installation auth preferred; PAT fallback environment
  scoped. Never rendered by the frontend.
- LLM keys: environment/secrets only.

## 4. Authentication & authorization

- Public surface minimal (webhook signature + feedback token + dashboard API keys).
- `user` roles: admin / operator / viewer; per-repo access enforced.
- Repository isolation enforced at query level (`repository_id`), with dedicated tests.

## 5. Untrusted content handling

- Diffs and PR text are treated as data; neutralisation of instruction-like markers;
  fixed versioned system prompt; schema-constrained outputs (see `docs/AGENTS.md` §5).
- No auto-execution of code in repository content; external tooling runs actions only
  via the GitHub client with locked permissions.

## 6. Inputs / rate limits

- Webhook + feedback endpoints rate-limited per IP and per repository burst.
- Request size caps; depth-limited JSON; pagination caps.

## 7. Logging & audit

- Structured logs; secrets scrubbed by redaction filter before emission.
- Admin actions (settings changes, reruns, manual outcome labels) audited.

## 8. Dependency & runtime hygiene

- Locked dependency sets; image scanning in CI; least-privilege worker containers
  (read-only filesystem where possible); database migrations use dedicated user.

## 9. Verification artifacts (planned)

- Test suite: signature verification, injection suite, isolation, auth matrix.
- A `SECURITY.md` implementation appendix is added after Phase 15 with concrete
  findings and checks (no hidden assumptions).

## 10. Implementation appendix (Phase 15, part 1 — authn/authz)

- `app/security/tokens.py` — bearer-token primitives: `sk-` tokens are random
  (32-byte URL-safe); only SHA-256 digests are stored
  (`ADAPTIVE_API_TOKEN_HASHES`); lookups use constant-time compare
  (`hmac.compare_digest`); `issue_token(label, role, repos)` provisions a token
  whose plaintext is shown once; roles are ranked `viewer < operator < admin`.
- `app/security/auth.py` — FastAPI gate `get_principal` (Bearer header, `401`
  with `WWW-Authenticate` on missing/unknown), `require_role(minimum)` (`403`),
  `require_repo_access` (repo-scoped principals get `403` outside their set),
  and `SettingsTokenProvider` reading `ADAPTIVE_API_TOKEN_HASHES` /
  `ADAPTIVE_API_TOKEN_ROLES` / `ADAPTIVE_API_TOKEN_SCOPES` (digest → role,
  digest → allowed repository ids; empty/absent scope = all repositories).
- Every dashboard + monitoring endpoint is behind the gate (router-level
  dependency). Repository detail/memory/feedback additionally enforce the
  principal's repo scope (dedicated isolation tests).
- Left for part 2 in this phase: feedback-endpoint bearer token, admin write
  actions + their audit trail, and request body/size caps.

## 11. Implementation appendix (Phase 15, part 2 — feedback auth, list scoping, body cap)

- `POST /api/v1/feedback` now sits behind the same bearer-token gate as the
  dashboard; the repository is still resolved server-side from the finding, and
  scoped tokens are limited to feedback on repositories inside their scope
  (`repository_out_of_scope` 403). Per-IP rate limiting is unchanged.
- `GET /api/v1/repositories` (list) now honours the caller's scope: scoped
  principals only see their own repositories; detail/memory/feedback endpoints
  return 403 out of scope (as in part 1). Admin write actions and the audit
  trail for them landed in part 2b with the FR-7.3 rerun/repository-settings
  routes that exercise them (see `docs/SECURITY.md` §12 below) — nothing was
  fabricated against an endpoint surface that did not exist.
- Request body-size cap: `ADAPTIVE_MAX_REQUEST_BODY_BYTES` (default 1 MiB) is
  enforced by `app/middleware/body_limit.py` before routing (413
  `body_too_large`), guarding the public write endpoints (webhook + feedback)
  against oversized permutation payloads.

## 12. Implementation appendix (Phase 15, part 2b — admin write actions + audit trail, FR-7.3)

- `app/api/admin.py` (mounted at `/api/v1`, router-level `get_principal` gate)
  exposes exactly the FR-7.3 admin write surface plus its audit trail — nothing
  is registered before it exists:
  - `POST /api/v1/reviews/{review_id}/rerun` — `require_role("operator")`; source
    review resolved **server-side** and checked with `require_repo_access` before
    any write; creates a new `Review` (`mode=MANUAL_RERUN`, `status=QUEUED`) and
    dispatches it via the config-backed `ReviewDispatcher` (`celery` provider, or
    the log-only fallback) → `202` with the new run's tracking payload.
  - `PATCH /api/v1/repositories/{repository_id}/settings` —
    `require_role("operator")` + repo scope; validated merge (urgency, budget,
    gates, ARUM weights, decay) into `Repository.review_settings`. Weight
    overrides are validated through the real ARUM override grammar
    (`ArumWeights.with_overrides`) → `422 invalid_settings` on unknown keys, so a
    typo can never silently change policy. Partial updates merge key-wise.
  - `GET /api/v1/audit-log` — `require_role("admin")`; newest-first, `action`
    filter + pagination; a scoped admin token sees only its own repositories'
    rows (the store filters on `repository_id IN scope`).
- Audit trail (`app/models/audit.py`, `admin_audit_log`, migration `0007`):
  append-only rows per write — `action`, polymorphic `target_kind`/`target_id`,
  FK'd `repository_id`, the acting principal's **SHA-256 token digest** (never
  the token), role and label, `before`/`after` JSON state, `created_at`. Rows are
  written in the same request as the mutation so an unrecorded admin action is
  impossible, and reads are admin-only. Future admin writes append under the same
  contract rather than multiplying ad-hoc audit columns.
- Stored settings are honest inputs, not dead config: the orchestrator decision
  layer consumes the per-repo budget caps / safety gates / ARUM weight overrides
  (`_decide_findings`) and per-repo memory decay (`_refresh_repository_memory`),
  each falling back to the global default when unset.
- The authn/authz matrix from parts 1–2 is extended and covered by tests
  (viewer 403, below-minimum role 403, out-of-scope 403, unauthenticated 401,
  scoped-admin audit filtering) in `tests/test_api_admin.py` (21 tests);
  512 tests pass with the whole suite (11 live-service self-skip).