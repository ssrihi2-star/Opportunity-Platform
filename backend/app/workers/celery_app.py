"""Celery application and beat schedule.

Nothing in here starts a scheduler. `beat_schedule` is configuration data that
the separate `celery beat` process reads; the API never imports this module, and
importing it — in a worker, in beat or in a test — creates no thread, no timer
and no connection. Whether anything is scheduled at all is decided by
`SCHEDULER_ENABLED` (see `app.workers.schedule` and docs/scheduling.md).
"""

from __future__ import annotations

from celery import Celery

from app.core.config import settings
from app.workers.schedule import build_beat_schedule, schedule_timezone

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
    # Crontabs are evaluated in the configured local timezone; stored timestamps
    # and `eta`/`countdown` arithmetic stay UTC, so a schedule expressed in
    # local time never changes what the database records.
    timezone=schedule_timezone(settings),
    enable_utc=True,
    task_routes={
        "ois.run_all_sources": {"queue": "ingest"},
        "ois.run_source": {"queue": "ingest"},
        "ois.reap_stale_runs": {"queue": "ingest"},
        # The ordered nightly run starts with collection, so it shares that
        # queue; the two phases that follow it are analysis, and are routed
        # separately so an operator can give alerting its own workers. The
        # `worker` service in docker-compose.yml consumes all of them.
        "ois.run_nightly_pipeline": {"queue": "ingest"},
        "ois.run_monitoring": {"queue": "analyze"},
        "ois.run_digests": {"queue": "analyze"},
    },
    beat_schedule=build_beat_schedule(settings),
)
