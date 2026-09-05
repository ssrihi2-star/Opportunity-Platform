"""Background tasks.

Two different reliability stories live in this file, and telling them apart is
most of the design:

* **Ingestion** — `run_source`, `run_all_sources`, `reap_stale_runs` — is
  idempotent. The unique indexes on `raw_records` and `signal_observations` mean
  a repeated run stores nothing new, so those tasks are acked late, may be
  redelivered when a worker is lost, and may be retried.
* **Notification** — `run_monitoring`, `run_digests` — is not, because
  `alerts.dispatch` hands a message to Telegram or SMTP *before* the transaction
  that records the delivery commits. Re-running a batch that already sent part
  of itself sends that part again. Those tasks are therefore acked early, are
  never retried as a batch, and recover through the next scheduled run instead:
  change events are durable, the lookback window re-presents the ones that were
  never delivered, and `(user_id, dedupe_key)` suppresses the ones that were.

Every task here is a thin wrapper around an `async` phase function, so the
phases can be exercised directly in tests without a broker, a worker or a
scheduler.
"""

from __future__ import annotations

import asyncio
import dataclasses
import uuid
from collections.abc import Sequence
from datetime import UTC, datetime, timedelta

import sqlalchemy as sa
from celery.exceptions import SoftTimeLimitExceeded

from app.core.config import get_settings
from app.core.logging import get_logger
from app.db.session import SessionLocal
from app.models.enums import DigestFrequency
from app.models.models import Source, SystemAuditLog
from app.services.alerts import dispatch, run_digests
from app.services.ingestion import reap_stale_runs, run_source
from app.services.monitoring import monitor_all, recent_events
from app.workers.celery_app import celery_app
from app.workers.locks import try_advisory_xact_lock
from app.workers.schedule import due_digest_frequencies, schedule_timezone

log = get_logger("worker")

#: Advisory lock names. One per guarded phase; see `app.workers.locks`.
MONITORING_LOCK = "ois.monitoring"
DIGEST_LOCK = "ois.digests"

#: Digest retries are exponential and bounded: 120s, then 240s, then give up.
DIGEST_RETRY_BASE_SECONDS = 120
DIGEST_RETRY_MAX_SECONDS = 1800

#: The ordered run spans three phases, so it gets more than the app-wide
#: `task_time_limit` of 1800s — a collection that legitimately takes 40 minutes
#: must not be killed halfway and leave the night's alerts undispatched. The
#: soft limit fires first and is caught, so the run stops at a transaction
#: boundary and reports what it managed, rather than being SIGKILLed.
PIPELINE_TIME_LIMIT_SECONDS = 7200
PIPELINE_SOFT_TIME_LIMIT_SECONDS = 6900


class DigestPhaseError(RuntimeError):
    """One or more digest frequencies could not be written."""


def _run(coro):
    return asyncio.run(coro)


async def _run_source_by_id(source_id: uuid.UUID) -> dict:
    async with SessionLocal() as session:
        source = await session.get(Source, source_id)
        if source is None:
            return {"error": f"Source {source_id} no longer exists."}
        result = await run_source(session, source, trigger="scheduled")
        await session.commit()
        return dataclasses.asdict(result)


@celery_app.task(name="ois.run_source", bind=True, max_retries=3, default_retry_delay=60)
def task_run_source(self, source_id: str) -> dict:  # noqa: ANN001
    try:
        return _run(_run_source_by_id(uuid.UUID(source_id)))
    except Exception as exc:  # noqa: BLE001
        log.error("task_run_source_failed", source_id=source_id, error=str(exc))
        raise self.retry(exc=exc) from exc


async def _run_all() -> list[dict]:
    async with SessionLocal() as session:
        sources = (await session.execute(sa.select(Source).where(Source.enabled.is_(True)))).scalars().all()
        results = []
        for source in sources:
            # A failing source must not stop the others.
            try:
                result = await run_source(session, source, trigger="scheduled")
                results.append(dataclasses.asdict(result))
            except SoftTimeLimitExceeded:
                # Celery's soft limit means "stop now". Recording it as one
                # source's failure and carrying on would run the task into the
                # hard limit, which kills the worker process outright.
                raise
            except Exception as exc:  # noqa: BLE001
                log.error("source_failed", slug=source.slug, error=str(exc))
                results.append({"source": source.slug, "error": str(exc)})
            await session.commit()
        return results


