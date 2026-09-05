"""Scheduling: what runs, in what order, and what may be repeated.

The behaviour under test is the periodic part of the system — the part that used
not to exist. `alerts.dispatch` was reachable only from the admin-only
monitoring endpoint and `alerts.run_digests` had no caller at all, so a
deployment that never pressed the button collected data forever and told nobody
about it.

What this file proves, and what it does not:

* **Proved here:** the schedule that is built from configuration, the order the
  phases run in, that a dependent phase is skipped when its prerequisite fails,
  deduplication across repeated runs, who is eligible for a digest, that nothing
  reaches an unverified channel, that one user's delivery failure does not stop
  the next user, and that importing the application starts no scheduler.
* **Not proved here, by construction:** cross-process concurrency. The advisory
  lock is a PostgreSQL transaction lock; on SQLite — which the default suite
  runs on — it reports "acquired" and steps aside. The "another run holds the
  lock" path below is therefore driven by patching the guard, which tests the
  wiring around it and nothing about real contention. The genuine check, two
  independent transactions against a real database, is
  `tests/test_worker_concurrency_postgres.py`.
* **Not proved here, on purpose:** delivery to Telegram or SMTP. `conftest.py`
  blocks sockets, so providers are either the real unconfigured ones (which
  report themselves unconfigured rather than pretending) or fakes registered for
  the duration of one test.
"""

from __future__ import annotations

import json
import os
import subprocess
import sys
from datetime import UTC, datetime, timedelta
from pathlib import Path

import pytest
import pytest_asyncio
import sqlalchemy as sa
from celery.exceptions import Retry
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from app.core.config import get_settings
from app.models.enums import DeliveryStatus, DigestFrequency
from app.models.models import (
    AlertDelivery,
    Digest,
    NotificationChannelLink,
    Source,
    SystemAuditLog,
    User,
    UserProfile,
)
from app.notifications import base as notification_base
from app.notifications.base import NotificationMessage, ProviderResult
from app.workers import tasks
from app.workers.celery_app import celery_app
from app.workers.locks import is_postgresql, lock_key, try_advisory_xact_lock
from app.workers.schedule import (
    build_beat_schedule,
    due_digest_frequencies,
    normalize_weekday,
    schedule_timezone,
)
from tests.test_monitoring_alerts import add_condition, add_observations, make_rule
from tests.test_multi_user import make_opportunity

BACKEND_DIR = Path(__file__).resolve().parents[1]


# --------------------------------------------------------------------- fixtures
@pytest_asyncio.fixture
async def worker_db(engine, monkeypatch):
    """Point the workers' own session factory at this test's database.

    A worker has no request scope to borrow a session from, so the phase
    functions open their own. Replacing the module attribute is what makes them
    run against the test database instead of `DATABASE_URL`.
    """
    maker = async_sessionmaker(engine, expire_on_commit=False, class_=AsyncSession)
    monkeypatch.setattr(tasks, "SessionLocal", maker)
    return maker


@pytest.fixture
def scheduling_enabled(monkeypatch):
    """Opt into the schedule, the way an operator would, for one test.

    `get_settings` is lru_cached, so the cache is cleared on the way in and out
    to keep this from leaking into the rest of the suite.
    """
    monkeypatch.setenv("SCHEDULER_ENABLED", "true")
    get_settings.cache_clear()
    yield get_settings()
    get_settings.cache_clear()


async def make_user(
    session: AsyncSession,
    *,
    email: str,
    digest_frequency: str = DigestFrequency.OFF.value,
    active: bool = True,
) -> User:
    """A user with a profile, because digests are a stored preference."""
    user = User(
        email=email, full_name=email.split("@")[0], password_hash="x", role="analyst", is_active=active
    )
    session.add(user)
    await session.flush()
    session.add(UserProfile(user_id=user.id, digest_frequency=digest_frequency))
    await session.commit()
    return user


async def make_confirmed_opportunity(session: AsyncSession, *, slug: str) -> None:
    """An opportunity whose one measurable condition is satisfied.

    `monitor_all` therefore writes a `confirmation_met` event, which is the fact
    a rule with that trigger is waiting for.
    """
    from app.models.models import Trend

    opp = await make_opportunity(session, slug=slug, title=f"Candidate {slug}", score=78.0)
    trend = await session.get(Trend, opp.primary_trend_id)
    await add_observations(session, trend=trend, signal_type="import_growth", values=[30.0, 40.0])
    await add_condition(
        session,
        opp,
        kind="confirmation",
        description="Imports grow",
        measurable={"signal_type": "import_growth", "comparator": "gt", "value": 10.0},
    )


