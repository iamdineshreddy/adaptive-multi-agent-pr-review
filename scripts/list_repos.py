import httpx
import os

token = os.environ["ADAPTIVE_GITHUB_PAT"]
headers = {
    "Authorization": f"Bearer {token}",
    "Accept": "application/vnd.github+json",
    "X-GitHub-Api-Version": "2022-11-28",
}
r = httpx.get("https://api.github.com/user/repos?per_page=30&sort=updated", headers=headers)
print("status:", r.status_code)
for repo in r.json():
    print(repo["full_name"], "| stars:", repo["stargazers_count"], "| lang:", repo["language"])