@celery_app.task(name="ois.run_all_sources")
def task_run_all_sources() -> list[dict]:
    return _run(_run_all())


async def _reap() -> int:
    async with SessionLocal() as session:
        count = await reap_stale_runs(session)
        await session.commit()
        return count


@celery_app.task(name="ois.reap_stale_runs")
def task_reap_stale_runs() -> int:
    return _run(_reap())


# ------------------------------------------------------- monitoring and alerts
async def run_monitoring_phase(
    *,
    now: datetime | None = None,
    lookback_hours: int | None = None,
    event_limit: int | None = None,
) -> dict:
    """Check conditions, record what changed, dispatch alerts. One transaction.

    The same sequence and the same transaction discipline as the admin endpoint
    in `app/api/v1/monitoring.py`: `monitor_all` -> `recent_events` ->
    `dispatch` -> audit row -> commit. Deliberately not a copy of it: the
    endpoint stays available for an administrator who wants to run the monitor
    by hand, and both call the same services.

    The lookback is wider than the endpoint's one minute because a scheduled run
    has to recover the run before it if that one died. Re-presenting an event
    that was already delivered costs nothing: the delivery is suppressed by the
    unique constraint on `(user_id, dedupe_key)`.
    """
    cfg = get_settings()
    now = now or datetime.now(UTC)
    hours = cfg.MONITOR_LOOKBACK_HOURS if lookback_hours is None else lookback_hours
    limit = cfg.MONITOR_EVENT_LIMIT if event_limit is None else event_limit

    async with SessionLocal() as session:
        try:
            if not await try_advisory_xact_lock(session, MONITORING_LOCK):
                # Somebody else is dispatching right now. Skipping is the safe
                # answer: waiting would mean doing the same work twice.
                return {
                    "phase": "monitoring",
                    "status": "skipped",
                    "reason": "another monitoring run holds the lock",
                }
            counts = await monitor_all(session, now=now)
            events = await recent_events(session, since=now - timedelta(hours=hours), limit=limit)
            outcome = await dispatch(session, events=events, now=now)
            session.add(
                SystemAuditLog(
                    # No actor: this is the schedule, not a person. Counts only —
                    # an audit row is not a place to copy notification bodies.
                    actor_user_id=None,
                    action="monitoring.run.scheduled",
                    object_type="opportunity",
                    after={
                        **counts,
                        "events_considered": len(events),
                        "alerts_sent": outcome.sent,
                        "alerts_suppressed": outcome.suppressed,
                    },
                )
            )
            await session.commit()
        except Exception as exc:  # noqa: BLE001 - reported, never blindly retried
            await session.rollback()
            log.error("scheduled_monitoring_failed", error=f"{type(exc).__name__}: {exc}")
            return {"phase": "monitoring", "status": "failed", "reason": type(exc).__name__}

        log.info(
            "scheduled_monitoring_finished",
            opportunities=counts["opportunities"],
            checks=counts["checks"],
            events=counts["events"],
            considered=len(events),
            sent=outcome.sent,
            suppressed=outcome.suppressed,
        )
        return {
            "phase": "monitoring",
            "status": "ok",
            **counts,
            "events_considered": len(events),
            "alerts_sent": outcome.sent,
            "alerts_suppressed": outcome.suppressed,
        }


@celery_app.task(
    name="ois.run_monitoring",
    acks_late=False,
    reject_on_worker_lost=False,
    max_retries=0,
)
def task_run_monitoring() -> dict:
    """Run the monitor and dispatch what it found.

    `acks_late=False` and `max_retries=0` are the point of this task's
    definition, not an oversight. The message is acked before the work starts
    and the batch is never re-sent, because a redelivery after a partial
    failure would repeat every Telegram and SMTP send that had already gone out
    before the transaction rolled back. Recovery is the next scheduled run,
    which re-presents the undelivered events and is deduplicated by the
    database. See docs/scheduling.md for the exact guarantee.
    """
    return _run(run_monitoring_phase())