class FakeChannel:
    """A provider stand-in that records what it was asked to deliver.

    Registered for one test and removed by `monkeypatch`, so the real provider
    registry is never left holding a fake.
    """

    channel = "telegram"

    def __init__(self, failing_addresses: tuple[str, ...] = ()) -> None:
        self.calls: list[tuple[str, str]] = []
        self.failing = set(failing_addresses)
        self.configured = True

    async def send(self, *, address: str, message: NotificationMessage) -> ProviderResult:
        self.calls.append((address, message.title))
        if address in self.failing:
            return ProviderResult(delivered=False, detail="Refused by the fake provider.")
        return ProviderResult(delivered=True, detail="Delivered by the fake provider.", live=True)


@pytest.fixture
def fake_telegram(monkeypatch):
    def _install(failing_addresses: tuple[str, ...] = ()) -> FakeChannel:
        fake = FakeChannel(failing_addresses)
        monkeypatch.setitem(notification_base._REGISTRY, "telegram", fake)  # noqa: SLF001
        return fake

    return _install


# ---------------------------------------------------------------- the schedule
def test_scheduling_is_off_by_default():
    """The default build schedules collection and the janitor, and nothing else.

    Alert dispatch and digests stay where they were — behind an administrator's
    explicit action — until somebody sets `SCHEDULER_ENABLED`.
    """
    schedule = build_beat_schedule(get_settings())
    assert set(schedule) == {"reap-stale-runs", "collect-daily"}
    assert {entry["task"] for entry in schedule.values()} == {"ois.reap_stale_runs", "ois.run_all_sources"}
    assert get_settings().SCHEDULER_ENABLED is False


def test_the_imported_celery_app_carries_the_default_schedule():
    """Not just the builder: the object beat would actually read."""
    assert set(celery_app.conf.beat_schedule) == {"reap-stale-runs", "collect-daily"}
    assert celery_app.conf.timezone == "UTC"
    assert celery_app.conf.enable_utc is True


def test_enabling_scheduling_registers_one_ordered_run(scheduling_enabled):
    """One entry, three phases — and collection is not scheduled twice.

    The pipeline *replaces* `collect-daily` rather than being added beside it,
    because two entries that both collect would crawl every source twice a
    night and burn the rate limits the adapters are careful about.
    """
    schedule = build_beat_schedule(scheduling_enabled)
    assert set(schedule) == {"reap-stale-runs", "nightly-pipeline"}
    assert schedule["nightly-pipeline"]["task"] == "ois.run_nightly_pipeline"
    collectors = [
        e["task"]
        for e in schedule.values()
        if e["task"] in {"ois.run_all_sources", "ois.run_nightly_pipeline"}
    ]
    assert len(collectors) == 1, "collection must be scheduled exactly once"


def test_the_cadence_and_timezone_come_from_configuration(scheduling_enabled, monkeypatch):
    monkeypatch.setenv("COLLECT_HOUR", "22")
    monkeypatch.setenv("COLLECT_MINUTE", "45")
    monkeypatch.setenv("REAP_EVERY_MINUTES", "30")
    monkeypatch.setenv("SCHEDULE_TIMEZONE", "Pacific/Auckland")
    get_settings.cache_clear()
    cfg = get_settings()

    schedule = build_beat_schedule(cfg)
    assert schedule_timezone(cfg) == "Pacific/Auckland"
    entry = schedule["nightly-pipeline"]["schedule"]
    assert str(entry) == "<crontab: 45 22 * * * (m/h/dM/MY/d)>"
    assert str(schedule["reap-stale-runs"]["schedule"]) == "<crontab: */30 * * * * (m/h/dM/MY/d)>"
    get_settings.cache_clear()


def test_an_unusable_timezone_or_day_name_is_refused_loudly(scheduling_enabled, monkeypatch):
    """A typo must stop beat at startup, not silently mean UTC."""
    monkeypatch.setenv("SCHEDULE_TIMEZONE", "Mars/Olympus_Mons")
    get_settings.cache_clear()
    with pytest.raises(ValueError, match="IANA timezone"):
        build_beat_schedule(get_settings())

    monkeypatch.setenv("SCHEDULE_TIMEZONE", "UTC")
    monkeypatch.setenv("DIGEST_WEEKLY_DAY", "someday")
    get_settings.cache_clear()
    with pytest.raises(ValueError, match="DIGEST_WEEKLY_DAY"):
        build_beat_schedule(get_settings())
    get_settings.cache_clear()


def test_weekly_digests_follow_the_local_day_not_the_utc_one():
    """22:00 UTC on a Sunday is already Monday morning in Auckland.

    A weekly digest judged in UTC would land a day late for exactly the
    operators most likely to read it over breakfast.
    """
    instant = datetime(2026, 9, 6, 22, 0, tzinfo=UTC)  # a Sunday in UTC
    assert due_digest_frequencies(now=instant, timezone_name="UTC", weekly_day="sunday") == [
        "daily",
        "weekly",
    ]
    assert due_digest_frequencies(now=instant, timezone_name="UTC", weekly_day="monday") == ["daily"]
    assert due_digest_frequencies(now=instant, timezone_name="Pacific/Auckland", weekly_day="monday") == [
        "daily",
        "weekly",
    ]
    assert normalize_weekday(" Monday ") == "monday"


