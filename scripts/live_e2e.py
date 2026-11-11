"""Live end-to-end driver: signed webhook -> queue -> agents -> ARUM -> publisher.

This is an operator tool, not a unit test. It sends a real, signature-valid
GitHub ``pull_request`` delivery to a running stack, then polls the dashboard
API until the review reaches a terminal status, printing the agent/ARUM/gate/
publication trail as it goes.

Safety: publication writes review comments to the target PR, so run it only
against the dedicated test repository. Nothing here fabricates results: every
printed value comes back from the running API / database.

Usage:
    python scripts/live_e2e.py --repo iamdineshreddy/adaptive-multi-agent-pr-review --pr 3
    python scripts/live_e2e.py --dry-run     # stop before dispatching the webhook
"""

from __future__ import annotations

import argparse
import hashlib
import hmac
import json
import os
import secrets
import sys
import time
from datetime import datetime, timezone
from typing import Any

import httpx
from dotenv import load_dotenv

GITHUB_API = "https://api.github.com"
TERMINAL = {"PUBLISHED", "FAILED", "COMPLETED", "SKIPPED"}


def _github_headers(token: str) -> dict[str, str]:
    return {
        "Authorization": f"Bearer {token}",
        "Accept": "application/vnd.github+json",
        "X-GitHub-Api-Version": "2022-11-28",
    }


def build_payload(repo: str, pr_number: int, token: str, action: str) -> dict[str, Any]:
    """Fetch the real PR and shape the subset of GitHub's event we consume."""
    with httpx.Client(
        base_url=GITHUB_API, headers=_github_headers(token), timeout=30
    ) as gh:
        pr = gh.get(f"/repos/{repo}/pulls/{pr_number}")
        pr.raise_for_status()
        data = pr.json()
    return {
        "action": action,
        "number": data["number"],
        "pull_request": {
            "id": data["id"],
            "number": data["number"],
            "title": data["title"],
            "state": data["state"],
            "draft": data.get("draft", False),
            "user": {"login": (data.get("user") or {}).get("login")},
            "base": {"ref": data["base"]["ref"], "sha": data["base"]["sha"]},
            "head": {"ref": data["head"]["ref"], "sha": data["head"]["sha"]},
            "changed_files": data.get("changed_files", 0),
            "additions": data.get("additions", 0),
            "deletions": data.get("deletions", 0),
        },
        "repository": {
            "id": data["base"]["repo"]["id"],
            "full_name": data["base"]["repo"]["full_name"],
            "default_branch": data["base"]["repo"]["default_branch"],
            "language": data["base"]["repo"].get("language"),
            "owner": {"login": data["base"]["repo"]["owner"]["login"]},
            "private": data["base"]["repo"].get("private", False),
        },
        "sender": {"login": (data.get("user") or {}).get("login")},
    }


def sign(secret: str, body: bytes) -> str:
    digest = hmac.new(secret.encode(), body, hashlib.sha256).hexdigest()
    return f"sha256={digest}"


def _get(client: httpx.Client, path: str, **params: Any) -> Any:
    response = client.get(path, params=params or None)
    response.raise_for_status()
    return response.json()


def poll_review(client: httpx.Client, review_id: str, timeout: float) -> dict[str, Any]:
    """Poll review detail, printing status transitions as they happen."""
    started = time.monotonic()
    last: str | None = None
    detail: dict[str, Any] = {}
    while time.monotonic() - started < timeout:
        detail = _get(client, f"/api/v1/reviews/{review_id}")
        status = str(detail.get("status"))
        if status != last:
            findings = detail.get("findings") or []
            print(
                f"  [{datetime.now(timezone.utc).strftime('%H:%M:%S')}] "
                f"status={status} findings={len(findings)} "
                f"iterations={len(detail.get('iterations') or [])}"
            )
            last = status
        if status in TERMINAL:
            return detail
        time.sleep(3)
    print(f"  timed out after {timeout:.0f}s (last status={last})", file=sys.stderr)
    return detail


def describe(detail: dict[str, Any]) -> None:
    findings = detail.get("findings") or []
    published = [
        f
        for f in findings
        if str(f.get("publication_status", "")).upper() == "PUBLISHED"
    ]
    print("\n--- review summary ---")
    for key in (
        "id",
        "status",
        "priority_score",
        "selected_count",
        "published_at",
        "github_review_id",
        "failure_reason",
    ):
        if key in detail:
            print(f"{key:>16}: {detail[key]}")
    print(f"{'findings':>16}: {len(findings)} total, {len(published)} published")
    by_agent: dict[str, int] = {}
    for finding in findings:
        by_agent[str(finding.get("agent_key"))] = (
            by_agent.get(str(finding.get("agent_key")), 0) + 1
        )
    for agent, count in sorted(by_agent.items()):
        print(f"{'  agent':>16}: {agent or '?'} -> {count}")
    for finding in findings[:25]:
        print(
            f"  - [{finding.get('severity')}] {finding.get('category')} "
            f"{finding.get('file_path')}:{finding.get('line_start')} "
            f"({finding.get('publication_status')}) {finding.get('title')}"
        )


def main() -> int:
    load_dotenv()
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--repo", default="iamdineshreddy/adaptive-multi-agent-pr-review"
    )
    parser.add_argument("--pr", type=int, default=3)
    parser.add_argument("--action", default="synchronize")
    parser.add_argument(
        "--api", default=os.getenv("ADAPTIVE_E2E_API", "http://127.0.0.1:8000")
    )
    parser.add_argument("--timeout", type=float, default=240.0)
    parser.add_argument(
        "--dry-run", action="store_true", help="build the delivery but do not send it"
    )
    args = parser.parse_args()

    gh_token = os.getenv("ADAPTIVE_GITHUB_PAT", "")
    webhook_secret = os.getenv("ADAPTIVE_GITHUB_WEBHOOK_SECRET", "")
    api_token = os.getenv("ADAPTIVE_API_TOKEN", "")
    if not gh_token:
        print("ADAPTIVE_GITHUB_PAT is required", file=sys.stderr)
        return 2
    if not webhook_secret:
        print("ADAPTIVE_GITHUB_WEBHOOK_SECRET is required", file=sys.stderr)
        return 2
    if not api_token:
        print(
            "ADAPTIVE_API_TOKEN is required (dashboard bearer token)", file=sys.stderr
        )
        return 2

    print(f"Target repo: {args.repo}  PR #{args.pr}  action={args.action}")
    payload = build_payload(args.repo, args.pr, gh_token, args.action)
    body = json.dumps(payload, separators=(",", ":")).encode()
    delivery_id = f"e2e-{secrets.token_hex(8)}"
    headers = {
        "Content-Type": "application/json",
        "X-GitHub-Event": "pull_request",
        "X-GitHub-Delivery": delivery_id,
        "X-Hub-Signature-256": sign(webhook_secret, body),
    }
    print(f"Delivery: {delivery_id}  body={len(body)} bytes")
    if args.dry_run:
        print("Dry run: webhook not sent.")
        return 0

    auth = {"Authorization": f"Bearer {api_token}"}
    with httpx.Client(base_url=args.api, headers=auth, timeout=30) as client:
        response = client.post("/api/v1/webhooks/github", content=body, headers=headers)
        print(f"Webhook response: {response.status_code} {response.text}")
        if response.status_code != 202:
            return 1
        review_id = response.json()["review_id"]
        print(f"Review id: {review_id}\n")
        detail = poll_review(client, review_id, args.timeout)
        describe(detail)
        return 0 if detail.get("status") in {"PUBLISHED", "COMPLETED"} else 1


if __name__ == "__main__":
    raise SystemExit(main())