# --------------------------------------------------------------------- digests
def _scheduled_frequencies(values: Sequence[str]) -> list[str]:
    """Reject anything that is not a real digest period, loudly and once.

    `DigestFrequency.OFF` is a preference, not a schedule: a user who asked for
    nothing is simply not matched by `alerts.run_digests`. An operator typo, on
    the other hand, should fail the task instead of writing nothing and
    reporting success.
    """
    allowed = (DigestFrequency.DAILY.value, DigestFrequency.WEEKLY.value)
    out: list[str] = []
    for value in values:
        name = str(value).strip().lower()
        if name not in allowed:
            raise ValueError(
                f"Digest frequency {value!r} cannot be scheduled; expected one of {list(allowed)}."
            )
        if name not in out:
            out.append(name)
    return out


async def run_digest_phase(
    *,
    frequencies: Sequence[str] | None = None,
    now: datetime | None = None,
) -> dict:
    """Write the digests that are due, through `alerts.run_digests`.

    One transaction per frequency. A weekly digest that fails must not throw
    away the daily digests that were already written, and a retry must not
    regenerate a frequency that committed — `digests` has no uniqueness
    constraint, so a repeated successful run would leave two rows for one
    period and the user would read the same summary twice.

    Nothing here sends mail or messages: a digest is a stored row the user reads
    in the product, which is why this phase *can* be retried and the alert phase
    above cannot.
    """
    cfg = get_settings()
    now = now or datetime.now(UTC)
    due = (
        list(frequencies)
        if frequencies
        else due_digest_frequencies(
            now=now,
            timezone_name=schedule_timezone(cfg),
            weekly_day=cfg.DIGEST_WEEKLY_DAY,
        )
    )
    written: dict[str, object] = {}
    failed: list[str] = []
    skipped: list[str] = []

    for frequency in _scheduled_frequencies(due):
        async with SessionLocal() as session:
            try:
                if not await try_advisory_xact_lock(session, f"{DIGEST_LOCK}:{frequency}"):
                    skipped.append(frequency)
                    written[frequency] = "skipped"
                    continue
                count = await run_digests(session, frequency=frequency, now=now)
                await session.commit()
            except Exception as exc:  # noqa: BLE001 - one period must not lose the rest
                await session.rollback()
                log.error("scheduled_digests_failed", frequency=frequency, error=str(exc))
                failed.append(frequency)
                written[frequency] = "failed"
                continue
            written[frequency] = count
            log.info("scheduled_digests_written", frequency=frequency, digests=count)

    succeeded = [f for f, value in written.items() if isinstance(value, int)]
    if failed and not succeeded:
        status = "failed"
    elif failed or skipped:
        status = "partial"
    else:
        status = "ok"
    return {
        "phase": "digests",
        "status": status,
        "frequencies": written,
        "failed": failed,
        "skipped": skipped,
    }


@celery_app.task(
    name="ois.run_digests",
    bind=True,
    acks_late=False,
    reject_on_worker_lost=False,
    max_retries=2,
    default_retry_delay=DIGEST_RETRY_BASE_SECONDS,
)
def task_run_digests(self, frequencies: Sequence[str] | None = None) -> dict:  # noqa: ANN001
    """Write due digests, retrying only the frequencies that rolled back.

    Retries are bounded and exponential (120s, 240s, then the task fails and
    says so), and they carry only the frequencies that rolled back: a committed
    digest is never regenerated, because `digests` has no uniqueness constraint
    and a second row for one period is the user reading the same summary twice.
    A permanent error — an unknown frequency, say — is not retried at all:
    `_scheduled_frequencies` raises before any work starts and nothing here
    catches it.

    `acks_late=False` for the same reason. A worker killed between two
    frequencies' commits would otherwise have its message redelivered and would
    rewrite the frequency that had already committed. Losing a run is the
    cheaper mistake: the next scheduled run writes a digest over a window that
    still contains the missed day.
    """
    result = _run(run_digest_phase(frequencies=frequencies))
    failed = [str(f) for f in (result.get("failed") or [])]
    if not failed:
        return result

    attempts = self.request.retries + 1
    if self.max_retries is not None and attempts > self.max_retries:
        raise DigestPhaseError(f"digests for {', '.join(failed)} still failing after {attempts} attempts")
    countdown = min(DIGEST_RETRY_BASE_SECONDS * (2 ** (attempts - 1)), DIGEST_RETRY_MAX_SECONDS)
    log.warning("scheduled_digests_retrying", frequencies=failed, attempt=attempts, countdown=countdown)
    raise self.retry(
        kwargs={"frequencies": failed},
        countdown=countdown,
        exc=DigestPhaseError(f"digests for {', '.join(failed)} rolled back"),
    )


