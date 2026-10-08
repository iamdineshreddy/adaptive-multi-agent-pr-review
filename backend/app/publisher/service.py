"""Publication core: POST selected findings back to the PR as a GitHub review.

The PUBLISH step (docs/ARCHITECTURE.md §4.5, component J): after ARUM selection
leaves a review on ``DECIDING`` (each selected representative
``publication_status=scheduled``), the publisher claims the review, builds one
GitHub pull-request review with inline comments, and atomically marks the review
``PUBLISHED`` and its findings ``PUBLISHED`` (carrying their GitHub comment ids).

Guarantees:
- **Exactly-one claimer**: ``claim_review_for_publication`` is atomic, so beats
  overlapping after a worker crash never double-post (docs/QUEUE.md §5).
- **No finding ever lost**: a comment GitHub rejects as off-diff (permanent
  validation error) degrades to the review body instead of being dropped.
- **Transient vs permanent**: transient GitHub errors release the review back to
  ``DECIDING`` for the scan to re-try (Celery also retries the task); permanent
  errors surface as a diagnosed ``FAILED`` review.

The core is broker/DB-free: stores and the GitHub client are injected, keeping
the whole pipeline unit-testable offline.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timedelta
from typing import Any

import structlog

from app.config.settings import Settings, get_settings
from app.github.client import GitHubApiError, GitHubClient
from app.github.schemas import (
    PullRequestReviewResult,
    PullRequestReviewWrite,
    ReviewCommentWrite,
)
from app.orchestrator.persistence import OrchestratorStore, utc_now

logger = structlog.get_logger(__name__)

_REVIEW_BADGE = "Adaptive Multi-Agent PR Review"
_DEGRADED_LABEL = "off-diff (posted in body)"


@dataclass(frozen=True)
class PublishOutcome:
    """What this publication pass did (for logs / task result)."""

    review_id: str
    github_review_id: int | None
    posted: int
    degraded: int
    already_published: bool = False


class PublishError(RuntimeError):
    """Publication failed. ``retryable`` maps onto the Celery retry policy."""

    def __init__(self, message: str, *, retryable: bool) -> None:
        super().__init__(message)
        self.retryable = retryable


def _split_full_name(full_name: str) -> tuple[str, str]:
    owner, _, repo = full_name.partition("/")
    if not owner or not repo:
        raise PublishError(
            f"repository full_name {full_name!r} is not 'owner/repo'",
            retryable=False,
        )
    return owner, repo


def _inline_comment(finding: dict[str, Any]) -> ReviewCommentWrite:
    """One finding rendered as an inline review comment on its changed line."""
    line = finding.get("line_start") or finding.get("line_end")
    severity = str(finding.get("severity", ""))
    title = str(finding.get("title", ""))
    description = str(finding.get("description", ""))
    body = f"**{severity} — {title}**\n\n{description}"
    suggested_fix = finding.get("suggested_fix")
    if suggested_fix:
        body += f"\n\n**Suggested fix:** {suggested_fix}"
    line_int = int(line) if line is not None else None
    return ReviewCommentWrite(
        path=str(finding["file_path"]),
        line=line_int,
        body=body,
    )


def _review_body(summary: str, degraded_findings: list[tuple[str, Any]]) -> str:
    """Markdown summary; off-diff findings are folded in below it."""
    lines = [f"### {_REVIEW_BADGE}", summary]
    for finding_id, finding in degraded_findings:
        lines.append("\n---\n")
        severity = str(finding.get("severity", ""))
        title = str(finding.get("title", ""))
        file_path = str(finding.get("file_path", ""))
        description = str(finding.get("description", ""))
        suggested_fix = finding.get("suggested_fix")
        lines.append(f"**{severity} — {title}**\n\n")
        lines.append(f"`{file_path}`\n\n")
        lines.append(f"{description}")
        if suggested_fix:
            lines.append(f"**Suggested fix:** {suggested_fix}")
        lines.append(f"*{_DEGRADED_LABEL}, finding `{finding_id}`*")
    return "\n".join(lines)


def _map_comment_ids(
    inline: list[tuple[dict[str, Any], ReviewCommentWrite]],
    result: PullRequestReviewResult,
) -> list[tuple[dict[str, Any], int | None]]:
    """GitHub echoes inline comments in request order -> attach ids to findings."""
    comments = list(result.comments)
    return [
        (finding, comments[i].id if i < len(comments) else None)
        for i, (finding, _) in enumerate(inline)
    ]


async def publish_review(
    store: OrchestratorStore,
    client: GitHubClient,
    review_id: object,
    *,
    settings: Settings | None = None,
    now: datetime | None = None,
) -> PublishOutcome:
    """Claim and publish one review's selected findings, idempotently."""
    cfg = settings or get_settings()
    rid = str(review_id)
    timestamp = now if now is not None else utc_now()

    claimed = await store.claim_review_for_publication(
        rid,
        claim_stale_before=timestamp
        - timedelta(seconds=cfg.publisher_claim_stale_seconds),
    )
    if not claimed:
        logger.info(
            "publisher_skip_no_claim",
            review_id=rid,
            reason="already claimed or terminal",
        )
        return PublishOutcome(
            review_id=rid,
            github_review_id=None,
            posted=0,
            degraded=0,
            already_published=True,
        )

    try:
        payload = await store.publication_payload(rid)
        if payload is None:
            raise PublishError(
                f"review {rid} has no publication payload", retryable=False
            )
        findings: list[dict[str, Any]] = payload["findings"]
        if not findings:
            await store.mark_review_published(
                rid, github_review_id=None, published_at=timestamp
            )
            logger.info("publisher_nothing_to_post", review_id=rid)
            return PublishOutcome(rid, None, 0, 0)

        owner, repo = _split_full_name(str(payload["repository_full_name"]))
        number = int(payload["pr_number"])
        commit_id = str(payload["head_sha"])
        capacity = cfg.publisher_max_comments_per_review
        inline: list[tuple[dict[str, Any], ReviewCommentWrite]] = []
        body_findings: list[tuple[str, object]] = []
        for finding in findings[:capacity]:
            comment = _inline_comment(finding)
            if comment.line is not None:
                inline.append((finding, comment))
            else:
                body_findings.append((str(finding["id"]), finding))
        for finding in findings[capacity:]:
            body_findings.append((str(finding["id"]), finding))

        summary = (
            f"Automated review of the ARUM-selected findings from **{_REVIEW_BADGE}**: "
            f"{len(inline) + len(body_findings)} finding(s), "
            f"{len(inline)} inline, {len(body_findings)} in body."
        )
        review_body = _review_body(summary, body_findings)

        try:
            result = await client.create_pull_request_review(
                owner,
                repo,
                number,
                PullRequestReviewWrite(
                    commit_id=commit_id,
                    event="COMMENT",
                    body=review_body,
                    comments=[comment for _, comment in inline],
                ),
            )
        except GitHubApiError as exc:
            if exc.retryable:
                await store.release_review_from_publication(rid)
                raise PublishError(str(exc), retryable=True) from exc
            # Inline positions can be rejected as off-diff: degrade, never lose.
            logger.warning(
                "publisher_degrades_to_body",
                review_id=rid,
                status_code=exc.status_code,
                reason="GitHub rejected inline comment positions",
            )
            result = await _post_body_only(
                client, owner, repo, number, commit_id, findings
            )
            body_findings = [(str(f["id"]), f) for f in findings]
            inline = []

        posted_ids = _map_comment_ids(inline, result)
        updates: list[tuple[str, int | None]] = [
            (str(f1["id"]), comment_id) for f1, comment_id in posted_ids
        ]
        updates += [(finding_id, None) for finding_id, _ in body_findings]
        await store.mark_findings_published(rid, updates)
        await store.mark_review_published(
            rid,
            github_review_id=result.id,
            published_at=timestamp,
        )

        logger.info(
            "publisher_review_posted",
            review_id=rid,
            github_review_id=result.id,
            comments_posted=sum(1 for _ in posted_ids),
            degraded=len(body_findings),
        )
        return PublishOutcome(
            review_id=rid,
            github_review_id=result.id,
            posted=sum(1 for _ in posted_ids),
            degraded=len(body_findings),
        )
    except PublishError:
        raise
    except Exception as exc:  # noqa: BLE001 - mapped to the retry policy
        await store.release_review_from_publication(rid)
        raise PublishError(
            f"unexpected publisher failure: {exc}", retryable=True
        ) from exc


async def _post_body_only(
    client: GitHubClient,
    owner: str,
    repo: str,
    number: int,
    commit_id: str,
    findings: list[dict[str, Any]],
) -> PullRequestReviewResult:
    body = _review_body(
        "Automated review body (inline comments rejected):",
        [(str(f["id"]), f) for f in findings],
    )
    try:
        return await client.create_pull_request_review(
            owner,
            repo,
            number,
            PullRequestReviewWrite(
                commit_id=commit_id,
                event="COMMENT",
                body=body,
                comments=[],
            ),
        )
    except GitHubApiError as exc:
        if exc.retryable:
            raise PublishError(str(exc), retryable=True) from exc
        raise PublishError(
            f"GitHub rejected the publication ({exc.status_code}): {exc}",
            retryable=False,
        ) from exc
