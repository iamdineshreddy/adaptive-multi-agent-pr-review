"""Diagnose GitHub API access."""

import os

import httpx

token = os.environ["ADAPTIVE_GITHUB_PAT"]  # from .env / environment; never hardcode
headers = {
    "Authorization": f"Bearer {token}",
    "Accept": "application/vnd.github+json",
    "X-GitHub-Api-Version": "2022-11-28",
}

# Check token
r = httpx.get("https://api.github.com/user", headers=headers)
print(f"Token status: {r.status_code}")
if r.status_code == 200:
    data = r.json()
    print(f"User: {data['login']}")
else:
    print(f"Error: {r.text}")

# Check repo
r = httpx.get(
    "https://api.github.com/repos/iamdineshreddy/adaptive-multi-agent-pr-review",
    headers=headers,
)
print(f"\nRepo status: {r.status_code}")
if r.status_code == 200:
    data = r.json()
    print(f"Repo: {data['full_name']}")
    print(f"Default branch: {data['default_branch']}")
    print(f"Private: {data['private']}")
else:
    print(f"Error: {r.text}")

# Check rate limit
r = httpx.get("https://api.github.com/rate_limit", headers=headers)
print(f"\nRate limit status: {r.status_code}")
if r.status_code == 200:
    data = r.json()
    print(f"Rate limit: {data['resources']['core']}")
