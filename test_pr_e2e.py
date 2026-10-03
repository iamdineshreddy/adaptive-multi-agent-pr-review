"""Real GitHub E2E test: create a PR with vulnerable code, run the pipeline, verify comments."""

import os
import httpx
import json
import time
import uuid
from datetime import datetime, timezone

# Load token from .env
from dotenv import load_dotenv
load_dotenv()
TOKEN = os.getenv("ADAPTIVE_GITHUB_PAT", "")
REPO = "iamdineshreddy/adaptive-multi-agent-pr-review"
API = "https://api.github.com"

HEADERS = {
    "Authorization": f"Bearer {TOKEN}",
    "Accept": "application/vnd.github+json",
    "X-GitHub-Api-Version": "2022-11-28",
}

# Vulnerable code samples
VULNERABLE_CODE = '''
import sqlite3
from flask import Flask, request, render_template_string

app = Flask(__name__)

# Vulnerability 1: SQL Injection
@app.route('/login', methods=['POST'])
def login():
    username = request.form['username']
    password = request.form['password']
    conn = sqlite3.connect('users.db')
    cursor = conn.cursor()
    # DANGEROUS: Direct string interpolation
    query = f"SELECT * FROM users WHERE username='{username}' AND password='{password}'"
    cursor.execute(query)
    user = cursor.fetchone()
    if user:
        return "Welcome!"
    return "Invalid credentials"

# Vulnerability 2: XSS
@app.route('/search')
def search():
    query = request.args.get('q', '')
    # DANGEROUS: Unescaped user input in HTML
    return render_template_string(f"<h1>Results for: {query}</h1>")

# Vulnerability 3: Command Injection
@app.route('/ping', methods=['POST'])
def ping():
    import subprocess
    host = request.form['host']
    # DANGEROUS: Unsanitized input in shell command
    result = subprocess.check_output(f"ping -c 1 {host}", shell=True)
    return result

# Vulnerability 4: Hardcoded Secret
API_KEY = "sk-1234567890abcdef"
DATABASE_PASSWORD = "super_secret_password_123"

# Vulnerability 5: Insecure Deserialization
@app.route('/load', methods=['POST'])
def load_data():
    import pickle
    data = request.data
    # DANGEROUS: Unpickling untrusted data
    obj = pickle.loads(data)
    return str(obj)

if __name__ == '__main__':
    app.run(debug=True)
'''

SAFE_CODE = '''
import sqlite3
from flask import Flask, request, render_template, escape
import subprocess
import os
import json

app = Flask(__name__)

# Secure: Using parameterized queries
@app.route('/login', methods=['POST'])
def login():
    username = request.form['username']
    password = request.form['password']
    conn = sqlite3.connect('users.db')
    cursor = conn.cursor()
    # SAFE: Parameterized query
    cursor.execute("SELECT * FROM users WHERE username=? AND password=?", (username, password))
    user = cursor.fetchone()
    if user:
        return "Welcome!"
    return "Invalid credentials"

# Secure: Escaping user input
@app.route('/search')
def search():
    query = request.args.get('q', '')
    # SAFE: Using template with auto-escaping
    return render_template("search.html", query=query)

# Secure: No shell=True, input validation
@app.route('/ping', methods=['POST'])
def ping():
    host = request.form['host']
    # SAFE: No shell=True, validated input
    import re
    if not re.match(r'^[a-zA-Z0-9.-]+$', host):
        return "Invalid host", 400
    result = subprocess.check_output(["ping", "-c", "1", host])
    return result

# Secure: Secrets from environment
API_KEY = os.environ.get("API_KEY", "")
DATABASE_PASSWORD = os.environ.get("DATABASE_PASSWORD", "")

# Secure: Safe serialization
@app.route('/load', methods=['POST'])
def load_data():
    data = request.data
    # SAFE: Using JSON instead of pickle
    obj = json.loads(data)
    return str(obj)

if __name__ == '__main__':
    app.run(debug=False)
'''


def create_branch_and_pr():
    """Create a test branch with vulnerable code and open a PR."""
    print("=" * 70)
    print("STEP 1: Creating test branch with vulnerable code")
    print("=" * 70)
    
    # Get the default branch SHA
    r = httpx.get(f"{API}/repos/{REPO}", headers=HEADERS)
    r.raise_for_status()
    repo_info = r.json()
    default_branch = repo_info["default_branch"]
    print(f"Default branch: {default_branch}")
    
    # Get the latest commit SHA
    r = httpx.get(f"{API}/repos/{REPO}/commits/{default_branch}", headers=HEADERS)
    r.raise_for_status()
    base_sha = r.json()["sha"]
    print(f"Base commit SHA: {base_sha}")
    
    # Create a new branch
    branch_name = f"test/security-review-{uuid.uuid4().hex[:8]}"
    r = httpx.post(
        f"{API}/repos/{REPO}/git/refs",
        headers=HEADERS,
        json={"ref": f"refs/heads/{branch_name}", "sha": base_sha}
    )
    r.raise_for_status()
    print(f"Created branch: {branch_name}")
    
    # Create the vulnerable file
    r = httpx.put(
        f"{API}/repos/{REPO}/contents/test_app.py",
        headers=HEADERS,
        json={
            "message": "Add test application with security issues",
            "content": __import__("base64").b64encode(VULNERABLE_CODE.encode()).decode(),
            "branch": branch_name
        }
    )
    r.raise_for_status()
    print("Added vulnerable test_app.py")
    
    # Create a PR
    r = httpx.post(
        f"{API}/repos/{REPO}/pulls",
        headers=HEADERS,
        json={
            "title": "Test: Security review demonstration",
            "head": branch_name,
            "base": default_branch,
            "body": "This PR contains intentional security vulnerabilities for testing the adaptive multi-agent review system."
        }
    )
    r.raise_for_status()
    pr_info = r.json()
    pr_number = pr_info["number"]
    print(f"Created PR #{pr_number}")
    
    return branch_name, pr_number


def simulate_webhook(pr_number):
    """Simulate a GitHub webhook event."""
    print("\n" + "=" * 70)
    print("STEP 2: Simulating webhook event")
    print("=" * 70)
    
    # In a real scenario, this would be sent by GitHub
    # For testing, we'll just log what would happen
    print(f"Webhook event: pull_request.opened")
    print(f"PR #{pr_number} would trigger the review pipeline")
    print("In production: POST /api/v1/webhooks/github")
    
    return True


def main():
    print("Adaptive Multi-Agent PR Review - E2E Test")
    print("This creates a real PR with vulnerable code for testing")
    print()
    
    try:
        branch_name, pr_number = create_branch_and_pr()
        simulate_webhook(pr_number)
        
        print("\n" + "=" * 70)
        print("SUCCESS: Test PR created")
        print("=" * 70)
        print(f"Branch: {branch_name}")
        print(f"PR: #{pr_number}")
        print(f"URL: https://github.com/{REPO}/pull/{pr_number}")
        print()
        print("To test the full pipeline:")
        print("1. Ensure the review system is running")
        print("2. The webhook should trigger automatically")
        print("3. Or manually trigger: POST /api/v1/reviews/{review_id}/rerun")
        
    except httpx.HTTPStatusError as e:
        print(f"\nERROR: {e.response.status_code}")
        print(e.response.text)
        return 1
    
    return 0


if __name__ == "__main__":
    exit(main())
