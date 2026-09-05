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
  run after it does the work.

Skipped unless `TEST_POSTGRES_URL` points at a scratch database, exactly like
`tests/test_postgres_integrity.py`. The schema is dropped and recreated, so
never aim it at anything you care about. Nothing here sends a notification:
the providers are the real, unconfigured ones, and in-app delivery is a stored
row.
"""

from __future__ import annotations

import os
from datetime import UTC, datetime

import pytest
import pytest_asyncio
import sqlalchemy as sa
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

from app.db.base import Base
from app.models.models import AlertDelivery, SystemAuditLog
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
    """Daily and weekly are separate locks: one must not block the other."""
    async with pg_maker() as daily_holder:
        assert await try_advisory_xact_lock(daily_holder, f"{tasks.DIGEST_LOCK}:daily") is True

        result = await tasks.run_digest_phase(
            frequencies=["daily", "weekly"], now=datetime(2026, 9, 6, 3, 0, tzinfo=UTC)
        )
        assert result["frequencies"]["daily"] == "skipped"
        assert result["skipped"] == ["daily"]
        assert result["status"] == "partial"

        await daily_holder.rollback()


async def test_advisory_locks_are_really_available_here(pg_maker):
    """If this ever fails, the tests above are passing for the wrong reason."""
    from app.workers.locks import is_postgresql

    async with pg_maker() as session:
        assert is_postgresql(session) is True
        assert (await session.execute(sa.text("SELECT 1"))).scalar_one() == 1


async def test_a_scheduled_run_writes_digests_for_the_users_who_asked(pg_maker):
    """The same end-to-end path as the SQLite suite, on the real database."""
    from app.models.models import Digest

    async with pg_maker() as setup:
        asked = await make_user(setup, email="pg-daily@example.org", digest_frequency="daily")
        declined = await make_user(setup, email="pg-off@example.org", digest_frequency="off")

    result = await tasks.run_digest_phase(frequencies=["daily"], now=datetime(2026, 9, 7, 3, 0, tzinfo=UTC))
    assert result["status"] == "ok"
    assert result["frequencies"] == {"daily": 1}

    async with pg_maker() as check:
        rows = (await check.execute(sa.select(Digest))).scalars().all()
        assert [row.user_id for row in rows] == [asked.id]
        assert declined.id not in {row.user_id for row in rows}
