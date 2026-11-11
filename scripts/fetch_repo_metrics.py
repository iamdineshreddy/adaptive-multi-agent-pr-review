import httpx
import os
import json
from pathlib import Path

token = os.environ["ADAPTIVE_GITHUB_PAT"]
headers = {
    "Authorization": f"Bearer {token}",
    "Accept": "application/vnd.github+json",
    "X-GitHub-Api-Version": "2022-11-28",
}
repo = "iamdineshreddy/localcart-connect"

def get(path):
    r = httpx.get(f"https://api.github.com{path}", headers=headers, timeout=30)
    return r

# Repo info
ri = get(f"/repos/{repo}").json()
print("=== REPO INFO ===")
print("name:", ri["full_name"])
print("description:", ri.get("description"))
print("language:", ri.get("language"))
print("stars:", ri["stargazers_count"], "forks:", ri["forks_count"])
print("open_issues:", ri["open_issues_count"])
print("default_branch:", ri["default_branch"])
print("created:", ri["created_at"], "updated:", ri["updated_at"])
print("size_kb:", ri["size"])

# Languages
lang = get(f"/repos/{repo}/languages").json()
print("\n=== LANGUAGES ===")
print(lang)

# PRs
prs = get(f"/repos/{repo}/pulls?state=all&per_page=100").json()
print("\n=== PULL REQUESTS (total fetched:", len(prs), ") ===")
merged = [p for p in prs if p.get("merged_at")]
closed = [p for p in prs if p["state"] == "closed" and not p.get("merged_at")]
open_prs = [p for p in prs if p["state"] == "open"]
print("open:", len(open_prs), "merged:", len(merged), "closed-unmerged:", len(closed))
for p in prs[:10]:
    print(f"  PR#{p['number']} [{p['state']}] merged={bool(p.get('merged_at'))} +{p.get('additions',0)}/-{p.get('deletions',0)} files={p.get('changed_files',0)} title={p['title'][:60]}")

# Issues
issues = get(f"/repos/{repo}/issues?state=all&per_page=100").json()
real_issues = [i for i in issues if "pull_request" not in i]
print("\n=== ISSUES (total fetched:", len(real_issues), ") ===")
print("open:", sum(1 for i in real_issues if i["state"]=="open"), "closed:", sum(1 for i in real_issues if i["state"]=="closed"))

# Commits
commits = get(f"/repos/{repo}/commits?per_page=100").json()
print("\n=== COMMITS (fetched:", len(commits), ") ===")
print("latest:", commits[0]["commit"]["message"][:80] if commits else "none")
print("latest date:", commits[0]["commit"]["author"]["date"] if commits else "none")

# Contributors
contribs = get(f"/repos/{repo}/contributors?per_page=100").json()
print("\n=== CONTRIBUTORS (", len(contribs), ") ===")
for c in contribs[:10]:
    print(" ", c["login"], c["contributions"])

# Save
out = {
    "repo": repo,
    "info": ri,
    "languages": lang,
    "prs": [{"number": p["number"], "state": p["state"], "merged": bool(p.get("merged_at")), "additions": p.get("additions"), "deletions": p.get("deletions"), "changed_files": p.get("changed_files"), "title": p["title"]} for p in prs],
    "issues_count": len(real_issues),
    "issues_open": sum(1 for i in real_issues if i["state"]=="open"),
    "issues_closed": sum(1 for i in real_issues if i["state"]=="closed"),
    "commits_fetched": len(commits),
    "contributors": [{"login": c["login"], "contributions": c["contributions"]} for c in contribs],
}
Path("experiments/results").mkdir(parents=True, exist_ok=True)
Path("experiments/results/repo_metrics_localcart_connect.json").write_text(json.dumps(out, indent=2), encoding="utf-8")
print("\nSaved to experiments/results/repo_metrics_localcart_connect.json")