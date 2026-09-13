# Database Design (PostgreSQL)

SQLAlchemy 2.x ORM, `asyncpg` driver, Alembic migrations. `pgvector` extension for
embeddings. This document is the ER specification; fixtures and migration code land in
Phase 2.

---

## 1. Conventions

- `id` — `UUID` primary key (server default `gen_random_uuid()`) except named natural
  keys (e.g. `github_id` unique constraints).
- `repository_id`, `review_id` — UUIDs, indexed, used on every join.
- Timestamps — `timestamptz NOT NULL DEFAULT now()`.
- Soft delete pattern where historical audit is required (`deleted_at`).
- All money/cost fields numeric with unit suffix documented (`cost_usd`).
- Enums are modelled as PostgreSQL enums OR `VARCHAR + CHECK` (kept consistent via
  Alembic); the domain constraints are shared by Pydantic schemas.

---

## 2. ER overview

```text
users ─────────────┐
repositories ──────┤
                   ▼
              pull_requests ──── review_iterations
                   │                    │
                   ▼                    ▼
                reviews ────────── developer_feedback
                   │                    ▲
                   │                    │
                   ▼                    │
             review_tasks ◄─────── agents
                   │                    │
                   ▼                    │
              findings ──┬──┤ finding_groups │
                   │     │                │
                   │     │                ▼
                   │     │           embeddings
                   │     ▼
                   ├────── comments(outcome) ──► developer_feedback
                   ▼
        repository_memory ───┬── coding_standards
                             └── agent_metrics · system_metrics
```

---

## 3. Tables

### 3.1 `users`
Authentication/authorization subjects (admin dashboard users).

| column | type | notes |
| --- | --- | --- |
| id | uuid PK | |
| github_login | text unique null | |
| email | citext unique null | |
| role | text | `admin` / `operator` / `viewer` |
| is_active | bool | |
| created_at / updated_at | timestamptz | |

### 3.2 `repositories`
| column | type | notes |
| --- | --- | --- |
| id | uuid PK | |
| github_id | bigint unique | GitHub repo id |
| full_name | text unique | `owner/name` |
| default_branch | text | |
| main_language | text | |
| github_installation_id | bigint | for App auth |
| priority_overrides | jsonb | repo-defined urgency/weights |
| review_settings | jsonb | strictness, budget caps, gates |
| is_active | bool | |
| created_at / updated_at | timestamptz | |
| deleted_at | timestamptz null | |

Index: `full_name`, `github_id`.

### 3.3 `pull_requests`
| column | type | notes |
| --- | --- | --- |
| id | uuid PK | |
| repository_id | uuid FK | |
| github_pr_id | bigint | |
| number | int | |
| title | text | |
| author_login | text | |
| base_ref / head_ref | text | |
| head_sha | text | current commit sha |
| last_reviewed_sha | text null | for iteration diffing |
| state | text | `open` / `closed` / `merged` |
| changed_files / additions / deletions | int | from API |
| priority_score | numeric | computed by priority queue |
| risk_class | text | `low` / `medium` / `high` |
| created_at / updated_at | timestamptz | |

Unique index: `(repository_id, number)`. Index: `priority_score`, `state`, `head_sha`.

### 3.4 `reviews`
| column | type | notes |
| --- | --- | --- |
| id | uuid PK | **review_id** exposed in APIs |
| pull_request_id | uuid FK not null | |
| repository_id | uuid FK not null | denormalised for isolation |
| status | enum | RECEIVED…COMPLETED/FAILED (state machine) |
| mode | text | `opened` / `synchronize` / `manual_rerun` |
| priority_score | numeric | captured at creation |
| risk_class | text | |
| github_delivery_id | varchar(64) unique | webhook idempotency key (Phase 3) |
| failure_reason | text null | reason if terminal FAILED (Phase 4) |
| budget_cap | int | resolved cap for this review |
| arum_version | text null | ARUM weights version used by the decision layer (Phase 8) |
| root_cause | text null | if FAILED |
| supervisor_notes | jsonb | time-ordered supervisor notes; appended on permanent agent failure (AGENTS.md §2) |
| status_history | jsonb | time-stamped state-transition trail (ARCHITECTURE.md §4.3) |
| feedback_aggregate | jsonb | rolling outcome counts snapshot |
| started_at / completed_at | timestamptz | |
| created_at / updated_at | timestamptz | |

Indexes: `(repository_id, status)`, `created_at`, `pull_request_id`.

### 3.5 `agents`
Registry of agent definitions/versions.
| column | type |
| --- | --- |
| id | uuid PK |
| key | text unique | `security`, `quality`, `performance`, `architecture`, `standards`, `supervisor` |
| name | text |
| version | text |
| is_active | bool |
| created_at / updated_at | timestamptz |

