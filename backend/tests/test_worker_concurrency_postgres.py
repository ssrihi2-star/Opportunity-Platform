"""The concurrency guard, against a real PostgreSQL.

`app.workers.locks` uses `pg_try_advisory_xact_lock`, which only exists on
PostgreSQL. The rest of the suite runs on SQLite, where the guard reports
"acquired" and steps aside, so those tests can show what a phase does *when* it
is told another run owns the lock and nothing about whether two real runs would
ever be told that. This file is the other half: separate sessions on separate
connections (`poolclass=None`, so no pooled connection is shared and neither
transaction can see the other's lock), each inside its own transaction.

What is asserted here is genuinely about contention, not about mocks:

* a second transaction is refused while the first is still open;
* the lock is released by a commit *and* by a rollback, which is what makes a
  killed worker recoverable rather than a permanent lockout;
* an in-progress scheduled run stops a second one from dispatching, and the next
  run after it does the work;
* two runs racing for the same digest period write it once — the unique index,
  under real contention rather than in sequence;
* an attempt that dies before its commit reserves nothing, and an outer rollback
  discards a savepoint that was already released. Neither can be modelled on
  SQLite, where the pysqlite driver runs SAVEPOINT outside an explicit
  transaction; see `tests/test_digest_periods.py`.

Skipped unless `TEST_POSTGRES_URL` points at a scratch database, exactly like
`tests/test_postgres_integrity.py`. The schema is dropped and recreated, so
never aim it at anything you care about. Nothing here sends a notification:
the providers are the real, unconfigured ones, and in-app delivery is a stored
row.
"""

from __future__ import annotations

import asyncio
import os
from datetime import UTC, datetime

import pytest
import pytest_asyncio
import sqlalchemy as sa
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

from app.db.base import Base
from app.models.models import AlertDelivery, Digest, SystemAuditLog
from app.services.alerts import digest_period, run_digests
from app.workers import tasks
from app.workers.locks import try_advisory_xact_lock
from tests.test_monitoring_alerts import make_rule
from tests.test_worker_scheduling import make_confirmed_opportunity, make_user

POSTGRES_URL = os.environ.get("TEST_POSTGRES_URL")

pytestmark = [
    pytest.mark.asyncio,
    pytest.mark.skipif(
        not POSTGRES_URL,
        reason="Set TEST_POSTGRES_URL to a scratch database to run the PostgreSQL checks.",
    ),
]


@pytest_asyncio.fixture
async def pg_engine():
    engine = create_async_engine(POSTGRES_URL or "", poolclass=None)
    async with engine.begin() as conn:
        await conn.execute(sa.text("DROP SCHEMA IF EXISTS public CASCADE"))
        await conn.execute(sa.text("CREATE SCHEMA public"))
        await conn.run_sync(Base.metadata.create_all)
    yield engine
    await engine.dispose()


@pytest_asyncio.fixture
async def pg_maker(pg_engine, monkeypatch):
    """A session factory on its own connections, wired into the worker."""
    maker = async_sessionmaker(pg_engine, expire_on_commit=False, class_=AsyncSession)
    monkeypatch.setattr(tasks, "SessionLocal", maker)
    return maker


async def test_a_second_transaction_is_refused_while_the_first_holds_the_lock(pg_maker):
    async with pg_maker() as first:
        assert await try_advisory_xact_lock(first, tasks.MONITORING_LOCK) is True

        async with pg_maker() as second:
            assert await try_advisory_xact_lock(second, tasks.MONITORING_LOCK) is False, (
                "two open transactions both believe they own the monitoring phase"
            )
            # A different phase is a different lock: digests are not blocked by
            # monitoring, they are ordered after it by the pipeline.
            assert await try_advisory_xact_lock(second, f"{tasks.DIGEST_LOCK}:daily") is True

        await first.commit()

    async with pg_maker() as third:
        assert await try_advisory_xact_lock(third, tasks.MONITORING_LOCK) is True


async def test_the_lock_dies_with_the_transaction_that_held_it(pg_maker):
    """The crash case: an aborted transaction cannot leave the phase locked out.

    A worker killed mid-phase loses its connection, PostgreSQL rolls the
    transaction back, and the lock goes with it. There is no TTL to expire and
    nothing for an operator to clear by hand.
    """
    async with pg_maker() as holder:
        assert await try_advisory_xact_lock(holder, tasks.MONITORING_LOCK) is True
        await holder.rollback()

    async with pg_maker() as next_run:
        assert await try_advisory_xact_lock(next_run, tasks.MONITORING_LOCK) is True


