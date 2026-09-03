"""Integrity checks that only mean something on PostgreSQL.

The rest of the suite runs on SQLite, which is fast and correct for logic but
silently forgiving about the two things this file exists to check:

* **Transaction containment.** On PostgreSQL a failed statement aborts the whole
  transaction unless a SAVEPOINT genuinely contains it. Alert deduplication
  depends on catching an `IntegrityError` and carrying on, so if containment
  fails the visible symptom is not a duplicate — it is that every alert *after*
  a duplicate in the same batch disappears with no error raised anywhere.
* **The migration chain.** SQLite tolerates ordering and constraint mistakes
  that PostgreSQL refuses, which is how two earlier migrations in this project
  passed locally and failed on the real database.

These skip when no PostgreSQL is reachable, so the default suite still runs
anywhere. Point `TEST_POSTGRES_URL` at a scratch database to enable them; it is
dropped and recreated, so never aim it at anything you care about.
"""

from __future__ import annotations

import os
import uuid
from datetime import UTC, datetime

import pytest
import pytest_asyncio
import sqlalchemy as sa
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

from app.db.base import Base
from app.models.models import (
    AlertDelivery,
    AlertRule,
    Opportunity,
    Trend,
    User,
)
from app.services.alerts import dispatch
from app.services.monitoring import detect_changes

POSTGRES_URL = os.environ.get("TEST_POSTGRES_URL")

pytestmark = [
    pytest.mark.asyncio,
    pytest.mark.skipif(
        not POSTGRES_URL,
        reason="Set TEST_POSTGRES_URL to a scratch database to run the PostgreSQL checks.",
    ),
]


@pytest_asyncio.fixture
async def pg_session():
    engine = create_async_engine(POSTGRES_URL or "", poolclass=None)
    async with engine.begin() as conn:
        # Dropping the schema rather than the tables: `model_runs`, `trends` and
        # `opportunities` reference each other in a cycle that metadata.drop_all
        # cannot order. The schema drop does not need an ordering.
        await conn.execute(sa.text("DROP SCHEMA IF EXISTS public CASCADE"))
        await conn.execute(sa.text("CREATE SCHEMA public"))
        await conn.run_sync(Base.metadata.create_all)
    maker = async_sessionmaker(engine, expire_on_commit=False, class_=AsyncSession)
    async with maker() as session:
        yield session
    await engine.dispose()


async def _opportunity(session: AsyncSession, slug: str, score: float) -> Opportunity:
    now = datetime.now(UTC)
    trend = Trend(
        name=f"{slug} trend",
        subject_type="topic",
        category="technology",
        geo_scope="global",
        trend_score=60.0,
        confidence=70.0,
        stage="early_adoption",
        state="active",
        first_detected_at=now,
        last_evaluated_at=now,
    )
    session.add(trend)
    await session.flush()
    opp = Opportunity(
        slug=slug,
        title=f"Candidate {slug}",
        opportunity_type="business",
        category="technology",
        industry="technology",
        geo_scope="global",
        state="candidate",
        validation_status="demo",
        maturity_stage="early_adoption",
        risk_level="moderate",
        opportunity_score=score,
        adjusted_score=score,
        raw_score=score,
        confidence=70.0,
        peak_score=score,
        capital_required_usd=20_000.0,
        primary_trend_id=trend.id,
        detected_at=now,
        algorithm_version="1.0.0",
    )
    session.add(opp)
    await session.flush()
    return opp


async def _user_with_rule(session: AsyncSession) -> User:
    user = User(
        email=f"pg-{uuid.uuid4().hex[:8]}@test",
        full_name="PG",
        password_hash="x",
        role="analyst",
    )
    session.add(user)
    await session.flush()
    session.add(
        AlertRule(
            user_id=user.id,
            name="Big moves",
            trigger="score_threshold",
            enabled=True,
            conditions={},
            channels=["in_app"],
            # Zero, so that only the uniqueness constraint can stop a repeat.
            cooldown_hours=0,
        )
    )
    await session.commit()
    return user


