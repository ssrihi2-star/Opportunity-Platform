"""Canonical digest periods: what a digest covers, and why it cannot cover it twice.

Before this, a digest period was whatever the clock said when the task ran:
`period_start = now - window`, `period_end = now`. Two things followed, and both
were defects rather than design. A repeated run — a retry, a redelivered message,
an operator pressing the button twice — wrote a **second row for the same
period**, because `digests` had no uniqueness at all. And a retry that crossed
midnight summarised a different 24 hours than the attempt it was retrying, while
claiming to be the same digest.

What this file proves:

* **Period math.** A daily period is the local calendar day that closed before
  the run; a weekly period is the ISO week that closed before the run — the week,
  not the day the task happened to land on. Both are judged on the configured
  wall clock, and both survive a clock change: a 23-hour day is still one
  calendar day, a 167-hour week is still one calendar week.
* **Stable identity.** The period's key and boundaries travel through the broker
  and back, so a retry writes the period it was scheduled for over the same
  content window — after midnight, and after a timezone reconfiguration.
* **Idempotency.** The same period written twice produces one row and one counted
  duplicate; different periods for the same user are different digests.
* **Compatibility.** Rows written before the column existed, and on-demand
  digests from `POST /me/digests`, keep a NULL key and stay exactly as repeatable
  as they were.
* **Migration.** `0007` upgrades a genuinely pre-0007 table, preserves every
  historical row, is safe to re-run, and reverses.

What it does not prove: real cross-process concurrency (that is
`tests/test_worker_concurrency_postgres.py`, and it is skipped without
PostgreSQL), and delivery of a digest to Telegram or email — a digest is still a
row in the database that the user reads in the application. Sending it is a
separate, deliberate follow-up.
"""

from __future__ import annotations

import json
import uuid
from datetime import UTC, datetime, timedelta
from zoneinfo import ZoneInfo

import pytest
import sqlalchemy as sa
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

from app.models.enums import DigestFrequency
from app.models.models import Base, Digest, User, UserProfile, Watchlist, WatchlistItem
from app.services import alerts
from app.services.alerts import DigestPeriod, build_digest, digest_period, run_digests
from app.services.monitoring import detect_changes
from app.workers import tasks
from tests.test_multi_user import make_opportunity
from tests.test_worker_scheduling import make_user

MONDAY_0300 = datetime(2026, 9, 7, 3, 0, tzinfo=UTC)  # a Monday, three hours in


@pytest.fixture
def worker_db(engine, monkeypatch):
    """Point the workers' own session factory at this test's database."""
    maker = async_sessionmaker(engine, expire_on_commit=False, class_=AsyncSession)
    monkeypatch.setattr(tasks, "SessionLocal", maker)
    return maker


def _hours(period: DigestPeriod) -> float:
    """Absolute length of a period. A DST day is not 24 hours and should not be.

    Compared in UTC on purpose. CPython subtracts two aware datetimes that share
    a `tzinfo` object as if both were naive, so `period.end - period.start`
    reports 24 hours for a 23-hour day — the arithmetic hides the very clock
    change the test exists to check.
    """
    return (period.end.astimezone(UTC) - period.start.astimezone(UTC)).total_seconds() / 3600


# ------------------------------------------------------------------ period math
def test_a_daily_period_is_the_day_that_closed_before_the_run():
    period = digest_period("daily", now=MONDAY_0300, timezone_name="UTC")

    assert period is not None
    assert period.key == "daily:2026-09-06", "the day that ended three hours ago"
    assert period.start == datetime(2026, 9, 6, tzinfo=UTC)
    assert period.end == datetime(2026, 9, 7, tzinfo=UTC)
    assert _hours(period) == 24
    assert period.timezone == "UTC"


def test_the_period_is_half_open_so_neither_day_is_counted_twice():
    """`[start, end)`: an event at exactly midnight belongs to the next period.

    Half-open is what makes consecutive periods tile the timeline without a gap
    or an overlap. An inclusive end would put a midnight event in two digests.
    """
    first = digest_period("daily", now=datetime(2026, 9, 7, 3, 0, tzinfo=UTC))
    second = digest_period("daily", now=datetime(2026, 9, 8, 3, 0, tzinfo=UTC))

    assert first.end == second.start, "no gap and no overlap between neighbours"
    assert first.key == "daily:2026-09-06"
    assert second.key == "daily:2026-09-07"


