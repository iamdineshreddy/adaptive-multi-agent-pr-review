"""Docker Compose boot smoke for the Phase 18 stack (Phase 18 part 2).

Boots the full ``docker-compose.yml`` topology (postgres/redis/api/worker/
scheduler/frontend/prometheus/grafana) on a real Docker host and verifies the
published surface:

- API + worker-exporter ``/health`` liveness probes.
- Frontend SPA behind nginx (``/``).
- Both Prometheus scrape targets (``adaptive-review-api``, ``adaptive-review-worker``)
  reachable and reporting ``up`` with the Phase 15 bearer gate in front.
- Grafana UI serving.
- ``/api/v1/monitoring/prometheus`` on the API (8000) and the worker exporter
  (8001) returning 401 without a bearer token.
- Webhook route wired and signature-guarded (401 on unsigned delivery); when the
  host exports a real ``ADAPTIVE_GITHUB_WEBHOOK_SECRET`` matching the compose
  environment, a signed delivery is asserted 202 ``accepted`` through the running
  stack (real Postgres + Redis priority hand-off).

Self-skip discipline identical to the live Postgres/Redis lanes: without the
docker CLI or a reachable daemon the module skips with a clear reason and never
claims a boot result. Every run tears the stack down with ``docker compose down
-v`` even on assertion failure.

Honesty note (README integrity rule): this suite asserts the *surface* of a
running stack (status codes / health / target state); it fabricates no metrics
or numbers, and nothing here is reported as a benchmark.
"""

from __future__ import annotations

import hashlib
import hmac
import json
import os
import shutil
import subprocess
import time
import uuid
from pathlib import Path

import httpx
import pytest

_COMPOSE_FILE = Path(__file__).resolve().parents[2] / "docker-compose.yml"
_STACK_SERVICES = frozenset(
    {
        "postgres",
        "redis",
        "api",
        "worker",
        "scheduler",
        "frontend",
        "prometheus",
        "grafana",
    }
)
_BOOT_TIMEOUT_S = 300
_PROMETHEUS_SCRAPE_WAIT_S = 120


def _docker(*args: str) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        ["docker", *args],
        capture_output=True,
        text=True,
        timeout=_BOOT_TIMEOUT_S,
    )


def _require_docker() -> None:
    if shutil.which("docker") is None:
        pytest.skip("docker CLI not available; skipping compose boot smoke.")
    probe = _docker("version", "--format", "{{.Server.Version}}")
    if probe.returncode != 0:
        pytest.skip(
            "docker CLI present but daemon not reachable; skipping compose "
            f"boot smoke ({probe.stderr.strip()})."
        )


def _compose_ps() -> dict[str, dict[str, str]]:
    """Parse ``docker compose ps --format json`` into service → state map."""
    result = _docker(
        "compose", "-f", str(_COMPOSE_FILE), "ps", "-a", "--format", "json"
    )
    if result.returncode != 0:
        return {}
    services: dict[str, dict[str, str]] = {}
    for line in result.stdout.splitlines():
        line = line.strip()
        if not line:
            continue
        try:
            entry = json.loads(line)
        except json.JSONDecodeError:
            continue
        name = str(entry.get("name") or entry.get("service") or "")
        if name:
            services[name] = {
                "state": str(entry.get("state") or ""),
                "health": str(entry.get("health") or ""),
            }
    return services


def _stack_healthy() -> bool:
    ps = _compose_ps()
    for service in _STACK_SERVICES:
        status = ps.get(service)
        if status is None:
            return False
        if status["state"] != "running":
            return False
        if status["health"] in {"unhealthy", "starting"}:
            return False
    return True


@pytest.fixture(scope="module")
def compose_stack() -> dict[str, str]:
    """Bring the full stack up (clean), yield endpoint base URLs, tear down."""
    _require_docker()
    yield_dict: dict[str, str] | None = None
    try:
        _docker("compose", "-f", str(_COMPOSE_FILE), "down", "-v", "--remove-orphans")
        up = _docker("compose", "-f", str(_COMPOSE_FILE), "up", "-d", "--build")
        if up.returncode != 0:
            raise AssertionError(f"docker compose up failed: {up.stderr.strip()}")

        deadline = time.monotonic() + _BOOT_TIMEOUT_S
        while time.monotonic() < deadline:
            if _stack_healthy():
                break
            time.sleep(5)
        else:
            raise AssertionError(
                "compose stack did not become healthy within "
                f"{_BOOT_TIMEOUT_S}s: {_compose_ps()}"
            )

        yield_dict = {
            "api": "http://127.0.0.1:8000",
            "worker": "http://127.0.0.1:8001",
            "frontend": "http://127.0.0.1",
            "prometheus": "http://127.0.0.1:9090",
            "grafana": "http://127.0.0.1:3000",
        }
        yield yield_dict
    finally:
        _docker("compose", "-f", str(_COMPOSE_FILE), "down", "-v", "--remove-orphans")


