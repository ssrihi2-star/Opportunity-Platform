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
  of itself sends that part again. Those tasks are acked early and are never
  retried as a batch.

The honest statement of what that buys, which is what docs/scheduling.md says
too: **best-effort delivery. Duplicate database records are constrained for the
same dedupe identity, but external delivery can be lost or repeated after
failures, and equivalent regenerated events may have different identities.**
Early acknowledgement plus a lookback window is a recovery *heuristic*, not a
guarantee — a run killed after sending and before committing loses the record of
what it sent, and the next run re-derives an equivalent event under a new id, so
nothing deduplicates the repeat.

Digests are the exception that proves the rule: they are stored rows with no
external send, so they can be retried, bounded at `1 + DIGEST_MAX_RETRIES`
attempts per period, and made idempotent by `(user_id, frequency, period_key)`.

Every task here is a thin wrapper around an `async` phase function, so the
phases can be exercised directly in tests without a broker, a worker or a
scheduler. Failure paths log a fixed outcome code and the exception *class*
name — never `str(exc)`, which can carry SQL parameters, a bot token inside a
request URL, or a recipient address.
"""

from __future__ import annotations

import asyncio
import dataclasses
import uuid
from collections.abc import Mapping, Sequence
from datetime import UTC, datetime, timedelta

import sqlalchemy as sa
from celery.exceptions import SoftTimeLimitExceeded

from app.core.config import get_settings
from app.core.errors import failure_code, is_unrecoverable
from app.core.logging import get_logger
from app.db.session import SessionLocal
from app.models.enums import DigestFrequency
from app.models.models import Source, SystemAuditLog
from app.services.alerts import DigestPeriod, digest_period, dispatch, run_digests
from app.services.ingestion import reap_stale_runs, run_source
from app.services.monitoring import monitor_all, recent_events
from app.workers.celery_app import celery_app
from app.workers.locks import try_advisory_xact_lock
from app.workers.schedule import due_digest_frequencies, schedule_timezone

log = get_logger("worker")

#: Advisory lock names. One per guarded phase; see `app.workers.locks`.
MONITORING_LOCK = "ois.monitoring"
DIGEST_LOCK = "ois.digests"

#: Digest retries are exponential and bounded: the scheduled attempt, then 120s
#: and 240s later. `1 + DIGEST_MAX_RETRIES` = 3 attempts per period, after which
#: the task fails and stays failed.
DIGEST_MAX_RETRIES = 2
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
        log.error(
            "task_run_source_failed",
            outcome="retry_scheduled",
            source_id=source_id,
            error_type=failure_code(exc),
        )
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
                log.error(
                    "source_failed", outcome="source_skipped", slug=source.slug, error_type=failure_code(exc)
                )
                # A task result is persisted in the result backend, so it gets
                # the same treatment as a log line: a class name, never the
                # exception text. The durable, user-visible run history is
                # `SourceRun.error`, written by the ingestion service itself.
                results.append({"source": source.slug, "error": failure_code(exc)})
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
                    "reason_code": "lock_held",
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
                        "alerts_failed": outcome.failed,
                    },
                )
            )
            await session.commit()
        except Exception as exc:  # noqa: BLE001 - reported, never blindly retried
            await session.rollback()
            if is_unrecoverable(exc):
                # A time limit or a dead connection is not a phase result. Let it
                # reach Celery, so the task state says what actually happened
                # instead of reporting a soft kill as a phase that failed.
                raise
            log.error("scheduled_monitoring_failed", outcome="phase_failed", error_type=failure_code(exc))
            return {
                "phase": "monitoring",
                "status": "failed",
                "reason_code": "monitoring_failed",
                "error_type": failure_code(exc),
            }

        log.info(
            "scheduled_monitoring_finished",
            opportunities=counts["opportunities"],
            checks=counts["checks"],
            events=counts["events"],
            considered=len(events),
            sent=outcome.sent,
            suppressed=outcome.suppressed,
            failed=outcome.failed,
        )
        return {
            "phase": "monitoring",
            "status": "ok",
            **counts,
            "events_considered": len(events),
            "alerts_sent": outcome.sent,
            "alerts_suppressed": outcome.suppressed,
            "alerts_failed": outcome.failed,
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
            raise ValueError(f"digest_frequency_unsupported:{name}")
        if name not in out:
            out.append(name)
    return out


def _resolve_periods(
    *,
    periods: Sequence[Mapping[str, str]] | None,
    frequencies: Sequence[str] | None,
    now: datetime,
) -> list[DigestPeriod]:
    """The periods this run owes, with their identity already fixed.

    A retry arrives with `periods`: the keys and boundaries chosen by the
    attempt that failed, carried verbatim through the broker. Nothing here
    recalculates them, which is what makes a retry after midnight, after a week
    boundary or after a timezone change still write the period it was scheduled
    for. A first attempt arrives with frequencies, or with neither, and gets the
    canonical completed periods for `now`.
    """
    cfg = get_settings()
    timezone_name = schedule_timezone(cfg)
    if periods:
        resolved = [DigestPeriod.from_dict(raw) for raw in periods]
        _scheduled_frequencies([period.frequency for period in resolved])
        return resolved
    due = (
        list(frequencies)
        if frequencies
        else due_digest_frequencies(now=now, timezone_name=timezone_name, weekly_day=cfg.DIGEST_WEEKLY_DAY)
    )
    out: list[DigestPeriod] = []
    for frequency in _scheduled_frequencies(due):
        period = digest_period(frequency, now=now, timezone_name=timezone_name)
        if period is not None:
            out.append(period)
    return out


async def run_digest_phase(
    *,
    periods: Sequence[Mapping[str, str]] | None = None,
    frequencies: Sequence[str] | None = None,
    now: datetime | None = None,
) -> dict:
    """Write the digests that are due, through `alerts.run_digests`.

    One transaction per period. A period that fails must not throw away the
    periods that were already written, and a retry must not rewrite one that
    committed — so the retry carries only the failures, and the unique index on
    `(user_id, frequency, period_key)` makes any overlap a counted duplicate
    rather than a second row.

    Nothing here sends mail or messages: a digest is a stored row the user reads
    in the product. That is why this phase can be retried and the alert phase
    above cannot.
    """
    now = now or datetime.now(UTC)
    resolved = _resolve_periods(periods=periods, frequencies=frequencies, now=now)
    results: list[dict] = []
    failed_periods: list[dict] = []
    skipped: list[str] = []

    for period in resolved:
        entry: dict = {
            "frequency": period.frequency,
            "period_key": period.key,
            "timezone": period.timezone,
        }
        async with SessionLocal() as session:
            try:
                if not await try_advisory_xact_lock(session, f"{DIGEST_LOCK}:{period.key}"):
                    skipped.append(period.key)
                    entry["status"] = "skipped"
                    results.append(entry)
                    continue
                outcome = await run_digests(session, frequency=period.frequency, now=now, period=period)
                await session.commit()
            except Exception as exc:  # noqa: BLE001 - one period must not lose the rest
                if is_unrecoverable(exc):
                    raise
                await session.rollback()
                log.error(
                    "scheduled_digests_failed",
                    outcome="period_failed",
                    frequency=period.frequency,
                    period_key=period.key,
                    error_type=failure_code(exc),
                )
                entry["status"] = "failed"
                results.append(entry)
                failed_periods.append(period.to_dict())
                continue

            if outcome.failed and not (outcome.written or outcome.duplicates):
                # Nothing at all came of this period. Calling it "partial" would
                # let a run where every user failed report as a run that mostly
                # worked.
                entry_status = "failed"
            elif outcome.failed:
                entry_status = "partial"
            else:
                entry_status = "ok"
            entry.update(
                status=entry_status,
                written=outcome.written,
                duplicates=outcome.duplicates,
                users_failed=outcome.failed,
            )
            results.append(entry)
            if outcome.failed:
                # Users who were written are protected by the unique index, so
                # retrying the period re-attempts only the ones that failed.
                failed_periods.append(period.to_dict())
            log.info(
                "scheduled_digests_written",
                frequency=period.frequency,
                period_key=period.key,
                written=outcome.written,
                duplicates=outcome.duplicates,
                users_failed=outcome.failed,
            )

    written_periods = [entry for entry in results if entry["status"] in {"ok", "partial"}]
    if failed_periods and not written_periods:
        status = "failed"
    elif failed_periods or skipped:
        status = "partial"
    else:
        status = "ok"
    return {
        "phase": "digests",
        "status": status,
        "periods": results,
        "failed_periods": failed_periods,
        "skipped": skipped,
    }


def _delegate_digest_retry(failed_periods: Sequence[Mapping[str, str]]) -> dict:
    """Hand failed periods to the dedicated digest task, with their identity intact.

    The pipeline never retries them itself. Retrying the pipeline would repeat
    collection and, worse, re-run the phase that sends alerts. The dedicated task
    carries the original keys and boundaries, so the retry writes the period that
    failed rather than whichever period the retry lands in.

    Publishing can fail — the broker is a network service, and neither
    `apply_async` nor `self.retry` removes that. When it does, the failure is
    reported in the run's own result and left to the next scheduled run; it is
    not swallowed and not retried in a loop.
    """
    try:
        pending = task_run_digests.apply_async(
            kwargs={"periods": [dict(period) for period in failed_periods]},
            countdown=DIGEST_RETRY_BASE_SECONDS,
        )
    except Exception as exc:  # noqa: BLE001 - a broker outage is a fact to report
        log.error(
            "digest_retry_publish_failed",
            outcome="retry_not_queued",
            periods=len(failed_periods),
            error_type=failure_code(exc),
        )
        return {
            "published": False,
            "reason_code": "broker_publish_failed",
            "error_type": failure_code(exc),
            "periods": len(failed_periods),
        }
    log.info("digest_retry_queued", periods=len(failed_periods), task_id=pending.id)
    return {"published": True, "task_id": pending.id, "periods": len(failed_periods)}


@celery_app.task(
    name="ois.run_digests",
    bind=True,
    acks_late=False,
    reject_on_worker_lost=False,
    max_retries=DIGEST_MAX_RETRIES,
    default_retry_delay=DIGEST_RETRY_BASE_SECONDS,
)
def task_run_digests(
    self,  # noqa: ANN001
    periods: Sequence[Mapping[str, str]] | None = None,
    frequencies: Sequence[str] | None = None,
) -> dict:
    """Write due digests, retrying only the periods that did not finish.

    **Attempt limit:** `1 + DIGEST_MAX_RETRIES` = 3 executions per period — the
    scheduled attempt plus two retries, 120s and 240s later. After that the task
    fails with `DigestPhaseError` and stays failed; nothing retries indefinitely.

    A retry carries the original `periods` (key, boundaries, timezone), never a
    recalculated window, and never a frequency that already committed. Users
    inside a partially failed period are skipped on retry by the database, not by
    memory: the unique index on `(user_id, frequency, period_key)` makes their
    digest a counted duplicate.

    `acks_late=False` because a redelivered message would re-run periods that
    committed. Losing a run is the cheaper mistake, and with a canonical period
    key even a lost run cannot produce a duplicate later.

    Permanent errors are not retried at all: an unsupported frequency raises out
    of `_scheduled_frequencies` before any work starts and nothing catches it.
    """
    result = _run(run_digest_phase(periods=periods, frequencies=frequencies))
    failed = [dict(period) for period in (result.get("failed_periods") or [])]
    if not failed:
        return result

    # "key" is the wire format: DigestPeriod.to_dict() on the way out,
    # from_dict() on the way back in. Getting this wrong would name the period
    # an operator has to go and fix as "None".
    keys = sorted(str(period["key"]) for period in failed)
    attempts = self.request.retries + 1
    if self.max_retries is not None and attempts > self.max_retries:
        raise DigestPhaseError(f"digest_periods_unresolved:{','.join(keys)}:after_{attempts}_attempts")
    countdown = min(DIGEST_RETRY_BASE_SECONDS * (2 ** (attempts - 1)), DIGEST_RETRY_MAX_SECONDS)
    log.warning(
        "scheduled_digests_retrying",
        outcome="retry_scheduled",
        period_keys=keys,
        attempt=attempts,
        max_attempts=self.max_retries + 1 if self.max_retries is not None else None,
        countdown=countdown,
    )
    raise self.retry(
        kwargs={"periods": failed},
        countdown=countdown,
        exc=DigestPhaseError(f"digest_periods_rolled_back:{','.join(keys)}"),
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
      isolates and records them, and monitoring works on whatever measurements
      are stored. The report says how many failed;
    * monitoring failing, or being skipped because another run holds the lock,
      stops the digests;
    * digests failing leaves the night's alerts delivered — that work is already
      committed — and is handed to the dedicated digest task for a bounded
      retry, which cannot re-run collection or re-send an alert.
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
        if is_unrecoverable(exc):
            raise
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
    if digests["failed_periods"]:
        digests["retry"] = _delegate_digest_retry(digests["failed_periods"])
    if digests["status"] != "ok" or failed_sources:
        report["status"] = "degraded"
    log.info(
        "pipeline_finished",
        status=report["status"],
        alerts_sent=monitoring.get("alerts_sent"),
        alerts_suppressed=monitoring.get("alerts_suppressed"),
        alerts_failed=monitoring.get("alerts_failed"),
        digest_periods=[entry.get("period_key") for entry in digests["periods"]],
    )
    return report


def _abort(report: dict, phase: str, exc: Exception) -> dict:
    report["phases"][phase] = {
        "phase": phase,
        "status": "failed",
        "reason_code": f"{phase}_failed",
        "error_type": failure_code(exc),
    }
    report["status"] = "aborted"
    log.error("pipeline_aborted", phase=phase, outcome="run_aborted", error_type=failure_code(exc))
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
    Digest failures are the exception: they are delegated to `ois.run_digests`,
    which retries only the unfinished periods.
    """
    return _run(run_nightly_pipeline())
