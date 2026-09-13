"""Celery application with Redis broker/backend (docs/QUEUE.md §1).

Task routing covers the full queue table from QUEUE.md so later phases register
tasks against the named queues without reconfiguring the broker. Production Python
tasks are sync; async I/O (DB, Redis) is delegated to ``asyncio.run`` cores in
``app.queue.tasks``.
"""

from __future__ import annotations

from celery import Celery

from app.config.settings import Settings, get_settings

# Canonical queue names (docs/QUEUE.md §1).
QUEUES = {
    "pr_ingestion": "pr_ingestion_queue",
    "review_task": "review_task_queue",
    "security": "security_queue",
    "quality": "quality_queue",
    "performance": "performance_queue",
    "architecture": "architecture_queue",
    "standards": "standards_queue",
    "decision": "decision_queue",
    "publisher": "publisher_queue",
    "feedback": "feedback_queue",
    "default": "default",
}

TASK_ROUTES = {
    "queue.enqueue_review": {"queue": QUEUES["pr_ingestion"]},
    "queue.pop_and_stage": {"queue": QUEUES["default"]},
    "agents.run_agent": {"queue": QUEUES["review_task"]},
}


def create_celery(settings: Settings | None = None) -> Celery:
    """Build a configured Celery application from application settings."""
    cfg = settings or get_settings()
    app = Celery(
        "adaptive_review",
        include=["app.queue.tasks", "app.queue.agent_tasks"],
    )
    app.conf.update(
        broker_url=cfg.redis_url,
        result_backend=cfg.redis_url,
        task_serializer="json",
        result_serializer="json",
        accept_content=["json"],
        task_acks_late=True,
        task_track_started=True,
        worker_prefetch_multiplier=1,
        task_routes=TASK_ROUTES,
        broker_connection_retry_on_startup=True,
    )
    return app


celery_app = create_celery()