def test_a_weekly_period_names_the_week_not_the_day_it_ran_on():
    """The whole point of the key: a week is a week, whoever writes it.

    A run at Monday 03:00 and a catch-up run on Wednesday both name the ISO week
    that closed on Monday, so they collide on the unique index — which is what
    stops a late run from giving the user a second weekly digest.
    """
    from_monday = digest_period("weekly", now=MONDAY_0300, timezone_name="UTC")
    from_wednesday = digest_period("weekly", now=datetime(2026, 9, 9, 3, 0, tzinfo=UTC))
    previous_week = digest_period("weekly", now=datetime(2026, 8, 30, 3, 0, tzinfo=UTC))

    assert from_monday.key == "weekly:2026-W36"
    assert from_monday.start == datetime(2026, 8, 31, tzinfo=UTC), "Monday 00:00"
    assert from_monday.end == datetime(2026, 9, 7, tzinfo=UTC), "the next Monday 00:00"
    assert from_wednesday == from_monday, "the same week, named the same way"
    # A Sunday run names the week that closed on the Monday before it: on 30
    # August the week of the 24th-31st has not finished yet. This is exactly why
    # the configured weekly day defaults to Monday.
    assert previous_week.key == "weekly:2026-W34"
    assert previous_week.end == datetime(2026, 8, 24, tzinfo=UTC)


def test_a_run_at_monday_midnight_belongs_to_the_week_that_just_closed():
    """The boundary itself, where an off-by-one would silently double-count."""
    just_before = digest_period("weekly", now=datetime(2026, 9, 6, 23, 59, 59, tzinfo=UTC))
    just_after = digest_period("weekly", now=datetime(2026, 9, 7, 0, 0, 0, tzinfo=UTC))

    assert just_before.key == "weekly:2026-W35"
    assert just_after.key == "weekly:2026-W36"
    assert just_after.start == just_before.end


def test_the_daily_boundary_is_midnight_not_the_run_time():
    just_before = digest_period("daily", now=datetime(2026, 9, 6, 23, 59, 59, tzinfo=UTC))
    just_after = digest_period("daily", now=datetime(2026, 9, 7, 0, 0, 0, tzinfo=UTC))

    assert just_before.key == "daily:2026-09-05"
    assert just_after.key == "daily:2026-09-06"


def test_the_period_is_judged_on_the_configured_wall_clock():
    """22:00 UTC on the 6th is already the 7th in Auckland, and still the 6th in UTC.

    Two honest answers to one instant. Which one the user gets is a configuration
    decision, not an accident of where the database server happens to be.
    """
    instant = datetime(2026, 9, 6, 22, 0, tzinfo=UTC)
    in_utc = digest_period("daily", now=instant, timezone_name="UTC")
    in_auckland = digest_period("daily", now=instant, timezone_name="Pacific/Auckland")

    assert in_utc.key == "daily:2026-09-05"
    assert in_auckland.key == "daily:2026-09-06"
    assert in_auckland.start == datetime(2026, 9, 6, tzinfo=ZoneInfo("Pacific/Auckland"))
    assert in_auckland.end == datetime(2026, 9, 7, tzinfo=ZoneInfo("Pacific/Auckland"))
    assert _hours(in_auckland) == 24


def test_a_day_that_is_not_24_hours_is_still_one_calendar_day():
    """DST moves the instant a period starts, never the day it covers."""
    spring = digest_period(
        "daily", now=datetime(2026, 3, 9, 12, 0, tzinfo=UTC), timezone_name="America/New_York"
    )
    autumn = digest_period(
        "daily", now=datetime(2026, 11, 2, 12, 0, tzinfo=UTC), timezone_name="America/New_York"
    )

    assert spring.key == "daily:2026-03-08"
    assert _hours(spring) == 23, "the day the clock jumped forward"
    assert autumn.key == "daily:2026-11-01"
    assert _hours(autumn) == 25, "the day the clock fell back"
    for period in (spring, autumn):
        assert period.start.hour == 0 and period.end.hour == 0
        assert period.end.date() - period.start.date() == timedelta(days=1)


def test_a_week_spanning_a_clock_change_is_still_one_calendar_week():
    """Auckland's clocks change on 27 September 2026, inside the week that starts on the 21st."""
    period = digest_period(
        "weekly", now=datetime(2026, 9, 28, 3, 0, tzinfo=UTC), timezone_name="Pacific/Auckland"
    )
    start = datetime(2026, 9, 21, tzinfo=ZoneInfo("Pacific/Auckland"))

    assert period.key == f"weekly:{start.isocalendar().year}-W{start.isocalendar().week:02d}"
    assert period.start == start
    assert period.end == datetime(2026, 9, 28, tzinfo=ZoneInfo("Pacific/Auckland"))
    assert _hours(period) == 167, "seven calendar days, one hour short"


