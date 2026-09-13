"""GitHub REST client (FR-1.3).

Fetches PR metadata, changed files, diffs, and commits so the ingestion worker can
feed diff slices to agents (docs/ARCHITECTURE.md §4.4, ROADMAP Phase 3 scope note).
Auth uses the configured PAT/installation token; secrets never enter logs.

Error model: transient conditions (timeouts, 429, 5xx) raise ``GitHubApiError``
with ``retryable=True``; auth/not-found/validation failures are permanent. The
client is async and transport-injectable for offline tests.
"""

from __future__ import annotations

from typing import Any

import httpx

from app.github.schemas import ChangedFile, CommitInfo, PullRequestDetail

_RETRYABLE_STATUS_CODES = {408, 425, 429}
_MAX_FILES_PER_PAGE = 300
_MAX_COMMITS_PER_PAGE = 100


class GitHubApiError(Exception):
    """A GitHub API call failed; ``retryable`` drives the task backoff policy."""

    def __init__(self, status_code: int, message: str, *, retryable: bool) -> None:
        super().__init__(f"GitHub API {status_code}: {message}")
        self.status_code = status_code
        self.retryable = retryable


class GitHubClient:
    """Async client for the pull-request endpoints the review pipeline needs."""

    def __init__(
        self,
        *,
        base_url: str,
        token: str,
        timeout_seconds: float = 30.0,
        transport: httpx.AsyncBaseTransport | None = None,
    ) -> None:
        if not token:
            raise ValueError("GitHubClient requires a token")
        self.base_url = base_url
        self._client = httpx.AsyncClient(
            base_url=str(base_url).rstrip("/"),
            headers={
                "Authorization": f"Bearer {token}",
                "Accept": "application/vnd.github+json",
                "X-GitHub-Api-Version": "2022-11-28",
            },
            timeout=timeout_seconds,
            transport=transport,
        )

    async def get_pull_request(
        self, owner: str, repo: str, number: int
    ) -> PullRequestDetail:
        data = await self._get(f"/repos/{owner}/{repo}/pulls/{number}")
        user = data.get("user") or {}
        return PullRequestDetail(
            number=data["number"],
            title=data["title"],
            state=data.get("state", ""),
            body=data.get("body"),
            base_ref=data["base"]["ref"],
            head_ref=data["head"]["ref"],
            head_sha=data["head"]["sha"],
            changed_files=int(data.get("changed_files", 0)),
            additions=int(data.get("additions", 0)),
            deletions=int(data.get("deletions", 0)),
            merged=data.get("merged"),
            draft=data.get("draft"),
            author_login=user.get("login"),
        )

    async def get_pull_request_files(
        self, owner: str, repo: str, number: int
    ) -> list[ChangedFile]:
        data = await self._get(
            f"/repos/{owner}/{repo}/pulls/{number}/files",
            params={"per_page": _MAX_FILES_PER_PAGE},
        )
        return [ChangedFile.model_validate(item) for item in data]

    async def get_pull_request_commits(
        self, owner: str, repo: str, number: int
    ) -> list[CommitInfo]:
        data = await self._get(
            f"/repos/{owner}/{repo}/pulls/{number}/commits",
            params={"per_page": _MAX_COMMITS_PER_PAGE},
        )
        commits = []
        for item in data:
            author = item.get("author") or item.get("commit", {}).get("author") or {}
            commits.append(
                CommitInfo(
                    sha=item["sha"],
                    message=(item.get("commit") or {}).get("message", ""),
                    author_login=author.get("login"),
                )
            )
        return commits

    async def aclose(self) -> None:
        await self._client.aclose()

    async def _get(self, path: str, *, params: dict[str, Any] | None = None) -> Any:
        try:
            response = await self._client.get(path, params=params)
        except httpx.TimeoutException as exc:
            raise GitHubApiError(0, "request timed out", retryable=True) from exc
        except httpx.TransportError as exc:
            raise GitHubApiError(0, f"transport error: {exc}", retryable=True) from exc
        if response.status_code < 400:
            return response.json()
        raise GitHubApiError(
            response.status_code,
            _error_message(response),
            retryable=_is_retryable(response.status_code),
        )


def _is_retryable(status_code: int) -> bool:
    return status_code in _RETRYABLE_STATUS_CODES or status_code >= 500


def _error_message(response: httpx.Response) -> str:
    try:
        payload = response.json()
        if isinstance(payload, dict):
            return str(payload.get("message") or payload)
        return str(payload)
    except ValueError:
        return response.text[:300]
