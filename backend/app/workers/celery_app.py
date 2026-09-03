"""Celery application and beat schedule."""

from __future__ import annotations

from celery import Celery
from celery.schedules import crontab

from app.core.config import settings

celery_app = Celery(
    "ois",
    broker=settings.REDIS_URL,
    backend=settings.REDIS_URL,
    include=["app.workers.tasks"],
)

celery_app.conf.update(
    task_acks_late=True,
    task_reject_on_worker_lost=True,
    task_default_queue="ingest",
    task_time_limit=1800,
    task_soft_time_limit=1500,
    worker_prefetch_multiplier=1,
    broker_connection_retry_on_startup=True,
    task_routes={
        "ois.run_all_sources": {"queue": "ingest"},
        "ois.run_source": {"queue": "ingest"},
        "ois.reap_stale_runs": {"queue": "ingest"},
    },
    beat_schedule={
        "collect-daily": {"task": "ois.run_all_sources", "schedule": crontab(hour=3, minute=0)},
        "reap-stale-runs": {"task": "ois.reap_stale_runs", "schedule": crontab(minute="*/15")},
    },
)