def test_a_frequency_that_is_not_a_period_has_no_period():
    """`off` is a preference, not a cadence, and must not be scheduled as one."""
    assert digest_period(DigestFrequency.OFF.value, now=MONDAY_0300) is None
    assert digest_period("hourly", now=MONDAY_0300) is None


def test_an_unusable_timezone_is_refused_rather_than_read_as_utc():
    """Silently falling back to UTC would move every user's period boundary."""
    with pytest.raises(KeyError):  # ZoneInfoNotFoundError is a KeyError
        digest_period("daily", now=MONDAY_0300, timezone_name="Mars/Olympus_Mons")


# ----------------------------------------------------------------- the wire form
def test_a_period_survives_the_broker_unchanged():
    """The retry crosses a JSON boundary, so the identity has to be serializable.

    If the boundaries were lost or rounded on the way out, the retry would
    recompute them from its own clock — exactly the defect the key exists to
    prevent.
    """
    period = digest_period("weekly", now=MONDAY_0300, timezone_name="Pacific/Auckland")
    wire = period.to_dict()

    assert json.loads(json.dumps(wire)) == wire, "not JSON-safe"
    assert set(wire) == {"frequency", "key", "start", "end", "timezone"}

    restored = DigestPeriod.from_dict(json.loads(json.dumps(wire)))
    assert restored == period
    assert restored.start == period.start and restored.end == period.end
    assert restored.timezone == "Pacific/Auckland"


# ----------------------------------------------------------------- idempotency
async def test_the_same_period_written_twice_is_one_digest(session, worker_db):
    """The property that used not to exist at all: nothing could object."""
    user = await make_user(session, email="twice@example.org", digest_frequency=DigestFrequency.DAILY.value)
    period = digest_period("daily", now=MONDAY_0300, timezone_name="UTC")

    first = await run_digests(session, frequency="daily", period=period)
    await session.commit()
    second = await run_digests(session, frequency="daily", period=period)
    await session.commit()

    assert (first.written, first.duplicates, first.failed) == (1, 0, 0)
    assert (second.written, second.duplicates, second.failed) == (0, 1, 0), (
        "a repeat was counted as work instead of as the duplicate it is"
    )
    rows = (await session.execute(sa.select(Digest))).scalars().all()
    assert len(rows) == 1
    assert rows[0].user_id == user.id
    assert rows[0].period_key == "daily:2026-09-06"


async def test_two_periods_for_the_same_user_are_two_digests(session, worker_db):
    """Uniqueness is per period, not per user: dedup must not become suppression."""
    user = await make_user(
        session, email="two-periods@example.org", digest_frequency=DigestFrequency.DAILY.value
    )
    saturday = digest_period("daily", now=datetime(2026, 9, 6, 3, 0, tzinfo=UTC))
    sunday = digest_period("daily", now=MONDAY_0300)

    first = await run_digests(session, frequency="daily", period=saturday)
    second = await run_digests(session, frequency="daily", period=sunday)
    await session.commit()

    assert (first.written, second.written) == (1, 1)
    assert (first.duplicates, second.duplicates) == (0, 0)
    rows = (await session.execute(sa.select(Digest).where(Digest.user_id == user.id))).scalars().all()
    assert {row.period_key for row in rows} == {"daily:2026-09-05", "daily:2026-09-06"}


async def test_a_daily_and_a_weekly_digest_for_the_same_day_coexist(session, worker_db):
    """The frequency is part of the identity, so a Monday's two digests are two rows.

    Built directly rather than through `run_digests`, which selects users by their
    stored preference: one user asks for one frequency, and this test is about the
    index, not about eligibility.
    """
    user = await make_user(
        session, email="both-frequencies@example.org", digest_frequency=DigestFrequency.DAILY.value
    )
    for frequency in ("daily", "weekly"):
        period = digest_period(frequency, now=MONDAY_0300, timezone_name="UTC")
        written = await build_digest(session, user=user, frequency=frequency, period=period)
        assert written is not None
    await session.commit()

    rows = (await session.execute(sa.select(Digest).where(Digest.user_id == user.id))).scalars().all()
    assert {(row.frequency, row.period_key) for row in rows} == {
        ("daily", "daily:2026-09-06"),
        ("weekly", "weekly:2026-W36"),
    }