def test_api_and_worker_liveness(compose_stack: dict[str, str]) -> None:
    with httpx.Client(timeout=10.0) as client:
        for url, service in (
            (compose_stack["api"], "adaptive-review"),
            (compose_stack["worker"], "adaptive-review-worker"),
        ):
            response = client.get(f"{url}/health")
            assert response.status_code == 200
            body = response.json()
            assert body["status"] == "ok"
            if "service" in body:
                assert body["service"] == service


def test_frontend_serves_spa(compose_stack: dict[str, str]) -> None:
    with httpx.Client(timeout=10.0, follow_redirects=True) as client:
        response = client.get(compose_stack["frontend"])
        assert response.status_code == 200
        assert "content-type" in response.headers


def test_api_and_worker_metrics_require_bearer_token(
    compose_stack: dict[str, str],
) -> None:
    with httpx.Client(timeout=10.0) as client:
        for url in (compose_stack["api"], compose_stack["worker"]):
            response = client.get(f"{url}/api/v1/monitoring/prometheus")
            assert response.status_code == 401


def test_prometheus_targets_up(compose_stack: dict[str, str]) -> None:
    target_url = f"{compose_stack['prometheus']}/api/v1/targets"
    deadline = time.monotonic() + _PROMETHEUS_SCRAPE_WAIT_S
    with httpx.Client(timeout=15.0) as client:
        while time.monotonic() < deadline:
            response = client.get(target_url)
            if response.status_code == 200:
                targets = response.json()["data"]["activeTargets"]
                jobs = {t["labels"]["job"]: t for t in targets}
                expected = {"adaptive-review-api", "adaptive-review-worker"}
                if expected <= set(jobs) and all(t["health"] == "up" for t in targets):
                    return
            time.sleep(10)
    raise AssertionError(
        "Prometheus targets did not all report up within "
        f"{_PROMETHEUS_SCRAPE_WAIT_S}s: {client.get(target_url).text[:800]}"
    )


def test_grafana_serves_ui(compose_stack: dict[str, str]) -> None:
    with httpx.Client(timeout=10.0, follow_redirects=True) as client:
        response = client.get(f"{compose_stack['grafana']}/login")
        assert response.status_code == 200


def _webhook_payload() -> dict[str, object]:
    number = 90000 + uuid.uuid4().int % 1000
    return {
        "action": "opened",
        "number": number,
        "pull_request": {
            "id": 40000 + number,
            "number": number,
            "title": "Compose smoke PR",
            "state": "open",
            "user": {"login": "smoke", "id": 1},
            "base": {"ref": "main", "sha": "base-sha"},
            "head": {"ref": "feature", "sha": f"head-sha-{number}"},
            "changed_files": 2,
            "additions": 25,
            "deletions": 3,
        },
        "repository": {
            "id": 42424240 + number,
            "full_name": f"smoke/repo-{uuid.uuid4().hex[:8]}",
            "default_branch": "main",
            "language": "python",
        },
        "installation": {"id": 7},
    }


def test_webhook_route_guarded_then_accepted(
    compose_stack: dict[str, str],
) -> None:
    url = f"{compose_stack['api']}/api/v1/webhooks/github"
    body = json.dumps(_webhook_payload()).encode()
    headers = {"X-GitHub-Event": "pull_request", "X-GitHub-Delivery": str(uuid.uuid4())}

    with httpx.Client(timeout=15.0) as client:
        unsigned = client.post(url, content=body, headers=headers)
        assert unsigned.status_code == 401

        secret = os.environ.get("ADAPTIVE_GITHUB_WEBHOOK_SECRET")
        if not secret:
            return
        digest = hmac.new(secret.encode(), body, hashlib.sha256).hexdigest()
        signed_headers = {**headers, "X-Hub-Signature-256": f"sha256={digest}"}
        accepted = client.post(url, content=body, headers=signed_headers)
        assert accepted.status_code == 202
        assert "review_id" in accepted.json()