def test_a_day_name_is_not_a_number_that_two_conventions_disagree_about():
    """cron numbers days from Sunday, Python from Monday. Neither is used."""
    with pytest.raises(ValueError, match="monday"):
        normalize_weekday("0")


# --------------------------------------------------- no scheduler in this process
def test_importing_the_application_starts_no_scheduler():
    """A web worker, a script or a test run must not put a beat thread anywhere.

    Checked in a fresh interpreter: this process has already imported plenty, so
    only a clean one can show what importing does on its own.
    """
    probe = (
        "import json, sys, threading;"
        "import app.main, app.workers.tasks, app.workers.celery_app;"
        "print('PROBE' + json.dumps({"
        "'threads': [t.name for t in threading.enumerate()],"
        "'beat_modules': sorted(m for m in sys.modules if 'beat' in m.lower()),"
        "'timers': [t.name for t in threading.enumerate() if isinstance(t, threading.Timer)],"
        "}))"
    )
    env = {**os.environ, "ENV": "test", "DATABASE_URL": "sqlite+aiosqlite:///:memory:"}
    done = subprocess.run(  # noqa: S603 - a fixed interpreter and a literal script
        [sys.executable, "-c", probe],
        cwd=BACKEND_DIR,
        env=env,
        capture_output=True,
        text=True,
        timeout=180,
        check=False,
    )
    assert done.returncode == 0, done.stderr
    line = next(x for x in done.stdout.splitlines() if x.startswith("PROBE"))
    payload = json.loads(line[len("PROBE") :])
    assert payload["threads"] == ["MainThread"], "importing started a thread"
    assert payload["beat_modules"] == [], "importing pulled in the beat machinery"
    assert payload["timers"] == []


def test_the_beat_schedule_is_data_not_a_running_thing():
    for entry in celery_app.conf.beat_schedule.values():
        assert isinstance(entry["task"], str)
        assert "schedule" in entry


# ------------------------------------------------- monitoring reaches dispatch
async def test_a_scheduled_run_delivers_what_the_monitor_found(session, worker_db):
    """The gap this work closes: events the monitor writes reach a user."""
    user = await make_user(session, email="nightly@example.org")
    await make_confirmed_opportunity(session, slug="sched-1")
    await make_rule(session, user, trigger="confirmation_met", cooldown_hours=0, channels=["in_app"])

    result = await tasks.run_monitoring_phase(now=datetime(2026, 9, 7, 3, 0, tzinfo=UTC))

    assert result["status"] == "ok"
    assert result["events"] == 1, "the condition is measurable and met, so it is news"
    assert result["alerts_sent"] == 1

    deliveries = (await session.execute(sa.select(AlertDelivery))).scalars().all()
    assert len(deliveries) == 1
    assert deliveries[0].user_id == user.id
    assert deliveries[0].status == DeliveryStatus.SENT
    assert deliveries[0].channel == "in_app"


async def test_the_run_leaves_an_audit_trail_of_counts_not_of_content(session, worker_db):
    user = await make_user(session, email="audited@example.org")
    await make_confirmed_opportunity(session, slug="sched-audit")
    await make_rule(session, user, trigger="confirmation_met", cooldown_hours=0)

    await tasks.run_monitoring_phase(now=datetime(2026, 9, 7, 3, 0, tzinfo=UTC))

    rows = (
        (
            await session.execute(
                sa.select(SystemAuditLog).where(SystemAuditLog.action == "monitoring.run.scheduled")
            )
        )
        .scalars()
        .all()
    )
    assert len(rows) == 1
    recorded = rows[0]
    assert recorded.actor_user_id is None, "the schedule is not a person"
    assert recorded.actor_label == "system"
    assert recorded.after["alerts_sent"] == 1
    assert recorded.after["events"] == 1
    # Counts, not copies: an audit log is readable by administrators and must
    # not become a second store of notification bodies.
    serialised = json.dumps(recorded.after)
    assert "Candidate sched-audit" not in serialised
    assert "Big moves" not in serialised


async def test_repeated_runs_over_the_same_facts_add_nothing(session, worker_db):
    """The database constraint, exercised through the scheduled path."""
    user = await make_user(session, email="repeat@example.org")
    await make_confirmed_opportunity(session, slug="sched-repeat")
    # No cooldown: only deduplication can stop the second and third runs.
    await make_rule(session, user, trigger="confirmation_met", cooldown_hours=0)

    now = datetime(2026, 9, 7, 3, 0, tzinfo=UTC)
    first = await tasks.run_monitoring_phase(now=now)
    second = await tasks.run_monitoring_phase(now=now + timedelta(days=1))
    third = await tasks.run_monitoring_phase(now=now + timedelta(days=2))

    assert first["alerts_sent"] == 1
    assert second["alerts_sent"] == 0
    assert third["alerts_sent"] == 0
    assert second["alerts_suppressed"] >= 1
    rows = (await session.execute(sa.select(AlertDelivery))).scalars().all()
    assert len(rows) == 1, "three runs over one fact produced more than one delivery"