async def test_a_legacy_row_without_a_key_does_not_block_the_scheduled_one(session, worker_db):
    """History is preserved by leaving it alone: NULL never collides with a key."""
    user = await make_user(session, email="legacy@example.org", digest_frequency=DigestFrequency.DAILY.value)
    session.add(
        Digest(
            user_id=user.id,
            frequency="daily",
            period_start=datetime(2026, 9, 6, tzinfo=UTC),
            period_end=datetime(2026, 9, 7, tzinfo=UTC),
            period_key=None,  # written before the column existed
            sections={"period": {"frequency": "daily"}, "note": "historical"},
            item_count=0,
            generated_at=datetime(2026, 9, 6, tzinfo=UTC),
        )
    )
    await session.commit()

    outcome = await run_digests(
        session, frequency="daily", period=digest_period("daily", now=MONDAY_0300, timezone_name="UTC")
    )
    await session.commit()

    assert outcome.written == 1
    assert outcome.duplicates == 0, "a NULL key was treated as equal to a real one"
    rows = (await session.execute(sa.select(Digest))).scalars().all()
    assert len(rows) == 2
    assert sorted(row.period_key or "(null)" for row in rows) == ["(null)", "daily:2026-09-06"]


def test_sqlite_cannot_model_a_crash_after_a_released_savepoint(tmp_path):
    """A recorded limitation, not a behaviour anyone wants.

    The property worth proving is that an attempt which dies before its commit
    reserves nothing — the next attempt writes the period for the first time, and
    the user is not left short a digest. On PostgreSQL that is a transaction
    rollback, and `tests/test_worker_concurrency_postgres.py` proves it against a
    real database.

    On SQLite it cannot be modelled at all. The pysqlite driver only opens a
    transaction implicitly before DML, so a SAVEPOINT statement runs outside one;
    the insert a released savepoint leaves behind is therefore not inside any
    transaction that `close()` or `rollback()` could discard. This test pins that
    fact, because the caveat is documented in `app/services/alerts.py` and a
    future reader deserves to know it was measured rather than assumed. The
    isolation the code depends on — a *failed* savepoint discarding its own rows
    — does hold on SQLite, and `tests/test_alert_isolation.py` proves it.
    """
    import asyncio

    from app.services.alerts import DigestPeriod

    async def measure() -> int:
        engine = create_async_engine(f"sqlite+aiosqlite:///{tmp_path / 'crash.db'}")
        try:
            async with engine.begin() as conn:
                await conn.run_sync(Base.metadata.create_all)
            maker = async_sessionmaker(engine, expire_on_commit=False, class_=AsyncSession)
            async with maker() as setup:
                user = User(
                    email="crashed@example.org",
                    full_name="Crashed",
                    password_hash="x",
                    role="analyst",
                    is_active=True,
                )
                setup.add(user)
                await setup.flush()
                setup.add(UserProfile(user_id=user.id, digest_frequency=DigestFrequency.DAILY.value))
                await setup.commit()
                user_id = user.id

            period = DigestPeriod(
                frequency="daily",
                key="daily:2026-09-06",
                start=datetime(2026, 9, 6, tzinfo=UTC),
                end=datetime(2026, 9, 7, tzinfo=UTC),
                timezone="UTC",
            )
            died = maker()
            outcome = await run_digests(died, frequency="daily", period=period)
            assert outcome.written == 1
            await died.close()  # "the process died here" — on SQLite, it did not

            async with maker() as direct:
                direct.add(
                    Digest(
                        user_id=user_id,
                        frequency="weekly",
                        period_start=period.start,
                        period_end=period.end,
                        period_key="weekly:2026-W36",
                        sections={},
                        item_count=0,
                        generated_at=MONDAY_0300,
                    )
                )
                await direct.flush()
            await direct.close()  # no savepoint involved: this one really is discarded

            async with engine.connect() as observer:
                keys = sorted(
                    str(row[0]) for row in (await observer.execute(sa.select(Digest.period_key))).fetchall()
                )
            return keys
        finally:
            await engine.dispose()

    keys = asyncio.run(measure())
    assert keys == ["daily:2026-09-06"], (
        "the released savepoint leaked and the plain uncommitted insert did not: "
        "SQLite cannot model a crash after a savepoint, so the crash-window "
        "guarantee is only asserted against PostgreSQL"
    )


