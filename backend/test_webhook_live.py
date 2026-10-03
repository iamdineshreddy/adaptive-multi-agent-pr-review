"""Test the webhook endpoint with the configured secret."""

import hashlib
import hmac
import json
import os

import httpx

WEBHOOK_URL = "http://localhost:8000/api/v1/webhooks/github"
WEBHOOK_SECRET = os.environ.get("ADAPTIVE_GITHUB_WEBHOOK_SECRET", "test-webhook-secret")

payload = {
    "action": "opened",
    "number": 99,
    "pull_request": {
        "number": 99,
        "title": "Test PR",
        "state": "open",
        "body": "Test",
        "head": {"ref": "feature/test", "sha": "abc123"},
        "base": {"ref": "main"},
        "changed_files": 1,
        "additions": 10,
        "deletions": 5,
        "user": {"login": "testuser"},
    },
    "repository": {
        "id": 123456,
        "full_name": "test/repo",
    },
}

body = json.dumps(payload)
signature = hmac.new(
    WEBHOOK_SECRET.encode(),
    body.encode(),
    digestmod=hashlib.sha256,
).hexdigest()

r = httpx.post(
    WEBHOOK_URL,
    json=payload,
    headers={
        "X-GitHub-Event": "pull_request",
        "X-GitHub-Delivery": "test-delivery-001",
        "X-Hub-Signature-256": f"sha256={signature}",
    },
    timeout=10,
)
print(f"Status: {r.status_code}")
print(f"Response: {r.text}")