async def test_a_duplicate_does_not_swallow_the_alerts_behind_it(pg_session):
    """The failure this file exists for.

    A duplicate first, then a genuinely new event in the same batch. If the
    savepoint does not contain the aborted insert, the transaction is poisoned
    and the new alert vanishes without any error surfacing anywhere.
    """
    session = pg_session
    await _user_with_rule(session)
    first = await _opportunity(session, "pg-first", 80.0)
    new = await _opportunity(session, "pg-new", 84.0)
    await session.commit()

    old = (await detect_changes(session, opportunity=first, previous={"opportunity_score": 50.0}))[0]
    fresh = (await detect_changes(session, opportunity=new, previous={"opportunity_score": 50.0}))[0]
    await session.commit()

    delivered = await dispatch(session, events=[(old, first)])
    await session.commit()
    assert delivered.sent == 1

    mixed = await dispatch(session, events=[(old, first), (fresh, new)])
    await session.commit()

    assert mixed.sent == 1, (
        "an alert following a duplicate in the same batch was lost; the savepoint "
        "is not containing the aborted insert"
    )
    assert mixed.suppressed == 1
    assert "already delivered" in mixed.reasons


async def test_the_same_fact_reaches_a_person_once(pg_session):
    session = pg_session
    await _user_with_rule(session)
    opp = await _opportunity(session, "pg-once", 80.0)
    await session.commit()
    event = (await detect_changes(session, opportunity=opp, previous={"opportunity_score": 50.0}))[0]
    await session.commit()

    for _ in range(4):
        await dispatch(session, events=[(event, opp)])
        await session.commit()

    total = (await session.execute(sa.select(sa.func.count()).select_from(AlertDelivery))).scalar_one()
    distinct = (
        await session.execute(sa.select(sa.func.count(sa.distinct(AlertDelivery.dedupe_key))))
    ).scalar_one()
    assert total == 1, f"four dispatches produced {total} deliveries"
    assert total == distinct


async def test_a_batch_of_new_events_all_deliver(pg_session):
    """The control: without duplicates, nothing is suppressed."""
    session = pg_session
    await _user_with_rule(session)
    events = []
    for index in range(5):
        opp = await _opportunity(session, f"pg-batch-{index}", 80.0 + index)
        await session.commit()
        made = await detect_changes(session, opportunity=opp, previous={"opportunity_score": 50.0})
        events.append((made[0], opp))
    await session.commit()

    result = await dispatch(session, events=events)
    await session.commit()
    assert result.sent == 5
    assert result.suppressed == 0


async def test_the_risk_level_repair_converts_only_current_state(pg_session):
    """Migration 0005: repair what is true now, never rewrite history.

    A historical decision row records what the system said when a person decided.
    Rewriting it to satisfy a later enum would hide the defect from exactly the
    Phase 6 backtesting that would catch its consequences.
    """
    session = pg_session
    user = await _user_with_rule(session)
    opp = await _opportunity(session, "pg-risk", 70.0)
    await session.commit()

    # A stale current-state value, and a historical record of the same string.
    await session.execute(
        sa.text("UPDATE opportunities SET risk_level = 'medium' WHERE id = :id"),
        {"id": opp.id},
    )
    await session.execute(
        sa.text(
            "INSERT INTO opportunity_decisions "
            "(id, opportunity_id, user_id, interest, decided_at, score_at_decision, "
            " confidence_at_decision, risk_at_decision, state_at_decision, "
            " algorithm_version, created_at) "
            "VALUES (:id, :opp, :user, 'watching', now(), 70, 70, 'medium', "
            "'candidate', '1.0.0', now())"
        ),
        {"id": uuid.uuid4(), "opp": opp.id, "user": user.id},
    )
    await session.commit()

    for table, column in (
        ("opportunities", "risk_level"),
        ("user_preferences", "max_risk_level"),
        ("user_profiles", "max_risk_level"),
        ("watchlists", "max_risk_level"),
    ):
        await session.execute(
            sa.text(f"UPDATE {table} SET {column} = 'moderate' WHERE {column} = 'medium'")  # noqa: S608
        )
    await session.commit()

    repaired = (
        await session.execute(sa.text("SELECT risk_level FROM opportunities WHERE id = :id"), {"id": opp.id})
    ).scalar_one()
    preserved = (
        await session.execute(sa.text("SELECT risk_at_decision FROM opportunity_decisions LIMIT 1"))
    ).scalar_one()

    assert repaired == "moderate", "current state must be repaired"
    assert preserved == "medium", "history must be left exactly as it was recorded"
