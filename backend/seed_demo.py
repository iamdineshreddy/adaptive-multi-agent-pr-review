"""Seed a demo dataset for dashboard showcases.

Idempotent: truncates the demo tables and re-inserts a deterministic dataset
(repositories, agents, PRs, reviews, findings, feedback, memory, metrics).
Safe only against a disposable/demo database.

Usage:
    python seed_demo.py
"""

from __future__ import annotations

import asyncio
import os
import uuid
from datetime import UTC, datetime, timedelta

from sqlalchemy import text
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from app.models import (
    Agent,
    AgentMetric,
    CodingStandard,
    DeveloperFeedback,
    FeedbackOutcome,
    FeedbackSource,
    Finding,
    FindingGroup,
    FindingStatus,
    PRState,
    PullRequest,
    Repository,
    RepositoryMemory,
    Review,
    ReviewIteration,
    ReviewMode,
    ReviewStatus,
    RiskClass,
    Severity,
)

URL = os.environ.get(
    "ADAPTIVE_DATABASE_URL",
    "postgresql+asyncpg://adaptive:adaptive@localhost:5434/adaptive_review",
)

NOW = datetime.now(UTC)


def uid(name: str) -> uuid.UUID:
    return uuid.uuid5(uuid.NAMESPACE_DNS, f"demo:{name}")


AGENTS = {
    "security": "Security Agent",
    "quality": "Code Quality Agent",
    "performance": "Performance Agent",
    "architecture": "Architecture Agent",
    "standards": "Standards Agent",
}

REPOS = [
    (
        "payments",
        "acme/payments-api",
        9001,
        "main",
        "python",
    ),
    (
        "console",
        "acme/web-console",
        9002,
        "develop",
        "typescript",
    ),
    (
        "pipeline",
        "acme/data-pipeline",
        9003,
        "main",
        "python",
    ),
]


def arum(sev: float, conf: float, agree: float, util: float) -> dict:
    return {
        "severity": sev,
        "confidence": conf,
        "agent_agreement": agree,
        "historical_actionability": 0.62,
        "historical_rejection": 0.14,
        "repository_relevance": 0.71,
        "context_relevance": 0.55,
        "redundancy": 0.0,
        "_utility": util,
    }