# ------------------------------------------------------------ retry identity
async def test_a_retry_after_midnight_writes_the_original_period(session, worker_db, monkeypatch):
    """The retry keeps the key, the boundaries and the content window it was given.

    The first attempt fails at 23:30; the retry lands at 00:30 the next day. If
    the retry recalculated anything, it would write `daily:2026-09-07` — a period
    that has not even finished — and the 6th would go unsummarised forever.
    """
    user = await make_user(
        session, email="midnight@example.org", digest_frequency=DigestFrequency.DAILY.value
    )
    inside = await make_opportunity(session, slug="inside-period", title="Changed on the 6th", score=70.0)
    outside = await make_opportunity(session, slug="outside-period", title="Changed on the 7th", score=70.0)
    await detect_changes(
        session,
        opportunity=inside,
        previous={"opportunity_score": 50.0},
        now=datetime(2026, 9, 6, 12, 0, tzinfo=UTC),
    )
    await detect_changes(
        session,
        opportunity=outside,
        previous={"opportunity_score": 50.0},
        now=datetime(2026, 9, 7, 12, 0, tzinfo=UTC),
    )
    await session.commit()

    calls = {"n": 0}
    real_build = alerts.build_digest

    async def fail_once(session_, **kwargs):  # noqa: ANN001, ANN003
        calls["n"] += 1
        if calls["n"] == 1:
            raise RuntimeError("first attempt died")
        return await real_build(session_, **kwargs)

    monkeypatch.setattr(alerts, "build_digest", fail_once)
    first = await tasks.run_digest_phase(frequencies=["daily"], now=datetime(2026, 9, 7, 23, 30, tzinfo=UTC))
    assert first["status"] == "failed"
    failed = first["failed_periods"]
    assert [period["key"] for period in failed] == ["daily:2026-09-06"]

    # The retry, carrying exactly what the failed attempt reported, after midnight.
    retry = await tasks.run_digest_phase(periods=failed, now=datetime(2026, 9, 8, 0, 30, tzinfo=UTC))

    assert retry["status"] == "ok"
    assert retry["periods"][0]["period_key"] == "daily:2026-09-06"
    assert retry["periods"][0]["written"] == 1
    digest = (await session.execute(sa.select(Digest).where(Digest.user_id == user.id))).scalars().one()
    assert digest.period_key == "daily:2026-09-06", "the retry invented a new period"
    assert digest.sections["period"]["key"] == "daily:2026-09-06"
    assert digest.sections["period"]["end"] == "2026-09-07T00:00:00+00:00"
    titles = [item["title"] for item in digest.sections["other_changes"]]
    assert titles == ["Changed on the 6th"], "the retry summarised a different 24 hours"


async def test_a_timezone_reconfigured_between_attempts_does_not_move_the_period(
    session, worker_db, monkeypatch
):
    """The period carries its own timezone; the configuration does not rewrite history.

    An operator fixing `SCHEDULE_TIMEZONE` between the attempt and the retry must
    not end up with a digest labelled Auckland over a window computed in UTC.
    """
    from app.core.config import get_settings

    await make_user(session, email="reconfigured@example.org", digest_frequency=DigestFrequency.DAILY.value)
    period = digest_period("daily", now=MONDAY_0300, timezone_name="UTC")
    monkeypatch.setenv("SCHEDULE_TIMEZONE", "Pacific/Auckland")
    get_settings.cache_clear()
    try:
        result = await tasks.run_digest_phase(periods=[period.to_dict()], now=MONDAY_0300)
    finally:
        get_settings.cache_clear()

    assert result["periods"][0]["period_key"] == "daily:2026-09-06"
    digest = (await session.execute(sa.select(Digest))).scalars().one()
    assert digest.sections["period"]["timezone"] == "UTC"
    assert digest.sections["period"]["start"] == "2026-09-06T00:00:00+00:00"


async def test_a_partial_period_is_finished_without_repeating_the_users_it_wrote(
    session, worker_db, monkeypatch
):
    """Retrying a period skips its successful users by database, not by memory.

    The worker that retries may be a different process with no idea who succeeded.
    What makes that safe is the unique index: the second run re-attempts everyone,
    and the users already written come back as counted duplicates.
    """
    fine = await make_user(session, email="fine@example.org", digest_frequency=DigestFrequency.DAILY.value)
    broken = await make_user(
        session, email="broken@example.org", digest_frequency=DigestFrequency.DAILY.value
    )
    period = digest_period("daily", now=MONDAY_0300, timezone_name="UTC")
    real_build = alerts.build_digest
    failing = {"armed": True}

    async def break_one(session_, *, user, **kwargs):  # noqa: ANN001, ANN003
        if failing["armed"] and user.id == broken.id:
            raise RuntimeError("this user's digest could not be built")
        return await real_build(session_, user=user, **kwargs)

    monkeypatch.setattr(alerts, "build_digest", break_one)
    first = await run_digests(session, frequency="daily", period=period)
    await session.commit()
    written_before = (
        (await session.execute(sa.select(Digest).where(Digest.user_id == fine.id))).scalars().one()
    )

    failing["armed"] = False
    second = await run_digests(session, frequency="daily", period=period)
    await session.commit()

    assert (first.written, first.failed, first.duplicates) == (1, 1, 0)
    assert (second.written, second.failed, second.duplicates) == (1, 0, 1), (
        "the successful user was written a second time instead of recognised"
    )
    rows = (await session.execute(sa.select(Digest))).scalars().all()
    assert len(rows) == 2
    assert {row.user_id for row in rows} == {fine.id, broken.id}
    still_there = (await session.execute(sa.select(Digest).where(Digest.user_id == fine.id))).scalars().one()
    assert still_there.id == written_before.id, "the first digest was replaced rather than kept"
    assert still_there.generated_at == written_before.generated_at


