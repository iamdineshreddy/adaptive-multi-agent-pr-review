"""Real GitHub E2E test - verify token and check repo status."""

import httpx
import json
import os

TOKEN = os.environ["ADAPTIVE_GITHUB_PAT"]  # from .env / environment; never hardcode
HEADERS = {
    "Authorization": f"Bearer {TOKEN}",
    "Accept": "application/vnd.github+json",
    "X-GitHub-Api-Version": "2022-11-28",
}

OWNER = "iamdineshreddy"
REPO = "adaptive-multi-agent-pr-review"


def main():
    # 1. Verify token
    print("=" * 60)
    print("STEP 1: Verify GitHub Token")
    print("=" * 60)
    r = httpx.get("https://api.github.com/user", headers=HEADERS)
    print(f"Status: {r.status_code}")
    if r.status_code != 200:
        print(f"Error: {r.text}")
        return
    user = r.json()
    print(f"User: {user['login']}")
    print(f"Name: {user.get('name', 'N/A')}")

    # 2. Check repo access
    print("\n" + "=" * 60)
    print("STEP 2: Check Repository Access")
    print("=" * 60)
    r = httpx.get(f"https://api.github.com/repos/{OWNER}/{REPO}", headers=HEADERS)
    print(f"Status: {r.status_code}")
    if r.status_code != 200:
        print(f"Error: {r.text}")
        return
    repo = r.json()
    print(f"Repo: {repo['full_name']}")
    print(f"Default branch: {repo['default_branch']}")
    print(f"Private: {repo['private']}")

    # 3. Check existing PRs
    print("\n" + "=" * 60)
    print("STEP 3: Check Existing Pull Requests")
    print("=" * 60)
    r = httpx.get(
        f"https://api.github.com/repos/{OWNER}/{REPO}/pulls",
        headers=HEADERS,
        params={"state": "all", "per_page": 10},
    )
    print(f"Status: {r.status_code}")
    if r.status_code != 200:
        print(f"Error: {r.text}")
        return
    prs = r.json()
    print(f"Total PRs found: {len(prs)}")
    for pr in prs:
        print(f"  #{pr['number']}: {pr['title']} [{pr['state']}]")

    # 4. Check branches
    print("\n" + "=" * 60)
    print("STEP 4: Check Branches")
    print("=" * 60)
    r = httpx.get(
        f"https://api.github.com/repos/{OWNER}/{REPO}/branches",
        headers=HEADERS,
    )
    print(f"Status: {r.status_code}")
    if r.status_code != 200:
        print(f"Error: {r.text}")
        return
    branches = r.json()
    print(f"Total branches: {len(branches)}")
    for b in branches:
        print(f"  {b['name']}")

    # 5. Check rate limit
    print("\n" + "=" * 60)
    print("STEP 5: Check Rate Limit")
    print("=" * 60)
    r = httpx.get("https://api.github.com/rate_limit", headers=HEADERS)
    if r.status_code == 200:
        rate = r.json()["rate"]
        print(f"Limit: {rate['limit']}")
        print(f"Remaining: {rate['remaining']}")
        print(f"Reset: {rate['reset']}")

    print("\n" + "=" * 60)
    print("VERIFICATION COMPLETE")
    print("=" * 60)


if __name__ == "__main__":
    main()