async def test_a_run_that_finds_nothing_reports_that_honestly(session, worker_db):
    result = await tasks.run_monitoring_phase(now=datetime(2026, 9, 7, 3, 0, tzinfo=UTC))
    assert result == {
        "phase": "monitoring",
        "status": "ok",
        "opportunities": 0,
        "checks": 0,
        "events": 0,
        "events_considered": 0,
        "alerts_sent": 0,
        "alerts_suppressed": 0,
    }


# ------------------------------------------------------------- delivery limits
async def test_an_unverified_channel_receives_nothing(session, worker_db, fake_telegram):
    """Section 21: a chat id alone must never be written to."""
    fake = fake_telegram()
    user = await make_user(session, email="unverified@example.org")
    await make_confirmed_opportunity(session, slug="sched-unverified")
    await make_rule(session, user, trigger="confirmation_met", cooldown_hours=0, channels=["telegram"])
    session.add(
        NotificationChannelLink(
            user_id=user.id, channel="telegram", external_id="111", verified=False, link_code="pending:abc"
        )
    )
    await session.commit()

    result = await tasks.run_monitoring_phase(now=datetime(2026, 9, 7, 3, 0, tzinfo=UTC))

    assert result["alerts_sent"] == 1, "the in-app fallback always exists"
    assert fake.calls == [], "an unverified link was written to"
    channels = {row.channel for row in (await session.execute(sa.select(AlertDelivery))).scalars()}
    assert channels == {"in_app"}


async def test_a_verified_channel_is_used(session, worker_db, fake_telegram):
    """The control for the test above: verification is the only difference."""
    fake = fake_telegram()
    user = await make_user(session, email="verified@example.org")
    await make_confirmed_opportunity(session, slug="sched-verified")
    await make_rule(session, user, trigger="confirmation_met", cooldown_hours=0, channels=["telegram"])
    session.add(
        NotificationChannelLink(
            user_id=user.id,
            channel="telegram",
            external_id="222",
            verified=True,
            verified_at=datetime(2026, 1, 1, tzinfo=UTC),
        )
    )
    await session.commit()

    result = await tasks.run_monitoring_phase(now=datetime(2026, 9, 7, 3, 0, tzinfo=UTC))

    assert result["alerts_sent"] == 2
    assert [address for address, _ in fake.calls] == ["222"]


async def test_an_unconfigured_provider_records_a_suppression_not_a_lie(session, worker_db):
    """No bot token, no SMTP host: nothing leaves the machine and the row says so."""
    user = await make_user(session, email="unconfigured@example.org")
    await make_confirmed_opportunity(session, slug="sched-unconfigured")
    await make_rule(
        session, user, trigger="confirmation_met", cooldown_hours=0, channels=["telegram", "email"]
    )
    session.add(
        NotificationChannelLink(user_id=user.id, channel="telegram", external_id="333", verified=True)
    )
    await session.commit()

    result = await tasks.run_monitoring_phase(now=datetime(2026, 9, 7, 3, 0, tzinfo=UTC))

    rows = (await session.execute(sa.select(AlertDelivery).order_by(AlertDelivery.channel))).scalars().all()
    by_channel = {row.channel: row for row in rows}
    assert by_channel["in_app"].status == DeliveryStatus.SENT
    assert by_channel["telegram"].status == DeliveryStatus.SUPPRESSED
    assert "No Telegram bot token" in by_channel["telegram"].suppressed_reason
    assert by_channel["email"].status == DeliveryStatus.SUPPRESSED
    assert result["alerts_sent"] == 1


async def test_one_users_delivery_failure_does_not_stop_the_next_user(session, worker_db, fake_telegram):
    """A refusal for one chat must not cost another user their alert.

    This is the isolation the provider interface already buys: a provider
    reports a failed send as data (`delivered=False`) instead of raising, so
    `dispatch` carries on to the next rule and the next person.
    """
    fake = fake_telegram(failing_addresses=("bad-chat",))
    unlucky = await make_user(session, email="unlucky@example.org")
    lucky = await make_user(session, email="lucky@example.org")
    await make_confirmed_opportunity(session, slug="sched-two-users")
    for user, chat in ((unlucky, "bad-chat"), (lucky, "good-chat")):
        await make_rule(
            session,
            user,
            name=f"rule-{chat}",
            trigger="confirmation_met",
            cooldown_hours=0,
            channels=["telegram"],
        )
        session.add(
            NotificationChannelLink(user_id=user.id, channel="telegram", external_id=chat, verified=True)
        )
    await session.commit()

    result = await tasks.run_monitoring_phase(now=datetime(2026, 9, 7, 3, 0, tzinfo=UTC))

    assert sorted(address for address, _ in fake.calls) == ["bad-chat", "good-chat"]
    rows = (await session.execute(sa.select(AlertDelivery))).scalars().all()
    by_user = {row.user_id: row for row in rows if row.channel == "telegram"}
    assert by_user[unlucky.id].status == DeliveryStatus.SUPPRESSED
    assert by_user[lucky.id].status == DeliveryStatus.SENT
    assert len(by_user) == 2, "the user whose chat refused the message cost the other user theirs"
    assert result["alerts_sent"] == 3, "two in-app rows plus the one chat that worked"