async def test_an_in_progress_run_stops_a_second_one_from_dispatching(pg_maker):
    user = None
    async with pg_maker() as setup:
        user = await make_user(setup, email="pg-scheduled@example.org")
        await make_confirmed_opportunity(setup, slug="pg-scheduled")
        await make_rule(setup, user, trigger="confirmation_met", cooldown_hours=0)

    now = datetime(2026, 9, 7, 3, 0, tzinfo=UTC)

    # A run that is mid-flight: it has the lock and has not committed yet.
    async with pg_maker() as in_flight:
        assert await try_advisory_xact_lock(in_flight, tasks.MONITORING_LOCK) is True

        overlapping = await tasks.run_monitoring_phase(now=now)
        assert overlapping["status"] == "skipped"

        async with pg_maker() as check:
            assert (await check.execute(sa.select(AlertDelivery))).scalars().all() == []
            assert (
                await check.execute(
                    sa.select(SystemAuditLog).where(SystemAuditLog.action == "monitoring.run.scheduled")
                )
            ).scalars().all() == []

        await in_flight.rollback()

    # The run after it does the work, and the fact reaches the user once.
    settled = await tasks.run_monitoring_phase(now=now)
    assert settled["status"] == "ok"
    assert settled["alerts_sent"] == 1

    async with pg_maker() as check:
        deliveries = (await check.execute(sa.select(AlertDelivery))).scalars().all()
        assert len(deliveries) == 1
        assert deliveries[0].user_id == user.id

    repeat = await tasks.run_monitoring_phase(now=now)
    assert repeat["alerts_sent"] == 0, "the same fact reached the same person twice"


async def test_the_digest_phase_is_guarded_per_period(pg_maker):
    """The lock is per period, so a retry of yesterday cannot block today.

    Granularity matters here in both directions: two runs of the *same* period
    must not both write it, and a run that is retrying an old period must not
    stop the night's current one.
    """
    sunday = datetime(2026, 9, 6, 3, 0, tzinfo=UTC)
    async with pg_maker() as setup:
        await make_user(setup, email="pg-guarded@example.org", digest_frequency="daily")

    async with pg_maker() as holder:
        assert await try_advisory_xact_lock(holder, f"{tasks.DIGEST_LOCK}:daily:2026-09-05") is True

        result = await tasks.run_digest_phase(frequencies=["daily", "weekly"], now=sunday)

        by_key = {entry["period_key"]: entry["status"] for entry in result["periods"]}
        assert by_key == {"daily:2026-09-05": "skipped", "weekly:2026-W35": "ok"}
        assert result["skipped"] == ["daily:2026-09-05"]
        assert result["status"] == "partial"
        assert result["failed_periods"] == [], "a skipped period is not a failed one"

        # A different period of the same frequency is a different lock.
        assert await try_advisory_xact_lock(holder, f"{tasks.DIGEST_LOCK}:daily:2026-09-04") is True

        await holder.rollback()


async def test_advisory_locks_are_really_available_here(pg_maker):
    """If this ever fails, the tests above are passing for the wrong reason."""
    from app.workers.locks import is_postgresql

    async with pg_maker() as session:
        assert is_postgresql(session) is True
        assert (await session.execute(sa.text("SELECT 1"))).scalar_one() == 1


async def test_a_scheduled_run_writes_digests_for_the_users_who_asked(pg_maker):
    """The same end-to-end path as the SQLite suite, on the real database."""
    async with pg_maker() as setup:
        asked = await make_user(setup, email="pg-daily@example.org", digest_frequency="daily")
        declined = await make_user(setup, email="pg-off@example.org", digest_frequency="off")

    result = await tasks.run_digest_phase(frequencies=["daily"], now=datetime(2026, 9, 7, 3, 0, tzinfo=UTC))

    assert result["status"] == "ok"
    assert result["periods"] == [
        {
            "frequency": "daily",
            "period_key": "daily:2026-09-06",
            "timezone": "UTC",
            "status": "ok",
            "written": 1,
            "duplicates": 0,
            "users_failed": 0,
        }
    ]

    async with pg_maker() as check:
        rows = (await check.execute(sa.select(Digest))).scalars().all()
        assert [(row.user_id, row.period_key) for row in rows] == [(asked.id, "daily:2026-09-06")]
        assert declined.id not in {row.user_id for row in rows}


async def test_two_runs_racing_for_one_period_write_one_digest(pg_maker):
    """The unique index under real contention, not two inserts in sequence.

    Both attempts run concurrently on their own connections and each commits its
    own work, so whichever loses the race is blocked on the winner's row and then
    sees it committed. What must happen is that the loser reports a duplicate —
    counted, not raised — and the user ends up with one digest.
    """
    async with pg_maker() as setup:
        await make_user(setup, email="pg-race@example.org", digest_frequency="daily")
    period = digest_period("daily", now=datetime(2026, 9, 7, 3, 0, tzinfo=UTC), timezone_name="UTC")

    async def attempt() -> tuple[int, int]:
        async with pg_maker() as session:
            outcome = await run_digests(session, frequency="daily", period=period)
            await session.commit()
            return outcome.written, outcome.duplicates

    results = await asyncio.gather(attempt(), attempt(), attempt())

    assert sorted(results) == [(0, 1), (0, 1), (1, 0)], (
        "three concurrent runs of one period did not produce exactly one digest"
    )
    async with pg_maker() as check:
        rows = (await check.execute(sa.select(Digest))).scalars().all()
        assert len(rows) == 1
        assert rows[0].period_key == "daily:2026-09-06"


