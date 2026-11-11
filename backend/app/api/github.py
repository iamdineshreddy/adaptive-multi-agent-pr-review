"""GitHub live-data endpoint (read-only).

Exposes the authenticated user's repositories and basic metrics so the
dashboard can display real GitHub activity. Uses the configured PAT; no secrets
are logged.
"""

from __future__ import annotations

from typing import Annotated, Any

import httpx
from fastapi import APIRouter, Depends, HTTPException

from app.config.settings import get_settings
from app.security.auth import get_principal

router = APIRouter(tags=["github"], dependencies=[Depends(get_principal)])


@router.get("/github/repos", response_model=list[dict[str, Any]])
async def list_github_repos() -> list[dict[str, Any]]:
    settings = get_settings()
    token = settings.github_pat
    if not token:
        raise HTTPException(status_code=503, detail="GitHub PAT not configured")
    base_url = settings.github_webhook_url.rstrip("/").replace("api.github.com", "api.github.com")
    # Use api.github.com for user repos
    url = "https://api.github.com/user/repos"
    params = {"per_page": "100", "sort": "updated", "affiliation": "owner,collaborator"}
    async with httpx.AsyncClient(timeout=30.0) as client:
        response = await client.get(
            url,
            headers={
                "Authorization": f"Bearer {token}",
                "Accept": "application/vnd.github+json",
                "X-GitHub-Api-Version": "2022-11-28",
            },
            params=params,
        )
    if response.status_code != 200:
        raise HTTPException(status_code=502, detail="GitHub API error")
    data = response.json()
    return [
        {
            "id": r["id"],
            "full_name": r["full_name"],
            "name": r["name"],
            "description": r.get("description"),
            "language": r.get("language"),
            "stars": r.get("stargazers_count", 0),
            "forks": r.get("forks_count", 0),
            "open_issues": r.get("open_issues_count", 0),
            "default_branch": r.get("default_branch"),
            "updated_at": r.get("updated_at"),
            "html_url": r.get("html_url"),
            "private": r.get("private", False),
        }
        for r in data
    ]