# ----------------------------------------------------------------- concurrency
def test_the_lock_key_is_a_stable_bigint():
    assert lock_key("ois.monitoring") == lock_key("ois.monitoring")
    assert lock_key("ois.monitoring") != lock_key("ois.digests:daily")
    assert 0 <= lock_key("ois.monitoring") < 2**63


async def test_the_guard_is_a_no_op_on_sqlite(session):
    """Stated plainly so nobody reads the tests below as proof of concurrency."""
    assert is_postgresql(session) is False
    assert await try_advisory_xact_lock(session, "ois.monitoring") is True


async def test_a_phase_is_skipped_when_another_run_holds_the_lock(session, worker_db, monkeypatch):
    """Mocked: the guard is patched, because SQLite cannot refuse a lock.

    What is being checked is the consequence — no work, no deliveries, no audit
    row, and the dependent phase does not run either.
    """
    user = await make_user(session, email="locked@example.org")
    await make_confirmed_opportunity(session, slug="sched-locked")
    await make_rule(session, user, trigger="confirmation_met", cooldown_hours=0)

    async def refuse(*_args, **_kwargs) -> bool:
        return False

    monkeypatch.setattr(tasks, "try_advisory_xact_lock", refuse)
    result = await tasks.run_monitoring_phase(now=datetime(2026, 9, 7, 3, 0, tzinfo=UTC))

    assert result["status"] == "skipped"
    assert (await session.execute(sa.select(AlertDelivery))).scalars().all() == []
    assert (await session.execute(sa.select(SystemAuditLog))).scalars().all() == []


async def test_an_overlapping_run_stops_before_the_digests(session, worker_db, monkeypatch):
    """Overlapping execution: the second run yields the whole pipeline."""
    calls: list[str] = []

    async def refuse(*_args, **_kwargs) -> bool:
        return False

    async def spy_digests(**kwargs):  # noqa: ANN003
        calls.append("digests")
        return {"phase": "digests", "status": "ok", "frequencies": {}, "failed": [], "skipped": []}

    monkeypatch.setattr(tasks, "try_advisory_xact_lock", refuse)
    monkeypatch.setattr(tasks, "_run_all", _async_returning([]))
    monkeypatch.setattr(tasks, "run_digest_phase", spy_digests)

    report = await tasks.run_nightly_pipeline(now=datetime(2026, 9, 7, 3, 0, tzinfo=UTC))

    assert report["phases"]["monitoring"]["status"] == "skipped"
    assert report["status"] == "skipped"
    assert calls == [], "digests ran while another run owned the night"


async def test_a_monitoring_failure_commits_nothing_and_is_not_retried(session, worker_db, monkeypatch):
    """The delivery guarantee, stated as behaviour.

    The phase rolls back and reports. It does not raise, so Celery marks the
    task successful and does not redeliver it — redelivering a batch that had
    already handed messages to Telegram before it failed would send them again.
    """
    user = await make_user(session, email="failed@example.org")
    await make_confirmed_opportunity(session, slug="sched-failed")
    await make_rule(session, user, trigger="confirmation_met", cooldown_hours=0)

    async def explode(*_args, **_kwargs):  # noqa: ANN002, ANN003
        raise RuntimeError("provider exploded")

    monkeypatch.setattr(tasks, "dispatch", explode)
    result = await tasks.run_monitoring_phase(now=datetime(2026, 9, 7, 3, 0, tzinfo=UTC))

    assert result == {"phase": "monitoring", "status": "failed", "reason": "RuntimeError"}
    assert (await session.execute(sa.select(AlertDelivery))).scalars().all() == []
    assert (await session.execute(sa.select(SystemAuditLog))).scalars().all() == []
    assert tasks.task_run_monitoring.max_retries == 0
    assert tasks.task_run_monitoring.acks_late is False
    assert tasks.task_run_monitoring.reject_on_worker_lost is False