async def test_an_attempt_that_died_before_committing_reserved_nothing(pg_maker):
    """The crash window, on the database that production actually runs.

    An attempt that writes a digest and then dies without committing has reserved
    nothing: the next attempt writes the period for the first time, and the user
    is not left short a digest. The SQLite suite cannot model this — see
    `test_sqlite_cannot_model_a_crash_after_a_released_savepoint` — so it is
    asserted here, where a closed connection really does roll back.
    """
    async with pg_maker() as setup:
        await make_user(setup, email="pg-crash@example.org", digest_frequency="daily")
    period = digest_period("daily", now=datetime(2026, 9, 7, 3, 0, tzinfo=UTC), timezone_name="UTC")

    died = pg_maker()
    crashed = await run_digests(died, frequency="daily", period=period)
    assert crashed.written == 1, "the attempt believed it had written one"
    await died.close()  # the process ended here: no commit

    async with pg_maker() as check:
        assert (await check.execute(sa.select(sa.func.count()).select_from(Digest))).scalar_one() == 0

    async with pg_maker() as recovered:
        outcome = await run_digests(recovered, frequency="daily", period=period)
        await recovered.commit()
        rows = (await recovered.execute(sa.select(Digest))).scalars().all()

    assert (outcome.written, outcome.duplicates, outcome.failed) == (1, 0, 0)
    assert [row.period_key for row in rows] == ["daily:2026-09-06"]


async def test_an_outer_rollback_discards_work_a_savepoint_already_released(pg_maker):
    """The other half of the SQLite caveat, proved where it holds.

    `run_digests` releases a savepoint per user and leaves the commit to its
    caller, so a caller that rolls back — a failed phase, a killed run — must
    find nothing persisted. On PostgreSQL that is a transaction rollback. A
    failed period is therefore genuinely unwritten, which is what lets the retry
    write it.
    """
    async with pg_maker() as setup:
        await make_user(setup, email="pg-rollback@example.org", digest_frequency="daily")
    period = digest_period("daily", now=datetime(2026, 9, 7, 3, 0, tzinfo=UTC), timezone_name="UTC")

    async with pg_maker() as rolled_back:
        outcome = await run_digests(rolled_back, frequency="daily", period=period)
        assert outcome.written == 1
        await rolled_back.rollback()

    async with pg_maker() as check:
        assert (await check.execute(sa.select(sa.func.count()).select_from(Digest))).scalar_one() == 0

    async with pg_maker() as retry:
        retried = await run_digests(retry, frequency="daily", period=period)
        await retry.commit()

    assert (retried.written, retried.duplicates) == (1, 0), (
        "the rolled-back attempt reserved the period after all"
    )


async def test_a_failed_user_inside_a_period_does_not_cost_the_others_theirs(pg_maker, monkeypatch):
    """Per-user savepoints, on the database where a failed statement aborts the transaction.

    This is the case the savepoint exists for: on PostgreSQL an error inside a
    transaction leaves the whole transaction unusable until it is rolled back, so
    without a savepoint one user's failure would take the entire period with it.
    The failure injected here is a real database error rather than a Python
    exception, because that is the kind that poisons a PostgreSQL transaction.
    """
    from app.services import alerts

    async with pg_maker() as setup:
        await make_user(setup, email="pg-fine@example.org", digest_frequency="daily")
        broken = await make_user(setup, email="pg-broken@example.org", digest_frequency="daily")
    period = digest_period("daily", now=datetime(2026, 9, 7, 3, 0, tzinfo=UTC), timezone_name="UTC")
    real_build = alerts.build_digest

    async def build(session_, *, user, **kwargs):  # noqa: ANN001, ANN003
        if user.id == broken.id:
            await session_.execute(sa.text("SELECT 1 / :zero"), {"zero": 0})
        return await real_build(session_, user=user, **kwargs)

    monkeypatch.setattr(alerts, "build_digest", build)

    async with pg_maker() as session:
        outcome = await run_digests(session, frequency="daily", period=period)
        await session.commit()
        rows = (await session.execute(sa.select(Digest))).scalars().all()

    assert (outcome.written, outcome.failed, outcome.duplicates) == (1, 1, 0)
    assert [row.period_key for row in rows] == ["daily:2026-09-06"]
    assert broken.id not in {row.user_id for row in rows}, "the user whose digest failed still got a row"
