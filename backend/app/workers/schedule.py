"""What runs when, and in which timezone.

This module is data and arithmetic only. It builds the Celery beat schedule from
configuration; it never starts anything. `celery beat` is a separate process (the
`beat` service in docker-compose.yml), the API does not import this module, and
importing it in a test does not put a scheduler thread in the background.

Two rules shape the schedule:

* **One ordered run, not three hopeful cron times.** Digests summarise what
  monitoring found, and monitoring reads what collection stored. Expressing that
  as "collection at 03:00, monitoring at 04:00, digests at 05:00" only works on
  days when collection finishes in an hour; on a day when it does not, the later
  jobs quietly summarise yesterday's data. So beat schedules a single entry whose
  task runs the phases in order and stops at the first one that fails.
* **Nothing new is scheduled until somebody asks for it.** With
  `SCHEDULER_ENABLED` false the schedule is byte-for-byte the old one: nightly
  collection and the stale-run janitor. Turning it on *replaces* the collection
  entry with the ordered run — it does not add a second collection job alongside
  it — so a deployment cannot end up crawling its sources twice a night because
  two entries both decided to.
"""

from __future__ import annotations

from datetime import UTC, datetime
from typing import TYPE_CHECKING
from zoneinfo import ZoneInfo

from celery.schedules import crontab

from app.core.logging import get_logger
from app.models.enums import DigestFrequency

if TYPE_CHECKING:  # pragma: no cover - typing only
    from app.core.config import Settings

log = get_logger("worker.schedule")

#: Day names in `datetime.weekday()` order. A name, not a number: cron numbers
#: days 0=Sunday, Python numbers them 0=Monday, and a configuration value that
#: could be read either way is a weekly digest that lands on the wrong day.
WEEKDAYS: tuple[str, ...] = (
    "monday",
    "tuesday",
    "wednesday",
    "thursday",
    "friday",
    "saturday",
    "sunday",
)


def normalize_weekday(name: str) -> str:
    """Validate a configured day name loudly, at startup rather than at 03:00."""
    day = str(name or "").strip().lower()
    if day not in WEEKDAYS:
        raise ValueError(f"DIGEST_WEEKLY_DAY must be one of {', '.join(WEEKDAYS)}; got {name!r}.")
    return day


def schedule_timezone(settings: Settings) -> str:
    """The IANA name every crontab is evaluated in.

    Resolved here so a typo fails when beat and the worker start, with the
    offending value in the message, instead of being interpreted as UTC or
    raising from inside the scheduler at the first tick.
    """
    name = str(settings.SCHEDULE_TIMEZONE or "").strip() or "UTC"
    try:
        ZoneInfo(name)
    except Exception as exc:  # noqa: BLE001 - report the value, not the internals
        raise ValueError(
            f"SCHEDULE_TIMEZONE must be an IANA timezone name (e.g. 'Europe/Berlin'); "
            f"got {settings.SCHEDULE_TIMEZONE!r}: {exc}"
        ) from exc
    return name


def due_digest_frequencies(
    *,
    now: datetime | None = None,
    timezone_name: str = "UTC",
    weekly_day: str = "sunday",
) -> list[str]:
    """Which digest frequencies this run owes, judged on the *local* day.

    Daily digests are owed every run. A weekly digest is owed on the run that
    falls on the configured local day, which is why the day is resolved in
    `timezone_name` rather than in UTC: for an operator in Auckland, a Sunday
    digest generated at 03:00 UTC is a Monday morning surprise.
    """
    day = normalize_weekday(weekly_day)
    local = (now or datetime.now(UTC)).astimezone(ZoneInfo(timezone_name))
    due = [DigestFrequency.DAILY.value]
    if WEEKDAYS[local.weekday()] == day:
        due.append(DigestFrequency.WEEKLY.value)
    return due


def build_beat_schedule(settings: Settings) -> dict:
    """The periodic entries beat is given, for one configuration."""
    reap_every = int(settings.REAP_EVERY_MINUTES)
    if not 1 <= reap_every <= 59:
        raise ValueError(f"REAP_EVERY_MINUTES must be between 1 and 59; got {reap_every}.")

    schedule: dict = {
        # The janitor. Unrelated to notifications, so it is scheduled whether or
        # not the notification schedule is enabled.
        "reap-stale-runs": {
            "task": "ois.reap_stale_runs",
            "schedule": crontab(minute=f"*/{reap_every}"),
        },
    }

    nightly = crontab(hour=int(settings.COLLECT_HOUR), minute=int(settings.COLLECT_MINUTE))
    if settings.SCHEDULER_ENABLED:
        # Validated here so a bad timezone or day name stops beat at startup.
        schedule_timezone(settings)
        normalize_weekday(settings.DIGEST_WEEKLY_DAY)
        # One entry, three phases, run in order by the task. Queue comes from
        # `task_routes`, not from here, so there is one place that says where
        # each task runs.
        schedule["nightly-pipeline"] = {
            "task": "ois.run_nightly_pipeline",
            "schedule": nightly,
        }
    else:
        schedule["collect-daily"] = {"task": "ois.run_all_sources", "schedule": nightly}
    return schedule