# --------------------------------------------------------- the ordered nightly run
async def run_nightly_pipeline(*, now: datetime | None = None) -> dict:
    """collect -> monitor + dispatch -> digests, stopping at the first failure.

    Ordering is enforced here rather than by three cron times, because a cron
    time is a guess about how long the previous job took. Each phase runs only
    if the one it depends on completed:

    * collection failing as a *phase* (the database is unreachable, the soft
      time limit fired) stops everything — there would be nothing new to
      monitor and no honest digest to write;
    * individual sources failing does **not** stop the run. Ingestion already
      isolates them and records each one, and monitoring works on whatever
      measurements are stored. The report says how many failed;
    * monitoring failing or being skipped because another run holds the lock
      stops the digests, which summarise what monitoring just committed;
    * digests failing leaves the night's alerts delivered — that work is already
      committed — and is reported as degraded rather than silently dropped.
    """
    cfg = get_settings()
    now = now or datetime.now(UTC)
    report: dict = {
        "started_at": now.isoformat(),
        "timezone": schedule_timezone(cfg),
        "phases": {},
        "status": "ok",
    }
    phases: dict = report["phases"]

    # --- 1. collection. The existing task's own coroutine: one code path, so
    # scheduling the pipeline cannot drift from `ois.run_all_sources`.
    try:
        collected = await _run_all()
    except Exception as exc:  # noqa: BLE001 - the dependents must not run
        return _abort(report, "collect", exc)
    failed_sources = [row for row in collected if isinstance(row, dict) and row.get("error")]
    phases["collect"] = {
        "phase": "collect",
        "status": "partial" if failed_sources else "ok",
        "sources": len(collected),
        "failed_sources": len(failed_sources),
    }
    log.info("pipeline_collect_finished", sources=len(collected), failed_sources=len(failed_sources))

    # --- 2. monitoring and alert dispatch.
    monitoring = await run_monitoring_phase(now=now)
    phases["monitoring"] = monitoring
    if monitoring["status"] != "ok":
        report["status"] = "skipped" if monitoring["status"] == "skipped" else "aborted"
        log.warning("pipeline_stopped_before_digests", phase="monitoring", status=monitoring["status"])
        return report

    # --- 3. digests, now that the facts they summarise are committed.
    digests = await run_digest_phase(now=now)
    phases["digests"] = digests
    if digests["status"] != "ok" or failed_sources:
        report["status"] = "degraded"
    log.info(
        "pipeline_finished",
        status=report["status"],
        alerts_sent=monitoring.get("alerts_sent"),
        alerts_suppressed=monitoring.get("alerts_suppressed"),
        digests=digests["frequencies"],
    )
    return report


def _abort(report: dict, phase: str, exc: Exception) -> dict:
    report["phases"][phase] = {"phase": phase, "status": "failed", "reason": type(exc).__name__}
    report["status"] = "aborted"
    log.error("pipeline_aborted", phase=phase, error=f"{type(exc).__name__}: {exc}")
    return report


@celery_app.task(
    name="ois.run_nightly_pipeline",
    acks_late=False,
    reject_on_worker_lost=False,
    max_retries=0,
    time_limit=PIPELINE_TIME_LIMIT_SECONDS,
    soft_time_limit=PIPELINE_SOFT_TIME_LIMIT_SECONDS,
)
def task_run_nightly_pipeline() -> dict:
    """The one entry beat schedules when `SCHEDULER_ENABLED` is true.

    It replaces the bare collection entry rather than being added next to it, so
    a night never collects twice. Like `ois.run_monitoring` it is acked early
    and never retried as a whole, because it contains the phase that sends.
    """
    return _run(run_nightly_pipeline())