def test_the_tasks_that_send_are_configured_not_to_repeat_a_batch():
    """Read off the task objects, not off a comment."""
    for task in (tasks.task_run_monitoring, tasks.task_run_nightly_pipeline):
        assert task.acks_late is False, f"{task.name} must not be redelivered"
        assert task.reject_on_worker_lost is False
        assert task.max_retries == 0
    assert tasks.task_run_digests.max_retries == 2
    # Ingestion stays as it was: idempotent, so late-acked and retried.
    assert tasks.task_run_all_sources.acks_late is not False
    assert tasks.task_run_nightly_pipeline.time_limit == tasks.PIPELINE_TIME_LIMIT_SECONDS
    assert tasks.task_run_nightly_pipeline.soft_time_limit < tasks.task_run_nightly_pipeline.time_limit


# -------------------------------------------------------------------- ordering
def _async_returning(value):
    async def _stub(*_args, **_kwargs):  # noqa: ANN002, ANN003
        return value

    return _stub


async def test_digests_wait_for_the_monitoring_they_summarise(session, worker_db, monkeypatch):
    calls: list[str] = []

    async def failing_monitor(**_kwargs):  # noqa: ANN003
        calls.append("monitoring")
        return {"phase": "monitoring", "status": "failed", "reason": "RuntimeError"}

    async def spy_digests(**_kwargs):  # noqa: ANN003
        calls.append("digests")
        return {"phase": "digests", "status": "ok", "frequencies": {}, "failed": [], "skipped": []}

    monkeypatch.setattr(tasks, "_run_all", _async_returning([]))
    monkeypatch.setattr(tasks, "run_monitoring_phase", failing_monitor)
    monkeypatch.setattr(tasks, "run_digest_phase", spy_digests)

    report = await tasks.run_nightly_pipeline(now=datetime(2026, 9, 7, 3, 0, tzinfo=UTC))

    assert calls == ["monitoring"], "a digest was written over a monitoring run that failed"
    assert report["status"] == "aborted"
    assert "digests" not in report["phases"]


async def test_a_collection_failure_stops_the_whole_run(session, worker_db, monkeypatch):
    calls: list[str] = []

    async def explode(*_args, **_kwargs):  # noqa: ANN002, ANN003
        raise RuntimeError("database unreachable")

    async def spy(*_args, **_kwargs):  # noqa: ANN002, ANN003
        calls.append("ran")
        return {"phase": "x", "status": "ok"}

    monkeypatch.setattr(tasks, "_run_all", explode)
    monkeypatch.setattr(tasks, "run_monitoring_phase", spy)
    monkeypatch.setattr(tasks, "run_digest_phase", spy)

    report = await tasks.run_nightly_pipeline(now=datetime(2026, 9, 7, 3, 0, tzinfo=UTC))

    assert calls == []
    assert report["status"] == "aborted"
    assert report["phases"]["collect"]["reason"] == "RuntimeError"


async def test_one_bad_source_does_not_cost_the_night(session, worker_db, monkeypatch):
    """Per-source failure is normal and recorded; it is not a reason to skip alerts."""
    user = await make_user(session, email="partial@example.org", digest_frequency=DigestFrequency.DAILY.value)
    await make_confirmed_opportunity(session, slug="sched-partial")
    await make_rule(session, user, trigger="confirmation_met", cooldown_hours=0)

    monkeypatch.setattr(
        tasks, "_run_all", _async_returning([{"source": "broken", "error": "HTTP 503"}, {"source": "fine"}])
    )
    report = await tasks.run_nightly_pipeline(now=datetime(2026, 9, 7, 3, 0, tzinfo=UTC))

    assert report["phases"]["collect"] == {
        "phase": "collect",
        "status": "partial",
        "sources": 2,
        "failed_sources": 1,
    }
    assert report["phases"]["monitoring"]["alerts_sent"] == 1
    assert report["status"] == "degraded", "the run finished, but the operator should see the bad source"
    assert (await session.execute(sa.select(Digest))).scalars().all(), "digests still owed to the user"


async def test_the_whole_ordered_run_end_to_end(session, worker_db):
    """No mocks: collection (nothing enabled), monitoring, alerts, digests."""
    daily_user = await make_user(
        session, email="daily@example.org", digest_frequency=DigestFrequency.DAILY.value
    )
    await make_confirmed_opportunity(session, slug="sched-pipeline")
    await make_rule(session, daily_user, trigger="confirmation_met", cooldown_hours=0)
    # The helper above creates a source to hang its observations on, and sources
    # ship enabled. Disabling it keeps the collection phase empty: this test is
    # about ordering, and ingestion is covered in test_ingestion.py.
    await session.execute(sa.update(Source).values(enabled=False))
    await session.commit()

    now = datetime(2026, 9, 7, 3, 0, tzinfo=UTC)  # a Monday
    report = await tasks.run_nightly_pipeline(now=now)

    assert [name for name in report["phases"]] == ["collect", "monitoring", "digests"]
    assert report["status"] == "ok"
    assert report["phases"]["monitoring"]["alerts_sent"] == 1
    # Monday, and the default weekly day is Sunday: one daily digest only.
    assert report["phases"]["digests"]["frequencies"] == {"daily": 1}
    digests = (await session.execute(sa.select(Digest))).scalars().all()
    assert [d.frequency for d in digests] == [DigestFrequency.DAILY.value]
    assert digests[0].user_id == daily_user.id
    assert (await session.execute(sa.select(AlertDelivery))).scalars().all()


