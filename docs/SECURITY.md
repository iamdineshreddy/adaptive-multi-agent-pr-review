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
  trail for them ship with the FR-7.3 rerun/repository-settings routes (see
  docs/ROADMAP.md Phase 15 split note) rather than being fabricated against an
  endpoint surface that does not yet exist.
- Request body-size cap: `ADAPTIVE_MAX_REQUEST_BODY_BYTES` (default 1 MiB) is
  enforced by `app/middleware/body_limit.py` before routing (413
  `body_too_large`), guarding the public write endpoints (webhook + feedback)
  against oversized permutation payloads.