### 3.6 `review_tasks`
Per-agent work items.
| column | type | notes |
| --- | --- | --- |
| id | uuid PK | |
| review_id | uuid FK | |
| agent_id | uuid FK | |
| queue_name | text | target queue |
| status | enum | `pending`/`running`/`success`/`failed`/`skipped` |
| scope | jsonb | files/diff windows passed to agent |
| retry_count | int | |
| last_error | text null | |
| celery_task_id | text null | |
| started_at / completed_at | timestamptz | |

Index: `(review_id, agent_id)`, `status`, `queue_name`.

### 3.7 `findings`
Core detection output. See `docs/ARUM.md` §2 for feature provenance.

| column | type | notes |
| --- | --- | --- |
| id | uuid PK | finding_id |
| review_id | uuid FK | |
| repository_id | uuid FK | |
| agent_id | uuid FK | origin agent |
| file_path | text | |
| line_start / line_end | int null | absolute or diff-line (mode flag) |
| category | text | e.g. `security/authz`, `maintainability` |
| severity | enum | `critical` / `high` / `medium` / `low` / `info` |
| confidence | numeric 0–1 | |
| title | text | |
| description | text | |
| evidence | jsonb | snippet, diff line, rule id |
| suggested_fix | text | |
| code_context | text | trimmed source context |
| agent_reasoning_summary | text | concise, no chain-of-thought |
| duplicate_group | uuid null | FK finding_groups |
| arum_features | jsonb | the 8 ARUM feature values at decision time (ARUM.md §2) |
| arum_utility | numeric null | ARUM score at decision time; set on the representative finding of a redundancy group (members share the group's decision but keep their own row) |
| arum_version | text null | ARUM weights version used (Phase 8) |
| publication_status | enum | `candidate` / `scheduled` / `published` / `suppressed` / `resolved` / `stale` |
| feedback_status | enum null | outcome pending/labelled |
| temporal_weight | numeric | decay at decision time |
| created_at / updated_at | timestamptz | |

Indexes: `(review_id)`, `(repository_id, file_path)`, `(duplicate_group)`,
`(publication_status)`, `(category, severity)`, `(agent_id)`.

### 3.8 `finding_groups`
| column | type | notes |
| --- | --- | --- |
| id | uuid PK | the `duplicate_group` id |
| review_id | uuid FK | |
| representative_finding_id | uuid FK | |
| member_count | int | |
| redundancy_method | text | embedding cosine / proximity / category + hybrid |
| max_pairwise_similarity | numeric | |
| created_at | timestamptz | |

Phase 7 behaviour: a `finding_groups` row is created **only** for cross-agent
duplicates (`member_count >= 2`, `redundancy_method` = `hybrid: embedding cosine +
category + proximity`); singleton findings keep `findings.duplicate_group` NULL.
Because `findings.duplicate_group` and `finding_groups.representative_finding_id`
form a bi-directional FK, inserts are ordered findings (NULL group) → group rows →
group-id write-back on the member findings.

### 3.9 ARUM decision provenance

The decision layer (Phase 8/9, `app/adaptive/`) writes **reproducibility records**
(ARUM.md §10) as JSON-lines to a file configured by `settings.arum_decision_log_path`
(one object per `Decision`: review_id, finding_id, group_id, arum_version, features,
utility, weights, inputs_hash, budget_cap, safety_gate, selected; `TRACE_VERSION` = 2).
The files are the append-only research trail; `findings.arum_features/arum_utility/
arum_version` (§3.7 — plus `publication_status` = `scheduled`/`suppressed`) are the
denormalised snapshot, and `reviews.arum_version`/`reviews.budget_cap` record the
weights family and resolved review budget, so decision-time state is queryable without
reading the log. Every decision carries its resolved `budget_cap`; `safety_gate`
annotates why a selection deviated from a plain budget fill (`critical_mandatory`,
`high_confidence_high_severity`, `low_value_high_redundancy`, or NULL for budget
truncation). Selection under the review budget and safety gates (§7) resolved the
Phase 9 budget controller: the cap comes from the review's `RiskClass`
(`review_budget_low/medium/high` = 5/10/20; a review with no risk-class metadata gets
the high cap and a supervisor note), the cap is filled by ARUM rank after the gates,
and the result is also rolled up on the review row (`selected_count`,
`suppressed_count`) with a supervisor note that publication is a later phase.

### 3.10 `developer_feedback`
| column | type | notes |
| --- | --- | --- |
| id | uuid PK | |
| finding_id | uuid FK null | nullable for repo-level feedback |
| review_id | uuid FK | |
| repository_id | uuid FK | |
| outcome | enum | `ACCEPTED` `FIXED` `MODIFIED` `REJECTED` `DISMISSED` `IGNORED` `DISCUSSION` |
| source | text | `api` / `github_reaction` / `commit_diff` / `manual` |
| author_login | text | |
| commit_sha | text null | if tied to a commit |
| details | jsonb null | user-supplied note/reaction map |
| created_at | timestamptz | |

Index: `(finding_id)`, `(repository_id, outcome)`, `(review_id)`,
`(created_at DESC)` for temporal decay scans.

### 3.11 `repository_memory`
Per-repo adaptive aggregates (denormalised for read speed; rebuilt on feedback).
| column | type | notes |
| --- | --- | --- |
| id | uuid PK | |
| repository_id | uuid FK unique | |
| snapshot | jsonb | rolling metrics: category→accept/reject shares (decayed), component→findings, strictness, common aliases |
| decay_params | jsonb | τ, λ |
| arum_weights | jsonb null | repo-level weight overrides |
| updated_at | timestamptz | |
| version | int | monotonic rebuild counter |

### 3.12 `coding_standards`
| column | type |
| --- | --- |
| id | uuid PK |
| repository_id | uuid FK |
| rule_key | text | e.g. `naming/snake_case` |
| description | text |
| source | text | `auto_extracted` / `manual` |
| enforcement_level | text | `hard` / `preferred` / `soft` |
| is_active | bool |
| created_at / updated_at | timestamptz |

Unique: `(repository_id, rule_key)`.

### 3.13 `embeddings`
`pgvector`-backed rows for candidate findings (used by redundancy + RAG).
| column | type | notes |
| --- | --- | --- |
| id | uuid PK | |
| repository_id | uuid FK | |
| finding_id | uuid FK null | |
| resource_type | text | `finding` / `standard` / `decision` / `comment` |
| content_hash | text | dedupe |
| vector | vector(1536) | default embedding dim, configurable |
| model | text | embedding model id |
| created_at | timestamptz | |

Index: HNSW on `vector` (cosine).

### 3.14 `review_iterations`
| column | type | notes |
| --- | --- | --- |
| id | uuid PK | |
| review_id | uuid FK | |
| iteration | int | 1 = first |
| base_sha / head_sha | text | review window |
| diff_stats | jsonb | changed files/lines summary |
| agents_invoked | jsonb | agent→file lists |
| tokens_used / cost_usd | numeric | |
| latency_ms | int | |
| published_count | int | |
| resolved_count | int | |
| stale_count | int | |
| created_at | timestamptz | |

Index: `(review_id, iteration)`.

### 3.15 `agent_metrics`
| column | type | notes |
| --- | --- | --- |
| id | uuid PK | |
| review_id | uuid FK | |
| agent_id | uuid FK | |
| task_id | uuid FK | |
| duration_ms | int | |
| tokens_in / tokens_out | int | |
| cost_usd | numeric | |
| findings_raw / findings_valid | int | |
| failed_results | int | |
| error | text null | |
| created_at | timestamptz | |

Index: `(agent_id, created_at)`.

### 3.16 `system_metrics`
Aggregate/derived counters (also mirror Prometheus; persisted for research queries).
| column | type | notes |
| --- | --- | --- |
| id | uuid PK | |
| metric_name | text | |
| repository_id | uuid FK null | |
| value | double precision | |
| labels | jsonb | |
| sampled_at | timestamptz | |

Index: `(metric_name, sampled_at)`.

### 3.17 `dead_letters`
Durable dead-letter log (docs/QUEUE.md §6): tasks that exhausted `max_retries`
without a silent drop. The Redis dead-letter list is the ephemeral signal; this
table backs the dashboard/alerting.
| column | type | notes |
| --- | --- | --- |
| id | uuid PK | |
| review_id | uuid FK not null | terminal FAILED review |
| delivery_id | text null | webhook delivery, if any |
| queue_name | text | from QUEUE.md §1 |
| task_name | text | Celery task name |
| error | text | final error message |
| attempts | int | total retry attempts |
| created_at | timestamptz | |

Index: `review_id`.

---

## 4. Index plan summary

- Hot paths: review-by-status, findings-by-review, feedback-by-(repo,outcome), RAG by
  repository + vector, last iteration per review.
- Functional indexes: `(repository_id, created_at desc)` on feedback for decay scans.
- `pgvector` HNSW index; fallback exact cosine index while small.

---

## 5. Isolation discipline

Every query that touches findings/memory/feedback/reviews is filtered by
`repository_id` (passed from auth context). Cross-repository access is denied by
policy and by the data layer, with a dedicated test for repository isolation.

---

## 6. Migration workflow

Alembic with async engine. Migrations in `backend/alembic/versions/`. Rules:
- one revision per logical change;
- destructive operations (`drop`, `rename`) in separate forward-only revisions;
- `pgvector` enabled via migration-created extension;
- review statuses enforced via DB CHECK or enum aligned with Pydantic enums.