async def test_the_weekly_digest_joins_the_run_on_its_local_day(session, worker_db):
    weekly_user = await make_user(
        session, email="weekly@example.org", digest_frequency=DigestFrequency.WEEKLY.value
    )
    daily_user = await make_user(
        session, email="everyday@example.org", digest_frequency=DigestFrequency.DAILY.value
    )

    sunday = datetime(2026, 9, 6, 3, 0, tzinfo=UTC)
    report = await tasks.run_nightly_pipeline(now=sunday)

    assert set(report["phases"]["digests"]["frequencies"]) == {"daily", "weekly"}
    rows = (await session.execute(sa.select(Digest))).scalars().all()
    assert {(row.user_id, row.frequency) for row in rows} == {
        (daily_user.id, DigestFrequency.DAILY.value),
        (weekly_user.id, DigestFrequency.WEEKLY.value),
    }


# --------------------------------------------------------------------- digests
async def test_only_users_who_asked_for_a_frequency_get_one(session, worker_db):
    asked_daily = await make_user(
        session, email="d1@example.org", digest_frequency=DigestFrequency.DAILY.value
    )
    asked_weekly = await make_user(
        session, email="w1@example.org", digest_frequency=DigestFrequency.WEEKLY.value
    )
    asked_nothing = await make_user(
        session, email="off@example.org", digest_frequency=DigestFrequency.OFF.value
    )
    switched_off = await make_user(
        session, email="gone@example.org", digest_frequency=DigestFrequency.DAILY.value, active=False
    )
    no_profile = User(
        email="noprofile@example.org", full_name="No profile", password_hash="x", role="analyst"
    )
    session.add(no_profile)
    await session.commit()

    result = await tasks.run_digest_phase(frequencies=["daily"], now=datetime(2026, 9, 7, 3, 0, tzinfo=UTC))

    assert result["status"] == "ok"
    assert result["frequencies"] == {"daily": 1}
    rows = (await session.execute(sa.select(Digest))).scalars().all()
    assert [row.user_id for row in rows] == [asked_daily.id]
    assert asked_weekly.id not in {row.user_id for row in rows}
    assert asked_nothing.id not in {row.user_id for row in rows}, "a user who asked for nothing was sent one"
    assert switched_off.id not in {row.user_id for row in rows}, "a deactivated account was summarised"
    assert no_profile.id not in {row.user_id for row in rows}


async def test_a_frequency_that_is_not_a_period_is_refused(session, worker_db):
    """Permanent errors fail loudly instead of being retried or ignored."""
    with pytest.raises(ValueError, match="cannot be scheduled"):
        await tasks.run_digest_phase(frequencies=["off"], now=datetime(2026, 9, 7, 3, 0, tzinfo=UTC))
    with pytest.raises(ValueError, match="cannot be scheduled"):
        await tasks.run_digest_phase(frequencies=["hourly"], now=datetime(2026, 9, 7, 3, 0, tzinfo=UTC))
    assert (await session.execute(sa.select(Digest))).scalars().all() == []


async def test_a_failed_period_is_rolled_back_and_the_other_survives(session, worker_db, monkeypatch):
    """Daily is written, weekly fails: the daily digest must not be lost."""
    await make_user(session, email="twoperiods@example.org", digest_frequency=DigestFrequency.DAILY.value)
    real = tasks.run_digests

    async def flaky(session_, *, frequency, now=None):  # noqa: ANN001
        if frequency == "weekly":
            raise RuntimeError("weekly blew up")
        return await real(session_, frequency=frequency, now=now)

    monkeypatch.setattr(tasks, "run_digests", flaky)
    result = await tasks.run_digest_phase(
        frequencies=["daily", "weekly"], now=datetime(2026, 9, 6, 3, 0, tzinfo=UTC)
    )

    assert result["status"] == "partial"
    assert result["frequencies"] == {"daily": 1, "weekly": "failed"}
    assert result["failed"] == ["weekly"]
    rows = (await session.execute(sa.select(Digest))).scalars().all()
    assert [row.frequency for row in rows] == ["daily"], (
        "the committed period was rolled back with the failed one"
    )


