"""Unit tests for the GitHub REST client (FR-1.3)."""

from __future__ import annotations

import uuid

import httpx
import pytest

from app.agents.contract import AgentScope
from app.github.client import GitHubApiError, GitHubClient
from app.github.schemas import PullRequestDetail
from app.github.scope import build_scope_from_github

_PR_ID = 12
PR_JSON = {
    "number": 12,
    "title": "Add auth",
    "state": "open",
    "body": "Implement login.",
    "base": {"ref": "main"},
    "head": {"ref": "feat/auth", "sha": "abc123"},
    "changed_files": 2,
    "additions": 40,
    "deletions": 10,
    "merged": False,
    "draft": False,
    "user": {"login": "dev1"},
}

FILES_JSON = [
    {
        "filename": "app/auth.py",
        "status": "modified",
        "additions": 5,
        "deletions": 2,
        "changes": 7,
        "patch": "@@ -1,3 +1,4 @@\n- old\n+ new\n",
        "raw_url": "https://example/raw",
    },
    {
        "filename": "app/secrets.py",
        "status": "added",
        "additions": 3,
        "deletions": 0,
        "changes": 3,
        "patch": "@@ -0,0 +1,3 @@\n+TOKEN = os.environ['T']\n",
    },
]

COMMITS_JSON = [
    {"sha": "abc", "commit": {"message": "wip"}, "author": {"login": "dev1"}},
    {"sha": "def", "commit": {"message": "fix", "author": {"login": "dev2"}}},
]


def _client(handler: object) -> GitHubClient:
    return GitHubClient(
        base_url="https://api.github.com",
        token="ghp_secret",
        transport=httpx.MockTransport(handler),  # type: ignore[arg-type]
    )


class TestGitHubClient:
    async def test_get_pull_request(self) -> None:
        def handler(request: httpx.Request) -> httpx.Response:
            assert request.url.path == "/repos/org/repo/pulls/12"
            assert request.headers["Authorization"] == "Bearer ghp_secret"
            return httpx.Response(200, json=PR_JSON)

        client = _client(handler)
        pr = await client.get_pull_request("org", "repo", 12)
        assert pr.number == 12
        assert pr.head_sha == "abc123"
        assert pr.base_ref == "main"
        assert pr.author_login == "dev1"
        assert pr.changed_files == 2

    async def test_get_pull_request_files(self) -> None:
        def handler(request: httpx.Request) -> httpx.Response:
            assert request.url.path == "/repos/org/repo/pulls/12/files"
            assert request.url.params["per_page"] == "300"
            return httpx.Response(200, json=FILES_JSON)

        client = _client(handler)
        files = await client.get_pull_request_files("org", "repo", 12)
        assert [f.filename for f in files] == ["app/auth.py", "app/secrets.py"]
        assert files[0].status == "modified"
        assert files[1].patch.startswith("@@")

    async def test_get_pull_request_commits(self) -> None:
        def handler(request: httpx.Request) -> httpx.Response:
            assert request.url.path == "/repos/org/repo/pulls/12/commits"
            return httpx.Response(200, json=COMMITS_JSON)

        client = _client(handler)
        commits = await client.get_pull_request_commits("org", "repo", 12)
        assert len(commits) == 2
        assert commits[0].sha == "abc"
        assert commits[1].author_login == "dev2"

    @pytest.mark.parametrize(
        "status,retryable",
        [
            (401, False),
            (403, False),
            (404, False),
            (422, False),
            (500, True),
            (429, True),
        ],
    )
    async def test_error_classification(self, status: int, retryable: bool) -> None:
        def handler(request: httpx.Request) -> httpx.Response:
            return httpx.Response(status, json={"message": "nope"})

        client = _client(handler)
        with pytest.raises(GitHubApiError) as exc:
            await client.get_pull_request("org", "repo", 12)
        assert exc.value.status_code == status
        assert exc.value.retryable is retryable

    async def test_timeout_is_retryable(self) -> None:
        def handler(request: httpx.Request) -> httpx.Response:
            raise httpx.TimeoutException("slow", request=request)

        client = _client(handler)
        with pytest.raises(GitHubApiError) as exc:
            await client.get_pull_request("org", "repo", 12)
        assert exc.value.retryable is True

    def test_missing_token_rejected(self) -> None:
        with pytest.raises(ValueError):
            GitHubClient(base_url="https://api.github.com", token="")


class TestBuildScope:
    def test_scope_from_github_data(self) -> None:
        pr = PullRequestDetail(
            number=PR_JSON["number"],
            title=PR_JSON["title"],
            state=PR_JSON["state"],
            body=PR_JSON["body"],
            base_ref=PR_JSON["base"]["ref"],
            head_ref=PR_JSON["head"]["ref"],
            head_sha=PR_JSON["head"]["sha"],
            changed_files=PR_JSON["changed_files"],
            additions=PR_JSON["additions"],
            deletions=PR_JSON["deletions"],
            author_login=PR_JSON["user"]["login"],
        )
        from app.github.schemas import ChangedFile

        files = [ChangedFile.model_validate(item) for item in FILES_JSON]
        scope = build_scope_from_github(
            review_id=uuid.uuid4(),
            repository_id=uuid.uuid4(),
            pr=pr,
            files=files,
            repo_standards=["Use type hints"],
        )
        assert isinstance(scope, AgentScope)
        assert len(scope.changed_files) == 2
        assert scope.pr_title == "Add auth"
        assert "app/secrets.py" in scope.changed_paths
        assert "Use type hints" in scope.repo_standards