# ------------------------------------------------------- on-demand compatibility
async def test_an_on_demand_digest_has_no_period_and_still_repeats(session, worker_db):
    """`POST /me/digests` keeps the behaviour it has always had.

    An on-demand digest is a rolling window ending "now", generated because a user
    asked for it. Giving it a canonical key would have turned a second click into
    a database error, which is a change in API behaviour this task did not
    authorise. It stays keyless — and therefore repeatable — by design.
    """
    user = await make_user(session, email="on-demand@example.org", digest_frequency=DigestFrequency.OFF.value)
    now = datetime(2026, 9, 7, 15, 0, tzinfo=UTC)

    first = await build_digest(session, user=user, frequency="daily", now=now)
    second = await build_digest(session, user=user, frequency="daily", now=now + timedelta(hours=1))
    await session.commit()

    assert first is not None and second is not None
    assert first.period_key is None and second.period_key is None
    assert "key" not in first.sections["period"], "an on-demand digest claimed a canonical period"
    assert first.period_start == now - timedelta(days=alerts.DIGEST_WINDOW_DAYS["daily"])
    assert first.period_end == now
    assert len((await session.execute(sa.select(Digest))).scalars().all()) == 2


async def test_the_digest_endpoint_still_answers_a_second_request_with_201(client, admin_headers):
    """The API contract is unchanged, end to end, by the new column."""
    first = await client.post("/api/v1/me/digests?frequency=daily", headers=admin_headers)
    second = await client.post("/api/v1/me/digests?frequency=daily", headers=admin_headers)

    assert first.status_code == 201, first.text
    assert second.status_code == 201, "deduplication leaked into the on-demand endpoint"
    assert first.json()["id"] != second.json()["id"], "the two requests returned one row"
    assert "key" not in first.json()["sections"]["period"], "an on-demand digest claimed a canonical period"


# ----------------------------------------------------------------- the contents
async def test_a_scheduled_digest_keeps_watchlist_scoping_and_the_disclaimer(session, worker_db):
    """Period identity must not have cost the digest anything it already did."""
    user = await make_user(session, email="scoped@example.org", digest_frequency=DigestFrequency.DAILY.value)
    watched = await make_opportunity(session, slug="period-watched", title="Watched", score=70.0)
    other = await make_opportunity(session, slug="period-other", title="Other", score=70.0)
    watchlist = Watchlist(user_id=user.id, name="Mine")
    session.add(watchlist)
    await session.flush()
    session.add(WatchlistItem(watchlist_id=watchlist.id, item_type="opportunity", opportunity_id=watched.id))
    for opp in (watched, other):
        await detect_changes(
            session,
            opportunity=opp,
            previous={"opportunity_score": 50.0},
            now=datetime(2026, 9, 6, 9, 0, tzinfo=UTC),
        )
    await session.commit()

    await tasks.run_digest_phase(frequencies=["daily"], now=MONDAY_0300)

    digest = (await session.execute(sa.select(Digest))).scalars().one()
    assert [item["title"] for item in digest.sections["watchlist_changes"]] == ["Watched"]
    assert [item["title"] for item in digest.sections["other_changes"]] == ["Other"]
    assert "not a recommendation" in digest.sections["note"]
    assert digest.item_count == 2
    assert digest.sections["period"]["key"] == "daily:2026-09-06"


async def test_a_quiet_period_produces_an_honest_empty_digest(session, worker_db):
    """Nothing happened is a finding, and it is still exactly one row per period."""
    await make_user(session, email="quiet@example.org", digest_frequency=DigestFrequency.DAILY.value)
    outcome = await run_digests(
        session, frequency="daily", period=digest_period("daily", now=MONDAY_0300, timezone_name="UTC")
    )
    await session.commit()

    digest = (await session.execute(sa.select(Digest))).scalars().one()
    assert outcome.written == 1
    assert digest.item_count == 0
    assert "Nothing on your watchlists changed" in digest.sections["empty_note"]