def test_a_transient_digest_failure_is_retried_with_backoff_and_then_stops(monkeypatch):
    """Bounded, exponential, and only over the frequencies that rolled back.

    A synchronous test on purpose: the task body calls `asyncio.run`, which
    cannot be nested inside the loop an `async def` test already owns. Celery's
    eager path runs the real retry machinery without contacting a broker, and
    ignores the countdown by design — the delay itself is asserted below.
    """
    attempts: list[list[str]] = []

    async def always_fail(*, frequencies=None, now=None):  # noqa: ANN001
        attempts.append(list(frequencies or []))
        return {
            "phase": "digests",
            "status": "failed",
            "frequencies": {f: "failed" for f in attempts[-1]},
            "failed": list(attempts[-1]),
            "skipped": [],
        }

    monkeypatch.setattr(tasks, "run_digest_phase", always_fail)
    result = tasks.task_run_digests.apply(kwargs={"frequencies": ["daily", "weekly"]})

    assert result.state == "FAILURE"
    assert isinstance(result.result, tasks.DigestPhaseError)
    assert attempts == [["daily", "weekly"]] * 3, "not bounded at max_retries=2"

    # And the successful path: one failure, then a retry that writes them.
    attempts.clear()
    calls = {"n": 0}

    async def fail_once(*, frequencies=None, now=None):  # noqa: ANN001
        calls["n"] += 1
        attempts.append(list(frequencies or []))
        if calls["n"] == 1:
            return {
                "phase": "digests",
                "status": "failed",
                "frequencies": {"weekly": "failed"},
                "failed": ["weekly"],
                "skipped": [],
            }
        return {"phase": "digests", "status": "ok", "frequencies": {"weekly": 1}, "failed": [], "skipped": []}

    monkeypatch.setattr(tasks, "run_digest_phase", fail_once)
    recovered = tasks.task_run_digests.apply(kwargs={"frequencies": ["daily", "weekly"]})
    assert recovered.state == "SUCCESS"
    assert recovered.result["status"] == "ok"
    assert attempts == [["daily", "weekly"], ["weekly"]], "the retry re-ran a period that had committed"


def test_the_retry_countdown_grows_and_is_capped(monkeypatch):
    recorded: dict = {}

    def fake_retry(**kwargs):  # noqa: ANN003
        recorded.update(kwargs)
        raise Retry("stop here")

    async def failing(*, frequencies=None, now=None):  # noqa: ANN001
        return {"phase": "digests", "status": "failed", "frequencies": {}, "failed": ["daily"], "skipped": []}

    monkeypatch.setattr(tasks, "run_digest_phase", failing)
    monkeypatch.setattr(tasks.task_run_digests, "retry", fake_retry)
    with pytest.raises(Retry):
        tasks.task_run_digests.run(frequencies=["daily"])
    assert recorded["countdown"] == tasks.DIGEST_RETRY_BASE_SECONDS
    assert recorded["kwargs"] == {"frequencies": ["daily"]}, "the retry must not repeat committed work"

    # Third attempt: past the bound, so it fails instead of retrying again.
    tasks.task_run_digests.push_request(retries=tasks.task_run_digests.max_retries)
    try:
        with pytest.raises(tasks.DigestPhaseError):
            tasks.task_run_digests.run(frequencies=["daily"])
    finally:
        tasks.task_run_digests.pop_request()


async def test_a_digest_run_reports_what_it_wrote(session, worker_db):
    await make_user(session, email="reported@example.org", digest_frequency=DigestFrequency.DAILY.value)
    result = await tasks.run_digest_phase(frequencies=["daily"], now=datetime(2026, 9, 7, 3, 0, tzinfo=UTC))
    assert result == {
        "phase": "digests",
        "status": "ok",
        "frequencies": {"daily": 1},
        "failed": [],
        "skipped": [],
    }


# ------------------------------------------------------- digest content rules
async def test_a_scheduled_digest_keeps_watchlist_scoping_and_the_disclaimer(session, worker_db):
    """The digest written by the schedule is the same one the API writes."""
    from app.models.models import Watchlist, WatchlistItem
    from app.services.monitoring import detect_changes

    run_at = datetime(2026, 9, 7, 3, 0, tzinfo=UTC)
    user = await make_user(session, email="scoped@example.org", digest_frequency=DigestFrequency.DAILY.value)
    watched = await make_opportunity(session, slug="digest-watched", title="Watched", score=80.0)
    other = await make_opportunity(session, slug="digest-other", title="Not watched", score=80.0)
    watchlist = Watchlist(user_id=user.id, name="Mine")
    session.add(watchlist)
    await session.flush()
    session.add(WatchlistItem(watchlist_id=watchlist.id, item_type="opportunity", opportunity_id=watched.id))
    for opp in (watched, other):
        await detect_changes(session, opportunity=opp, previous={"opportunity_score": 50.0}, now=run_at)
    await session.commit()

    await tasks.run_digest_phase(frequencies=["daily"], now=run_at)

    digest = (await session.execute(sa.select(Digest))).scalars().one()
    assert [i["title"] for i in digest.sections["watchlist_changes"]] == ["Watched"]
    assert [i["title"] for i in digest.sections["other_changes"]] == ["Not watched"]
    assert "not a recommendation" in digest.sections["note"]
    assert digest.period_end - digest.period_start == timedelta(days=1)
    assert digest.item_count > 0, "an empty digest would be indistinguishable from a broken pipeline"
