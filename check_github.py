"""Check GitHub token and list available repositories."""
import os
import httpx
from dotenv import load_dotenv

load_dotenv()
token = os.getenv('ADAPTIVE_GITHUB_PAT', '')
print(f'Token length: {len(token)}')
if len(token) > 8:
    print(f'Token prefix: {token[:8]}...')

headers = {
    'Authorization': f'Bearer {token}',
    'Accept': 'application/vnd.github+json',
    'X-GitHub-Api-Version': '2022-11-28',
}

# Verify token
r = httpx.get('https://api.github.com/user', headers=headers)
print(f'Auth status: {r.status_code}')
if r.status_code == 200:
    data = r.json()
    print(f'User: {data["login"]}')
    print(f'Name: {data.get("name", "N/A")}')

    # List repos
    r2 = httpx.get(
        'https://api.github.com/user/repos?per_page=20&sort=updated',
        headers=headers,
    )
    if r2.status_code == 200:
        repos = r2.json()
        print(f'\nRepositories ({len(repos)}):')
        for repo in repos:
            print(f'  - {repo["full_name"]} '
                  f'(lang={repo.get("language", "N/A")}, '
                  f'private={repo["private"]}, '
                  f'default_branch={repo["default_branch"]})')
    else:
        print(f'Failed to list repos: {r2.text[:200]}')
else:
    print(f'Auth failed: {r.text[:200]}')
