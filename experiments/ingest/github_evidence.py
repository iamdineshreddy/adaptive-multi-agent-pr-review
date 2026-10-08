"""Fetch the GitHub evidence an annotator needs for the annotation pass.

The archive carries no file path and no review thread (datasets/
DATASET_MAPPING_PROPOSAL.md V7), and the approved protocol makes ``file_path``
an annotation field supplied from the PR view. This module is that PR view,
programmatically: for every PR in ``annotation_required.jsonl`` it retrieves the
PR's review comments, reviews and discussion comments, caches them under
gitignored ``datasets/annotation/github/`` (raw GitHub data — never committed),
and writes ``path_evidence.jsonl``:

  - ``file_path`` — recovered from the review comment the archive row came from
    (``match``: ``exact`` | ``fuzzy`` | ``none``; ``none`` stays null so the
    record is excluded rather than guessed),
  - the comment's reply thread, the PR's review states and the discussion —
    the only admissible source for an ``explicit_outcome`` annotation
    (``datasets/README.md``: "never from the change alone").

The PAT is read from ``.env`` and never printed or written anywhere. Caching
makes the run resumable; re-running only fetches what is missing.

Usage:
    python experiments/ingest/github_evidence.py \
        --required datasets/annotation/annotation_required.jsonl
"""

from __future__ import annotations

import argparse
import difflib
import json
import os
import re
import sys
import time
import urllib.error
import urllib.request
from pathlib import Path
from typing import Any

GITHUB_API = "https://api.github.com"
_BODY_CAP = 4000  # characters kept per cached body
_FUZZY_FLOOR = 0.92
_SLEEP = 0.15


def _pat() -> str:
    """Read ADAPTIVE_GITHUB_PAT from .env (repo root), never logging it."""
    for name in (".env", os.path.join("..", ".env")):
        path = Path(name)
        if not path.is_file():
            continue
        for line in path.read_text(encoding="utf-8").splitlines():
            if line.startswith("ADAPTIVE_GITHUB_PAT="):
                token = line.split("=", 1)[1].strip().strip('"').strip("'")
                if token:
                    return token
    raise SystemExit(
        "ADAPTIVE_GITHUB_PAT not found in .env — evidence fetch needs it "
        "(the annotation aid's PR links need the same access)"
    )


class _Client:
    def __init__(self, token: str) -> None:
        self._token = token
        self.remaining: int | None = None

    def get(self, url: str) -> Any | None:
        """GET a GitHub API URL; returns parsed JSON or None on 404."""
        req = urllib.request.Request(
            url,
            headers={
                "Authorization": f"Bearer {self._token}",
                "Accept": "application/vnd.github+json",
                "X-GitHub-Api-Version": "2022-11-28",
                "User-Agent": "adaptive-review-corpus-annotation",
            },
        )
        for attempt in range(3):
            try:
                with urllib.request.urlopen(req, timeout=30) as resp:
                    self.remaining = resp.headers.get("x-ratelimit-remaining")
                    payload = json.loads(resp.read().decode("utf-8"))
                    time.sleep(_SLEEP)
                    return payload
            except urllib.error.HTTPError as exc:
                if exc.code == 404:
                    return None
                if exc.code in (403, 429) and attempt < 2:
                    time.sleep(2.0 * (attempt + 1))
                    continue
                raise
        return None

    def paged(self, url: str, cap: int = 10) -> list[Any]:
        out: list[Any] = []
        for page in range(1, cap + 1):
            chunk = self.get(f"{url}{'&' if '?' in url else '?'}per_page=100&page={page}")
            if not isinstance(chunk, list) or not chunk:
                break
            out.extend(chunk)
            if len(chunk) < 100:
                break
        return out


def _norm(text: str) -> str:
    return re.sub(r"\s+", " ", (text or "")).strip().lower()


def _cap(text: Any) -> str:
    if not isinstance(text, str):
        return ""
    return text[:_BODY_CAP]


def _match(archive_comment: str, comments: list[dict[str, Any]]) -> dict[str, Any] | None:
    """Find the GitHub review comment the archive row came from."""
    target = _norm(archive_comment)
    for candidate in comments:
        if _norm(candidate.get("body")) == target:
            return {"comment": candidate, "match": "exact"}
    best, best_ratio = None, 0.0
    for candidate in comments:
        ratio = difflib.SequenceMatcher(
            None, target, _norm(candidate.get("body"))
        ).ratio()
        if ratio > best_ratio:
            best, best_ratio = candidate, ratio
    if best is not None and best_ratio >= _FUZZY_FLOOR:
        return {"comment": best, "match": "fuzzy"}
    return None


def _cache_path(cache_dir: Path, repo: str, number: str) -> Path:
    safe = f"{repo.replace('/', '__')}__{number}.json"
    return cache_dir / safe


def _fetch_pr(client: _Client, repo: str, number: str) -> dict[str, Any]:
    base = f"{GITHUB_API}/repos/{repo}"
    comments = client.paged(f"{base}/pulls/{number}/comments")
    reviews = client.paged(f"{base}/pulls/{number}/reviews")
    issue_comments = client.paged(f"{base}/issues/{number}/comments")
    return {
        "repo": repo,
        "pr": number,
        "review_comments": [
            {
                "id": c.get("id"),
                "path": c.get("path"),
                "line": c.get("line") or c.get("original_line"),
                "side": c.get("side"),
                "author": (c.get("user") or {}).get("login"),
                "created_at": c.get("created_at"),
                "in_reply_to_id": c.get("in_reply_to_id"),
                "body": _cap(c.get("body")),
            }
            for c in comments
        ],
        "reviews": [
            {
                "author": (r.get("user") or {}).get("login"),
                "state": r.get("state"),
                "submitted_at": r.get("submitted_at"),
                "body": _cap(r.get("body")),
            }
            for r in reviews
        ],
        "issue_comments": [
            {
                "author": (c.get("user") or {}).get("login"),
                "created_at": c.get("created_at"),
                "body": _cap(c.get("body")),
            }
            for c in issue_comments
        ],
    }