# -------------------------------------------------------------------- migration
def _apply_migration_0007(sync_conn, *, downgrade: bool = False) -> None:
    """Execute the real 0007 `upgrade()` (or `downgrade()`) on a sync connection.

    The suite builds its schema with `Base.metadata.create_all` rather than by
    running Alembic, so the revision module is loaded directly and driven through
    Alembic's operations context — the migration's own code, not a restatement of
    its SQL.
    """
    import importlib.util
    from pathlib import Path

    from alembic.migration import MigrationContext
    from alembic.operations import Operations

    path = Path(__file__).resolve().parents[1] / "alembic" / "versions" / "0007_digest_period_identity.py"
    spec = importlib.util.spec_from_file_location("_migration_0007", path)
    assert spec and spec.loader
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)

    with Operations.context(MigrationContext.configure(sync_conn)):
        (module.downgrade if downgrade else module.upgrade)()


def _digest_columns(sync_conn) -> set[str]:
    return {row[1] for row in sync_conn.exec_driver_sql("PRAGMA table_info(digests)")}


def _index_names(sync_conn) -> set[str]:
    return {row[1] for row in sync_conn.exec_driver_sql("PRAGMA index_list(digests)")}


async def _make_pre_0007_schema(engine) -> str:
    """Reshape `digests` to how it looked before 0007, seeded with real history.

    `create_all` builds the current model, which already has the column and the
    index, so running the migration against it would only exercise the
    "already present" guards. Dropping both reproduces the starting state of an
    existing deployment; the rows are then inserted with raw SQL, because the
    mapped class knows about a column this table deliberately no longer has.

    The two same-day rows are the point: they are legal today, they were legal
    before, and they must still be legal after the migration. A destructive
    backfill would have had to delete one of them to satisfy a unique constraint.
    """
    maker = async_sessionmaker(engine, expire_on_commit=False, class_=AsyncSession)
    async with maker() as s:
        s.add(
            User(
                email="historical@example.org",
                password_hash="x",
                full_name="Historical",
                role="viewer",
                is_active=True,
            )
        )
        await s.commit()

    async with engine.begin() as conn:
        user_id = (await conn.exec_driver_sql("SELECT id FROM users LIMIT 1")).scalar_one()
        await conn.exec_driver_sql("DROP INDEX ux_digest_period")
        await conn.exec_driver_sql("ALTER TABLE digests DROP COLUMN period_key")
        assert "period_key" not in await conn.run_sync(_digest_columns)
        assert "ux_digest_period" not in await conn.run_sync(_index_names)
        for frequency, day, generated in [
            # Two digests for the same day: what the old rolling window allowed,
            # and what a backfilled unique key would have had to destroy.
            ("daily", "2026-08-01", "2026-08-01 06:00:00"),
            ("daily", "2026-08-01", "2026-08-01 18:00:00"),
            ("weekly", "2026-07-27", "2026-08-02 06:00:00"),
        ]:
            await conn.exec_driver_sql(
                "INSERT INTO digests (id,user_id,frequency,period_start,period_end,sections,"
                " item_count,generated_at,created_at)"
                " VALUES (?,?,?,?,?,?,?,?,datetime('now'))",
                (
                    uuid.uuid4().hex,
                    user_id,
                    frequency,
                    f"{day} 00:00:00",
                    f"{day} 23:59:59",
                    json.dumps({"period": {"frequency": frequency}, "alerts": [], "note": "historical"}),
                    0,
                    generated,
                ),
            )
    return user_id


async def test_migration_0007_upgrades_a_legacy_table_and_keeps_every_row(engine):
    await _make_pre_0007_schema(engine)

    async with engine.begin() as conn:
        before = await conn.run_sync(_digest_columns)
        history = (await conn.exec_driver_sql("SELECT id, frequency, generated_at FROM digests")).fetchall()
        assert len(history) == 3
        await conn.run_sync(_apply_migration_0007)
        after = await conn.run_sync(_digest_columns)
        indexes = await conn.run_sync(_index_names)
        kept = (
            await conn.exec_driver_sql("SELECT id, frequency, generated_at, period_key FROM digests")
        ).fetchall()

    assert "period_key" in after, "the migration adds the period identity"
    assert before | {"period_key"} == after, "and changes no other column"
    assert "ux_digest_period" in indexes, "uniqueness has to be enforced by the database"
    assert [(row[0], row[1], row[2]) for row in kept] == history, "historical rows were touched"
    assert [row[3] for row in kept] == [None, None, None], "history was backfilled with a guessed key"