async def main() -> None:
    engine = create_async_engine(URL)
    session_factory = async_sessionmaker(engine, expire_on_commit=False)

    tables = (
        "admin_audit_log, dead_letters, system_metrics, agent_metrics, "
        "review_tasks, developer_feedback, embeddings, findings, finding_groups, "
        "review_iterations, reviews, pull_requests, coding_standards, "
        "repository_memory, repositories, agents"
    )
    async with engine.begin() as conn:
        await conn.execute(text(f"TRUNCATE TABLE {tables} RESTART IDENTITY CASCADE"))

    async with session_factory() as s:
        # --- Agents ---------------------------------------------------------
        for key, name in AGENTS.items():
            s.add(Agent(id=uid(f"agent:{key}"), key=key, name=name, version="1.4.0"))

        # --- Repositories ---------------------------------------------------
        repo_ids = {}
        for key, full_name, github_id, branch, language in REPOS:
            rid = uid(f"repo:{key}")
            repo_ids[key] = rid
            s.add(
                Repository(
                    id=rid,
                    github_id=github_id,
                    full_name=full_name,
                    default_branch=branch,
                    main_language=language,
                    priority_overrides={},
                    review_settings={
                        "review_budget_high": 20,
                        "review_budget_medium": 10,
                    },
                    is_active=True,
                    created_at=NOW - timedelta(days=120),
                )
            )

        s.add_all(
            [
                CodingStandard(
                    id=uid("std:payments:1"),
                    repository_id=repo_ids["payments"],
                    rule_key="security/no-string-sql",
                    description="All SQL must use parameterised queries.",
                    source="auto_extracted",
                    enforcement_level="mandatory",
                ),
                CodingStandard(
                    id=uid("std:payments:2"),
                    repository_id=repo_ids["payments"],
                    rule_key="quality/typed-public-functions",
                    description="Public functions require full type annotations.",
                    source="auto_extracted",
                    enforcement_level="preferred",
                ),
                CodingStandard(
                    id=uid("std:console:1"),
                    repository_id=repo_ids["console"],
                    rule_key="standards/react-hooks",
                    description="Hooks must not be called conditionally.",
                    source="auto_extracted",
                    enforcement_level="preferred",
                ),
            ]
        )

        # Flush agents/repositories first. The findings <-> finding_groups FK
        # cycle defeats SQLAlchemy's global table sort, so dependencies are
        # flushed explicitly in stages rather than left to the unit of work.
        await s.flush()

        # --- Pull requests + reviews ---------------------------------------
        prs = [
            # key, repo, number, title, author, status, risk, priority, mode,
            # created offset (hours), failure/notes
            (
                "pr-482",
                "payments",
                482,
                "Add Stripe webhook handling",
                "priya-dev",
                ReviewStatus.PUBLISHED,
                RiskClass.HIGH,
                8.7,
                ReviewMode.OPENED,
                2,
            ),
            (
                "pr-483",
                "payments",
                483,
                "Refactor auth middleware",
                "marco",
                ReviewStatus.DECIDING,
                RiskClass.MEDIUM,
                6.2,
                ReviewMode.OPENED,
                5,
            ),
            (
                "pr-479",
                "console",
                479,
                "Upgrade dependencies and tooling",
                "sana",
                ReviewStatus.FAILED,
                RiskClass.LOW,
                3.1,
                ReviewMode.OPENED,
                9,
            ),
            (
                "pr-470",
                "pipeline",
                470,
                "Query builder cleanup",
                "devon",
                ReviewStatus.ITERATING,
                RiskClass.MEDIUM,
                5.4,
                ReviewMode.SYNCHRONIZE,
                26,
            ),
            (
                "pr-468",
                "pipeline",
                468,
                "Add cursor pagination to exports",
                "li",
                ReviewStatus.COMPLETED,
                RiskClass.LOW,
                2.4,
                ReviewMode.OPENED,
                50,
            ),
            (
                "pr-484",
                "console",
                484,
                "Improve audit log filtering",
                "ana",
                ReviewStatus.WAITING_FOR_FEEDBACK,
                RiskClass.MEDIUM,
                6.8,
                ReviewMode.OPENED,
                1,
            ),
        ]

        review_ids = {}
        for (
            key,
            repo_key,
            number,
            title,
            author,
            status,
            risk,
            priority,
            mode,
            hours,
        ) in prs:
            pr_id = uid(f"{key}:pr")
            review_id = uid(f"{key}:review")
            review_ids[key] = review_id
            created = NOW - timedelta(hours=hours)
            s.add(
                PullRequest(
                    id=pr_id,
                    repository_id=repo_ids[repo_key],
                    github_pr_id=700000 + number,
                    number=number,
                    title=title,
                    author_login=author,
                    base_ref="main",
                    head_ref=f"feat/{key}",
                    head_sha=f"{(key + '0' * 40)[:40]}",
                    state=PRState.OPEN,
                    changed_files=6 + (number % 5),
                    additions=120 + number,
                    deletions=40 + (number % 30),
                    priority_score=priority,
                    risk_class=risk,
                    created_at=created,
                )
            )
            notes = {
                ReviewStatus.FAILED: [
                    "security agent timed out after 120s; partial findings preserved"
                ],
                ReviewStatus.DECIDING: [
                    "ARUM selected 4 of 6 candidates — publication is the next phase"
                ],
                ReviewStatus.PUBLISHED: [
                    "ARUM selected 6 of 9 candidates",
                    "Published as GitHub PR review #9011",
                ],
                ReviewStatus.ITERATING: [
                    "Round 2: 2 stale findings re-evaluated, 3 carried forward"
                ],
            }.get(status, [])
            s.add(
                Review(
                    id=review_id,
                    pull_request_id=pr_id,
                    repository_id=repo_ids[repo_key],
                    status=status,
                    mode=mode,
                    priority_score=priority,
                    risk_class=risk,
                    budget_cap=10,
                    arum_version="v1",
                    supervisor_notes=notes,
                    status_history=[
                        {"status": "RECEIVED", "at": created.isoformat()},
                        {
                            "status": status.value,
                            "at": (created + timedelta(minutes=12)).isoformat(),
                        },
                    ],
                    failure_reason="agent_timeout"
                    if status == ReviewStatus.FAILED
                    else None,
                    root_cause=(
                        "security agent exceeded the 120s hard timeout"
                        if status == ReviewStatus.FAILED
                        else None
                    ),
                    github_review_id=9011 if status == ReviewStatus.PUBLISHED else None,
                    published_at=(
                        created + timedelta(minutes=15)
                        if status == ReviewStatus.PUBLISHED
                        else None
                    ),
                    started_at=created,
                    completed_at=(
                        created + timedelta(minutes=18)
                        if status
                        in (
                            ReviewStatus.PUBLISHED,
                            ReviewStatus.COMPLETED,
                            ReviewStatus.FAILED,
                        )
                        else None
                    ),
                    created_at=created,
                )
            )

        # Flush reviews before findings reference them.
        await s.flush()

        # --- Findings -------------------------------------------------------
        # (review key, idx, agent, file, line, category, severity, conf, title,
        #  description, status, utility, group)
        finding_rows = [
            (
                "pr-482",
                1,
                "security",
                "stripe/webhooks.py",
                88,
                "security/sql-injection",
                Severity.CRITICAL,
                0.94,
                "SQL injection in webhook event lookup",
                "Event id from the request body is interpolated into a SQL string.",
                FindingStatus.PUBLISHED,
                0.97,
            ),
            (
                "pr-482",
                2,
                "security",
                "stripe/webhooks.py",
                132,
                "security/secrets",
                Severity.HIGH,
                0.91,
                "Hardcoded Stripe signing secret",
                "The webhook signing secret is committed directly in source.",
                FindingStatus.PUBLISHED,
                0.90,
            ),
            (
                "pr-482",
                3,
                "quality",
                "stripe/webhooks.py",
                88,
                "quality/error-handling",
                Severity.MEDIUM,
                0.78,
                "Unhandled exception path in event handler",
                "A failed event lookup raises without a fallback or logging.",
                FindingStatus.PUBLISHED,
                0.61,
            ),
            (
                "pr-482",
                4,
                "performance",
                "stripe/webhooks.py",
                150,
                "performance/query",
                Severity.MEDIUM,
                0.72,
                "N+1 query while loading event metadata",
                "Each event triggers a separate metadata query inside a loop.",
                FindingStatus.PUBLISHED,
                0.58,
            ),
            (
                "pr-482",
                5,
                "security",
                "stripe/webhooks.py",
                132,
                "security/secrets",
                Severity.HIGH,
                0.69,
                "Sensitive credential in source",
                "Duplicate report of the committed signing secret.",
                FindingStatus.SUPPRESSED,
                0.44,
            ),
            (
                "pr-482",
                6,
                "architecture",
                "billing/service.py",
                41,
                "architecture/layering",
                Severity.LOW,
                0.55,
                "Handler reaches into persistence layer directly",
                "The webhook handler bypasses the service boundary.",
                FindingStatus.SUPPRESSED,
                0.22,
            ),
            (
                "pr-483",
                1,
                "security",
                "auth/middleware.py",
                57,
                "security/authorization",
                Severity.HIGH,
                0.88,
                "Session accepted without verifying revocation",
                "A revoked session may still pass the middleware check.",
                FindingStatus.SCHEDULED,
                0.88,
            ),
            (
                "pr-483",
                2,
                "quality",
                "auth/middleware.py",
                24,
                "quality/complexity",
                Severity.MEDIUM,
                0.74,
                "Middleware branch count exceeds project threshold",
                "Twelve nested conditionals make the decision path hard to audit.",
                FindingStatus.SCHEDULED,
                0.52,
            ),
            (
                "pr-483",
                3,
                "performance",
                "auth/tokens.py",
                90,
                "performance/memory",
                Severity.LOW,
                0.61,
                "Token cache grows without eviction",
                "The in-memory cache has no size bound or TTL.",
                FindingStatus.SCHEDULED,
                0.31,
            ),
            (
                "pr-483",
                4,
                "standards",
                "auth/middleware.py",
                12,
                "standards/naming",
                Severity.INFO,
                0.49,
                "Inconsistent handler naming",
                "Handler names mix snake_case and verb-first conventions.",
                FindingStatus.CANDIDATE,
                0.09,
            ),
            (
                "pr-470",
                1,
                "quality",
                "query/builder.py",
                77,
                "quality/duplication",
                Severity.MEDIUM,
                0.81,
                "Duplicated predicate assembly",
                "The same condition builder is repeated in two branches.",
                FindingStatus.RESOLVED,
                0.66,
            ),
            (
                "pr-470",
                2,
                "performance",
                "query/executor.py",
                144,
                "performance/loop",
                Severity.MEDIUM,
                0.79,
                "Repeated sort inside batch loop",
                "Sorting on every iteration makes batching quadratic.",
                FindingStatus.STALE,
                0.63,
            ),
            (
                "pr-470",
                3,
                "architecture",
                "query/builder.py",
                33,
                "architecture/coupling",
                Severity.LOW,
                0.57,
                "Builder imports concrete adapter",
                "Hard dependency on the Postgres adapter blocks reuse.",
                FindingStatus.STALE,
                0.34,
            ),
            (
                "pr-484",
                1,
                "security",
                "audit/filters.py",
                61,
                "security/authorization",
                Severity.HIGH,
                0.86,
                "Filter accepts arbitrary actor ids",
                "Callers can enumerate other users' audit entries.",
                FindingStatus.PUBLISHED,
                0.89,
            ),
            (
                "pr-484",
                2,
                "quality",
                "audit/filters.py",
                20,
                "quality/maintainability",
                Severity.LOW,
                0.64,
                "Filter construction duplicated across routes",
                "Three routes rebuild the same filter object.",
                FindingStatus.PUBLISHED,
                0.28,
            ),
        ]

        finding_ids = {}
        for (
            rkey,
            idx,
            agent,
            path,
            line,
            category,
            severity,
            conf_int,
            title,
            desc,
            fstatus,
            utility,
        ) in finding_rows:
            fid = uid(f"{rkey}:finding:{idx}")
            finding_ids[(rkey, idx)] = fid
            created = NOW - timedelta(hours=3 if rkey == "pr-482" else 6)
            feats = arum(
                {
                    Severity.CRITICAL: 1.0,
                    Severity.HIGH: 0.8,
                    Severity.MEDIUM: 0.5,
                    Severity.LOW: 0.25,
                    Severity.INFO: 0.05,
                }[severity],
                conf_int,
                1.0 if agent == "security" else 0.5,
                utility,
            )
            s.add(
                Finding(
                    id=fid,
                    review_id=review_ids[rkey],
                    repository_id=repo_ids[
                        "payments"
                        if rkey in ("pr-482", "pr-483")
                        else ("console" if rkey in ("pr-479", "pr-484") else "pipeline")
                    ],
                    agent_id=uid(f"agent:{agent}"),
                    file_path=path,
                    line_start=line,
                    line_end=line + 3,
                    category=category,
                    severity=severity,
                    confidence=conf_int,
                    title=title,
                    description=desc,
                    evidence={"snippet": f"# {path}:{line}", "rule": category},
                    suggested_fix="Use the project helper and add a regression test.",
                    code_context="+ " + desc,
                    agent_reasoning_summary="Static rule match on changed lines.",
                    arum_features=feats,
                    arum_utility=utility,
                    arum_version="v1",
                    publication_status=fstatus,
                    feedback_result=(
                        FeedbackOutcome.ACCEPTED
                        if fstatus == FindingStatus.PUBLISHED and idx == 1
                        else None
                    ),
                    feedback_labelled=(fstatus == FindingStatus.PUBLISHED and idx == 1),
                    github_comment_id=(
                        5000 + idx if fstatus == FindingStatus.PUBLISHED else None
                    ),
                    github_review_id=(
                        9011 if fstatus == FindingStatus.PUBLISHED else None
                    ),
                    temporal_weight=1.0,
                    created_at=created,
                )
            )

        # Flush reviews/findings first: findings <-> finding_groups is a FK cycle,
        # so SQLAlchemy cannot infer insertion order on its own.
        await s.flush()

        # Duplicate group: the suppressed duplicate on PR 482 (finding 5).
        group_id = uid("pr-482:group:secrets")
        s.add(
            FindingGroup(
                id=group_id,
                review_id=review_ids["pr-482"],
                representative_finding_id=finding_ids[("pr-482", 2)],
                member_count=2,
                redundancy_method="hybrid: embedding cosine + category + proximity",
                max_pairwise_similarity=0.9231,
                created_at=NOW - timedelta(hours=3),
            )
        )
        # Flush group so the FK exists before the member references it.
        await s.flush()
        dup = await s.get(Finding, finding_ids[("pr-482", 5)])
        if dup is not None:
            dup.duplicate_group = group_id

        # --- Iterations -----------------------------------------------------
        s.add_all(
            [
                ReviewIteration(
                    id=uid("pr-470:iter:1"),
                    review_id=review_ids["pr-470"],
                    iteration=1,
                    base_sha="a" * 40,
                    head_sha="b" * 40,
                    diff_stats={"added": 84, "removed": 12, "files": 4},
                    agents_invoked={"quality": 1, "performance": 1},
                    tokens_used=18420.0,
                    cost_usd=0.041,
                    latency_ms=21400,
                    published_count=2,
                    resolved_count=0,
                    stale_count=0,
                    created_at=NOW - timedelta(hours=30),
                ),
                ReviewIteration(
                    id=uid("pr-470:iter:2"),
                    review_id=review_ids["pr-470"],
                    iteration=2,
                    base_sha="b" * 40,
                    head_sha="c" * 40,
                    diff_stats={"added": 31, "removed": 5, "files": 2},
                    agents_invoked={"performance": 1},
                    tokens_used=6120.0,
                    cost_usd=0.013,
                    latency_ms=8400,
                    published_count=1,
                    resolved_count=1,
                    stale_count=2,
                    created_at=NOW - timedelta(hours=26),
                ),
                ReviewIteration(
                    id=uid("pr-482:iter:1"),
                    review_id=review_ids["pr-482"],
                    iteration=1,
                    base_sha="d" * 40,
                    head_sha="e" * 40,
                    diff_stats={"added": 156, "removed": 44, "files": 7},
                    agents_invoked={k: 1 for k in AGENTS},
                    tokens_used=56230.0,
                    cost_usd=0.128,
                    latency_ms=48200,
                    published_count=6,
                    resolved_count=0,
                    stale_count=0,
                    created_at=NOW - timedelta(hours=2),
                ),
            ]
        )

        # --- Agent metrics (tokens/cost/latency rollups) ---------------------
        for i, (agent, tokens_in, tokens_out, duration) in enumerate(
            [
                ("security", 8200, 1600, 9800),
                ("quality", 6100, 1100, 7200),
                ("performance", 5900, 900, 6900),
                ("architecture", 5400, 800, 6400),
                ("standards", 4300, 600, 5100),
            ]
        ):
            s.add(
                AgentMetric(
                    id=uid(f"pr-482:metric:{agent}"),
                    review_id=review_ids["pr-482"],
                    agent_id=uid(f"agent:{agent}"),
                    duration_ms=duration,
                    tokens_in=tokens_in,
                    tokens_out=tokens_out,
                    cost_usd=round((tokens_in + tokens_out) * 2.5e-6, 6),
                    findings_raw=3,
                    findings_valid=2 if i else 2,
                    failed_results=0,
                    created_at=NOW - timedelta(hours=2),
                )
            )

        # --- Developer feedback --------------------------------------------
        feedback = [
            (
                "pr-482",
                1,
                FeedbackOutcome.FIXED,
                FeedbackSource.GITHUB_REACTION,
                "priya-dev",
                1,
            ),
            ("pr-482", 2, FeedbackOutcome.ACCEPTED, FeedbackSource.API, "priya-dev", 2),
            ("pr-482", 3, FeedbackOutcome.DISMISSED, FeedbackSource.API, "marco", 3),
            (
                "pr-482",
                4,
                FeedbackOutcome.IGNORED,
                FeedbackSource.GITHUB_REACTION,
                "sana",
                4,
            ),
            ("pr-483", 1, FeedbackOutcome.MODIFIED, FeedbackSource.MANUAL, "marco", 5),
            ("pr-470", 1, FeedbackOutcome.REJECTED, FeedbackSource.API, "devon", 6),
        ]
        for rkey, idx, outcome, source, author, order in feedback:
            s.add(
                DeveloperFeedback(
                    id=uid(f"{rkey}:feedback:{order}"),
                    finding_id=finding_ids.get((rkey, idx)),
                    review_id=review_ids[rkey],
                    repository_id=repo_ids[
                        "payments" if rkey in ("pr-482", "pr-483") else "pipeline"
                    ],
                    outcome=outcome,
                    source=source,
                    author_login=author,
                    commit_sha="f" * 40,
                    details={"note": "Recorded during demo seeding."},
                    created_at=NOW - timedelta(hours=order),
                )
            )

        # --- Repository memory ---------------------------------------------
        s.add(
            RepositoryMemory(
                id=uid("payments:memory"),
                repository_id=repo_ids["payments"],
                snapshot={
                    "categories": {
                        "security": {"accepted_share": 0.78, "rejected_share": 0.08},
                        "quality": {"accepted_share": 0.55, "rejected_share": 0.20},
                        "performance": {"accepted_share": 0.41, "rejected_share": 0.31},
                    },
                    "events_count": 6,
                    "last_updated": NOW.isoformat(),
                },
                decay_params={"tau_days": 90, "lambda": 1.0, "horizon_days": 270},
                arum_weights={"w1_severity": 0.32, "w2_confidence": 0.18},
                learned_weights={
                    "version": "v2-logistic-demo",
                    "samples": 128,
                    "weights": {
                        "w1_severity": 0.34,
                        "w2_confidence": 0.16,
                        "w3_agreement": 0.11,
                    },
                    "promoted": False,
                },
                version=3,
            )
        )
        s.add_all(
            [
                RepositoryMemory(
                    id=uid("console:memory"),
                    repository_id=repo_ids["console"],
                    snapshot={
                        "categories": {
                            "standards": {
                                "accepted_share": 0.61,
                                "rejected_share": 0.17,
                            },
                            "quality": {"accepted_share": 0.48, "rejected_share": 0.22},
                        },
                        "events_count": 3,
                        "last_updated": NOW.isoformat(),
                    },
                    decay_params={"tau_days": 90, "lambda": 1.0, "horizon_days": 270},
                    arum_weights={"w1_severity": 0.30, "w2_confidence": 0.20},
                    learned_weights={"version": "v2-logistic-demo", "samples": 41},
                    version=2,
                ),
                RepositoryMemory(
                    id=uid("pipeline:memory"),
                    repository_id=repo_ids["pipeline"],
                    snapshot={
                        "categories": {
                            "performance": {
                                "accepted_share": 0.58,
                                "rejected_share": 0.19,
                            },
                            "architecture": {
                                "accepted_share": 0.37,
                                "rejected_share": 0.28,
                            },
                        },
                        "events_count": 4,
                        "last_updated": NOW.isoformat(),
                    },
                    decay_params={"tau_days": 90, "lambda": 1.0, "horizon_days": 270},
                    arum_weights={"w1_severity": 0.31, "w2_confidence": 0.19},
                    learned_weights={"version": "v2-logistic-demo", "samples": 63},
                    version=2,
                ),
            ]
        )

        await s.commit()

    await engine.dispose()
    print("Demo dataset seeded.")
    print(
        f"Repositories: {len(REPOS)}  Reviews: {len(prs)}  "
        f"Findings: {len(finding_rows)}"
    )


if __name__ == "__main__":
    asyncio.run(main())