def _load_cache(path: Path) -> dict[str, Any] | None:
    if not path.is_file():
        return None
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (json.JSONDecodeError, OSError):
        return None


def _evidence_row(
    row: dict[str, Any], pr_data: dict[str, Any] | None, fetch: str
) -> dict[str, Any]:
    """One path_evidence row: path + thread + reviews for one archive comment."""
    out: dict[str, Any] = {
        "review_id": row["review_id"],
        "pr_review_id": row["pr_review_id"],
        "pr_url": row["pr_url"],
        "fetch": fetch,
        "match": "none",
        "file_path": None,
        "github_comment_id": None,
        "github_line": None,
        "thread": [],
        "reviews": [],
        "issue_comments": [],
    }
    if pr_data is None:
        return out

    comments = pr_data["review_comments"]
    hit = _match(row["comment"], comments)
    if hit is not None:
        target = hit["comment"]
        thread = [
            c
            for c in comments
            if c["id"] == target["id"] or c.get("in_reply_to_id") == target["id"]
        ]
        out.update(
            match=hit["match"],
            file_path=target.get("path"),
            github_comment_id=target.get("id"),
            github_line=target.get("line"),
            thread=thread,
        )
    out["reviews"] = pr_data["reviews"]
    out["issue_comments"] = pr_data["issue_comments"]
    return out


def _main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--required",
        type=Path,
        default=Path("datasets/annotation/annotation_required.jsonl"),
        help="aid output listing the comments to annotate",
    )
    parser.add_argument(
        "--cache-dir",
        type=Path,
        default=Path("datasets/annotation/github"),
        help="raw GitHub response cache (gitignored; default: datasets/annotation/github)",
    )
    parser.add_argument(
        "--out",
        type=Path,
        default=Path("datasets/annotation/path_evidence.jsonl"),
        help="evidence output (default: datasets/annotation/path_evidence.jsonl)",
    )
    parser.add_argument(
        "--refresh",
        action="store_true",
        help="ignore the cache and refetch everything",
    )
    args = parser.parse_args(argv)

    if not args.required.is_file():
        raise SystemExit(f"not found: {args.required} (run annotation_aid.py first)")

    rows = [
        json.loads(line)
        for line in args.required.read_text(encoding="utf-8").splitlines()
        if line.strip()
    ]
    # group by PR so each PR is fetched exactly once
    prs: dict[str, dict[str, Any]] = {}
    for row in rows:
        m = re.match(r"https://github.com/([^/]+)/([^/]+)/pull/(\d+)$", row["pr_url"])
        if not m:
            raise SystemExit(f"unexpected pr_url: {row['pr_url']}")
        prs.setdefault(
            row["pr_review_id"],
            {"repo": f"{m.group(1)}/{m.group(2)}", "pr": m.group(3)},
        )

    client = _Client(_pat())
    args.cache_dir.mkdir(parents=True, exist_ok=True)

    fetched = cached = 0
    for index, (pr_id, ident) in enumerate(sorted(prs.items()), start=1):
        cache = _cache_path(args.cache_dir, ident["repo"], ident["pr"])
        data = None if args.refresh else _load_cache(cache)
        if data is not None:
            cached += 1
        else:
            try:
                data = _fetch_pr(client, ident["repo"], ident["pr"])
            except Exception as exc:  # noqa: BLE001 - recorded, not swallowed
                data = None
                print(f"  ERROR {ident['repo']}#{ident['pr']}: {exc}")
            if data is not None:
                cache.write_text(
                    json.dumps(data, ensure_ascii=False), encoding="utf-8"
                )
                fetched += 1
        if index % 50 == 0:
            print(
                f"  {index}/{len(prs)} PRs "
                f"(cached {cached}, fetched {fetched}, "
                f"rate remaining {client.remaining})"
            )

    evidence = []
    for row in rows:
        m = re.match(r"https://github.com/([^/]+)/([^/]+)/pull/(\d+)$", row["pr_url"])
        cache = _cache_path(args.cache_dir, f"{m.group(1)}/{m.group(2)}", m.group(3))
        data = _load_cache(cache)
        evidence.append(
            _evidence_row(row, data, "ok" if data is not None else "not_fetched")
        )

    args.out.parent.mkdir(parents=True, exist_ok=True)
    with args.out.open("w", encoding="utf-8", newline="\n") as fh:
        for item in evidence:
            fh.write(json.dumps(item, ensure_ascii=False) + "\n")

    exact = sum(1 for e in evidence if e["match"] == "exact")
    fuzzy = sum(1 for e in evidence if e["match"] == "fuzzy")
    none = sum(1 for e in evidence if e["match"] == "none")
    paths = sum(1 for e in evidence if e["file_path"])
    print(
        f"PRs: {len(prs)} (cached {cached}, fetched {fetched}); "
        f"rows: {len(evidence)}"
    )
    print(f"path recovered: {paths} (exact {exact}, fuzzy {fuzzy}, none {none})")
    print(f"wrote {args.out}")
    return 0


if __name__ == "__main__":
    raise SystemExit(_main())