async def test_migration_0007_makes_the_index_unique_but_leaves_nulls_distinct(engine):
    """The exact semantics the on-demand endpoint and the history both depend on."""
    await _make_pre_0007_schema(engine)
    async with engine.begin() as conn:
        await conn.run_sync(_apply_migration_0007)
        user_id = (await conn.exec_driver_sql("SELECT id FROM users LIMIT 1")).scalar_one()

        async def insert(key):  # noqa: ANN001
            await conn.exec_driver_sql(
                "INSERT INTO digests (id,user_id,frequency,period_start,period_end,sections,"
                " item_count,generated_at,created_at,period_key)"
                " VALUES (?,?,?,?,?,?,?,?,datetime('now'),?)",
                (
                    uuid.uuid4().hex,
                    user_id,
                    "daily",
                    "2026-09-06 00:00:00",
                    "2026-09-07 00:00:00",
                    "{}",
                    0,
                    "2026-09-07 03:00:00",
                    key,
                ),
            )

        await insert("daily:2026-09-06")
        with pytest.raises(sa.exc.IntegrityError):
            await insert("daily:2026-09-06")
        await insert("daily:2026-09-07")  # a different period is a different digest
        await insert(None)
        await insert(None)  # NULLs stay distinct: on-demand digests remain repeatable
        assert (await conn.exec_driver_sql("SELECT count(*) FROM digests")).scalar_one() == 3 + 4


async def test_migration_0007_is_safe_to_re_run_and_reverses(engine):
    """Idempotent guards, and a downgrade that leaves the digests themselves alone."""
    await _make_pre_0007_schema(engine)

    async with engine.begin() as conn:
        await conn.run_sync(_apply_migration_0007)
    async with engine.begin() as conn:
        await conn.run_sync(_apply_migration_0007)  # the "already present" branch
        assert "period_key" in await conn.run_sync(_digest_columns)

    async with engine.begin() as conn:
        await conn.run_sync(_apply_migration_0007, downgrade=True)
        assert "period_key" not in await conn.run_sync(_digest_columns)
        assert "ux_digest_period" not in await conn.run_sync(_index_names)
        assert (await conn.exec_driver_sql("SELECT count(*) FROM digests")).scalar_one() == 3, (
            "downgrading dropped digests"
        )

    async with engine.begin() as conn:
        await conn.run_sync(_apply_migration_0007)  # upgrade -> downgrade -> upgrade
        assert "period_key" in await conn.run_sync(_digest_columns)


def test_the_migration_chain_still_has_one_head():
    """0007 hangs off 0006, which hangs off the Telegram binding work."""
    import importlib.util
    from pathlib import Path

    path = Path(__file__).resolve().parents[1] / "alembic" / "versions" / "0007_digest_period_identity.py"
    spec = importlib.util.spec_from_file_location("_migration_0007_head", path)
    assert spec and spec.loader
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)

    assert module.revision == "0007_digest_period_identity"
    assert module.down_revision == "0006_telegram_secure_binding"


async def test_the_orm_and_the_migration_agree_about_the_schema(session, worker_db):
    """The model is the other half of the contract the migration wrote.

    If the model's index were dropped or renamed while the migration kept the old
    one, `create_all` deployments and migrated deployments would enforce
    different rules — and the test suite, which uses `create_all`, would not see
    the difference.
    """
    table = Digest.__table__
    index = next(i for i in table.indexes if i.name == "ux_digest_period")

    assert index.unique is True
    assert [column.name for column in index.columns] == ["user_id", "frequency", "period_key"]
    assert table.columns["period_key"].nullable is True, (
        "history has no key, so the column cannot be NOT NULL"
    )
    assert table.columns["period_key"].type.length == 40

    user = await make_user(session, email="orm@example.org", digest_frequency=DigestFrequency.DAILY.value)
    period = digest_period("daily", now=MONDAY_0300, timezone_name="UTC")
    for _ in range(2):
        session.add(
            Digest(
                user_id=user.id,
                frequency="daily",
                period_start=period.start,
                period_end=period.end,
                period_key=period.key,
                sections={},
                item_count=0,
                generated_at=MONDAY_0300,
            )
        )
    with pytest.raises(sa.exc.IntegrityError):
        await session.commit()
    await session.rollback()


async def test_the_profile_preference_is_untouched_by_the_period_change(session, worker_db):
    """The new column describes the digest, not the user's preference."""
    user = await make_user(
        session, email="preference@example.org", digest_frequency=DigestFrequency.WEEKLY.value
    )
    profile = (
        (await session.execute(sa.select(UserProfile).where(UserProfile.user_id == user.id))).scalars().one()
    )

    assert profile.digest_frequency == DigestFrequency.WEEKLY.value
    assert not hasattr(profile, "period_key")
