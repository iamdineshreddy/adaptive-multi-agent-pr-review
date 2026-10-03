"""Quick end-to-end webhook test with proper HMAC signature."""

import hashlib
import hmac
import json

import httpx

WEBHOOK_URL = "http://localhost:8000/api/v1/webhooks/github"
SECRET = "test-webhook-secret"


def compute_signature(body: bytes) -> str:
    return "sha256=" + hmac.new(SECRET.encode(), body, hashlib.sha256).hexdigest()


def test_webhook_ping():
    """Ping event should return 200."""
    payload = {"zen": "Design for failure.", "hook_id": 12345}
    body = json.dumps(payload).encode()
    headers = {
        "X-GitHub-Event": "ping",
        "X-GitHub-Delivery": "test-delivery-001",
        "X-Hub-Signature-256": compute_signature(body),
        "Content-Type": "application/json",
    }
    r = httpx.post(WEBHOOK_URL, content=body, headers=headers, timeout=10)
    print(f"Ping: {r.status_code} - {r.text[:200]}")
    assert r.status_code == 200


def test_webhook_pull_request_opened():
    """PR opened event should return 202 and enqueue review."""
    payload = {
        "action": "opened",
        "number": 42,
        "pull_request": {
            "id": 123456,
            "number": 42,
            "title": "Add authentication",
            "state": "open",
            "body": "Implements login flow",
            "head": {"ref": "feature/auth", "sha": "abc123"},
            "base": {"ref": "main", "sha": "def456"},
            "changed_files": 2,
            "additions": 20,
            "deletions": 10,
            "user": {"login": "testuser"},
        },
        "repository": {
            "id": 789012,
            "full_name": "test/repo",
        },
    }
    body = json.dumps(payload).encode()
    headers = {
        "X-GitHub-Event": "pull_request",
        "X-GitHub-Delivery": "test-delivery-002",
        "X-Hub-Signature-256": compute_signature(body),
        "Content-Type": "application/json",
    }
    r = httpx.post(WEBHOOK_URL, content=body, headers=headers, timeout=10)
    print(f"PR opened: {r.status_code} - {r.text[:300]}")
    assert r.status_code == 202


if __name__ == "__main__":
    print("=" * 60)
    print("Webhook E2E Tests")
    print("=" * 60)
    test_webhook_ping()
    test_webhook_pull_request_opened()
    print("\nAll webhook tests passed!")
