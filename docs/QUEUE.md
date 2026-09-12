# Queue Architecture

Celery + Redis broker. This document specifies the queues, priority policy, and
failure model (the spec-level summary is in `docs/ARCHITECTURE.md` §5–6).

---

## 1. Queues

| Queue | Producer | Consumer | Concurrency notes |
| --- | --- | --- | --- |
| `pr_ingestion_queue` | Webhook/API | Ingestion worker | low concurrency; validates & computes priority |
| `review_task_queue` | Orchestrator | Orchestration worker | fan-out dispatcher |
| `security_queue` | Supervisor | Security agent worker | LLM-bound; rate-limited |
| `quality_queue` | Supervisor | Quality agent worker | LLM-bound |
| `performance_queue` | Supervisor | Performance agent worker | LLM-bound |
| `architecture_queue` | Supervisor | Architecture agent worker | LLM-bound |
| `standards_queue` | Supervisor | Standards agent worker | LLM-bound |
| `decision_queue` | Orchestrator | Decision worker | runs redundancy → ARUM → budget |
| `publisher_queue` | Decision worker | Publisher worker | GitHub API rate-limit aware |
| `feedback_queue` | Feedback API + GitHub activity | Feedback worker | updates memory; emits decay recompute |
| `default` | Fallback | Generic | health/aux tasks |

Agent queues run **in parallel**; a review waits on the fan-out join before it moves to
consolidation. Queues are independent so the LLM provider can be throttled per type.

---

## 2. Priority policy

PR priority score is computed at ingestion from **all documented, configurable**
factors (no hidden magic numbers):

```text
priority_score =
   w_sec    · SecurityRisk(0..1)
 + w_impact · ChangeImpact(0..1)
 + w_hist   · HistoricalRisk(0..1)
 + w_comp   · ComponentCriticality(0..1)
 + w_dep    · DependencyRisk(0..1)
 + w_urgency· RepoPriority(0..1)   [repo override / urgency]
```

Default weights (documented, env-configurable):

| weight | default | rationale |
| --- | --- | --- |
| `w_sec` | 0.30 | security-sensitive files / auth / injection surface |
| `w_impact` | 0.25 | changed files + lines, binned log-scale |
| `w_hist` | 0.15 | previous finding severity on repo/component |
| `w_comp` | 0.15 | touched critical components (configurable map) |
| `w_dep` | 0.10 | dependency/config file changes |
| `w_urgency` | 0.05 | repository-set urgency |

`risk_class` is derived from the score: `low` < 3.0, `medium` ≤ 6.0, `high` > 6.0
(thresholds configurable). `risk_class` selects the review budget cap (ARUM §7).

---

## 3. Ordering mechanism

Priority is enforced with a Redis sorted set (`priority.score`) — `ZADD` with
`priority_score`, score tie-break by FIFO ordinal. A single dispatcher task pops the
highest-priority review and hands it to the orchestrator. Rationale: Redis zsets are
O(log n) and simpler than Celery's built-in priority (which only supports a small
fixed range and is not authoritative when multiple workers consume).

The DB row remains the system of record; the zset is a scheduling hint.

---

## 4. Task lifecycle

1. Task enqueued with `task_id` stored on the review/task row.
2. Worker `acks_late`; result written to `review_tasks`.
3. Success → status update; fail → retry with exponential backoff + jitter
   (e.g. 30s, 60s, 120s, 240s cap `max_retries=4` for LLM tasks, higher for GitHub).
4. Exhausted → `FAILED` + `failure_reason` + alert metric; review state machine
   handles downstream fallout.

## 5. Idempotency

- Webhook deliveries keyed by `X-GitHub-Delivery` (Redis + DB).
- Every task carries its `task_id`; rerun/re-dispatch upserts.
- Publisher tasks are idempotent by `finding_id` + `head_sha` (a review comment is
  created at most once per (finding, commit)).

## 6. Poison / dead-letter handling

Tasks exceeding `max_retries` are routed to a dead-letter log (table + Prometheus
cardinality counter), surfaced on the dashboard, and a review may be marked `FAILED`.
No silent drop.

## 7. Back-pressure

- Queue depth gauges per queue (Prometheus); alerts at configurable thresholds.
- LLM provider rate limits → per-provider semaphore inside worker + bulkhead pattern;
  publisher respects GitHub REST rate limits with a token bucket.
- Long reviews can be pre-empted by a repo lock: a `synchronize` supersedes an
  in-flight `opened` review for the same PR (the newer review wins the lock).