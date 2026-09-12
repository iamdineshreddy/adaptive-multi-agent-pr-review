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