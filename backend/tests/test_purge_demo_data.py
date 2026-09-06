"""The demo purge removes demo evidence and nothing else.

These pin the two ordering bugs found while rehearsing the script, both of which
were invisible in its own summary output and only showed up when the database
was inspected directly afterwards:

* `source_runs` was counted but never deleted. PostgreSQL would have cascaded it
  from `sources`, so the missing statement was masked; SQLite does not enforce
  the cascade, so the rows simply stayed.
* The all-demo trend set was identified by a subquery over `trend_signals`, but
  the deletes emptied `trend_signals` first, so by the time the trend delete ran
  the predicate matched nothing and every demo trend survived.

Both are the same class of mistake — a delete whose predicate depends on rows an
earlier delete already removed — which is why the survival assertions below check
row counts directly rather than trusting the script's report.
"""

from __future__ import annotations

import pytest
import sqlalchemy as sa
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.models import (
    Entity,
    Opportunity,
    Signal,
    SignalObservation,
    Source,
    SourceRun,
    Trend,
    TrendSignal,
    User,
    Watchlist,
    WatchlistItem,
)
from scripts.purge_demo_data import apply_purge, build_plan, classify_sources

pytestmark = pytest.mark.asyncio


async def _corpus(session: AsyncSession) -> dict[str, object]:
    """A live source, a demo source, and one trend fed by both."""
    from datetime import UTC, datetime

    now = datetime.now(UTC)
    live = Source(
        slug="live_github",
        name="GitHub",
        adapter_key="github",
        source_class="primary_api",
        enabled=True,
    )
    demo = Source(
        slug="demo_gen",
        name="Demo generator",
        adapter_key="demo_mock",
        source_class="demo",
        enabled=True,
    )
    csv = Source(
        slug="manual_csv",
        name="Seeded CSV",
        adapter_key="csv_import",
        source_class="manual_import",
        enabled=True,
    )
    session.add_all([live, demo, csv])
    await session.flush()

    entity = Entity(canonical_name="Widget", normalized="widget", entity_type="product")
    session.add(entity)
    await session.flush()

    # A run on each source: the bug was that demo runs were never deleted.
    for src in (live, demo):
        session.add(SourceRun(source_id=src.id, status="ok", started_at=now, finished_at=now))

    mixed = Trend(
        name="Mixed evidence trend",
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
    pure_demo = Trend(
        name="Demo-only trend",
        subject_type="topic",
        category="technology",
        geo_scope="global",
        trend_score=55.0,
        confidence=65.0,
        stage="early_adoption",
        state="active",
        first_detected_at=now,
        last_evaluated_at=now,
    )
    session.add_all([mixed, pure_demo])
    await session.flush()

    signals = {}
    for key, src in (("live", live), ("demo", demo), ("csv", csv)):
        sig = Signal(
            entity_id=entity.id,
            signal_type=f"{key}_metric",
            signal_class="adoption",
            geo_scope="global",
            source_id=src.id,
            is_proxy=False,
        )
        session.add(sig)
        await session.flush()
        signals[key] = sig
        session.add(
            SignalObservation(
                signal_id=sig.id,
                observed_at=now,
                value=10.0,
                status="ok",
                confidence=80.0,
                source_reliability=90.0,
                method="direct",
                is_proxy=False,
                collected_at=now,
            )
        )

    # mixed trend: one live signal + one demo signal. pure_demo: demo only.
    for trend, keys in ((mixed, ("live", "demo")), (pure_demo, ("demo", "csv"))):
        for key in keys:
            session.add(
                TrendSignal(
                    trend_id=trend.id,
                    signal_id=signals[key].id,
                    source_id=signals[key].source_id,
                    source_group=key,
                    signal_type=signals[key].signal_type,
                    growth_30d=5.0,
                    acceleration=1.0,
                    observation_count=1,
                    is_proxy=False,
                    counted_as_independent=True,
                    contribution=1.0,
                )
            )

    demo_opp = Opportunity(
        slug="demo-opp",
        title="Demo opportunity",
        summary="from a demo-only trend",
        opportunity_type="business",
        category="technology",
        industry="technology",
        geo_scope="global",
        state="candidate",
        validation_status="demo",
        maturity_stage="early_adoption",
        risk_level="moderate",
        opportunity_score=50.0,
        adjusted_score=50.0,
        raw_score=50.0,
        confidence=50.0,
        peak_score=50.0,
        primary_trend_id=pure_demo.id,
        detected_at=now,
        algorithm_version="1.0.0",
    )
    session.add(demo_opp)
    await session.flush()

    user = User(email="purge@example.com", password_hash="x", role="admin", is_active=True)
    session.add(user)
    await session.flush()
    watchlist = Watchlist(user_id=user.id, name="Mine")
    session.add(watchlist)
    await session.flush()
    session.add(
        WatchlistItem(
            watchlist_id=watchlist.id, item_type="opportunity", opportunity_id=demo_opp.id
        )
    )
    await session.commit()
    return {"mixed": mixed.id, "pure_demo": pure_demo.id, "watchlist": watchlist.id, "user": user.id}


async def test_the_plan_classifies_sources_by_the_live_only_rule(session):
    await _corpus(session)
    plan = await build_plan(session)

    assert [s[0] for s in plan.live_sources] == ["live_github"]
    removed = {s[0] for s in plan.demo_sources}
    assert removed == {"demo_gen", "manual_csv"}, "the seeded CSV is demo by the operator's decision"


async def test_a_dry_run_deletes_nothing(session):
    await _corpus(session)
    before = {
        t: (await session.execute(sa.text(f"SELECT count(*) FROM {t}"))).scalar()
        for t in ("sources", "trends", "opportunities", "signals", "source_runs", "watchlist_items")
    }

    await build_plan(session)

    after = {
        t: (await session.execute(sa.text(f"SELECT count(*) FROM {t}"))).scalar() for t in before
    }
    assert after == before


async def test_source_runs_are_actually_deleted(session):
    """Regression: they were counted in the plan but never deleted."""
    await _corpus(session)
    from scripts.purge_demo_data import Plan

    demo_ids = await classify_sources(session, Plan())

    await apply_purge(session, demo_ids)
    await session.commit()

    remaining = (
        await session.execute(
            sa.text(
                "SELECT count(*) FROM source_runs r JOIN sources s ON s.id = r.source_id "
                "WHERE s.adapter_key IN ('demo_mock','csv_import')"
            )
        )
    ).scalar()
    assert remaining == 0
    # The live source keeps its run.
    kept = (await session.execute(sa.text("SELECT count(*) FROM source_runs"))).scalar()
    assert kept == 1


async def test_all_demo_trends_are_actually_deleted(session):
    """Regression: the predicate read trend_signals, which an earlier delete emptied."""
    ids = await _corpus(session)
    from scripts.purge_demo_data import Plan

    demo_ids = await classify_sources(session, Plan())

    await apply_purge(session, demo_ids)
    await session.commit()

    assert (await session.get(Trend, ids["pure_demo"])) is None, "demo-only trend must be gone"
    assert (await session.get(Trend, ids["mixed"])) is not None, "mixed trend must survive"


async def test_a_mixed_trend_keeps_only_its_live_signals(session):
    """The whole reason for purging evidence rather than results."""
    ids = await _corpus(session)
    from scripts.purge_demo_data import Plan

    demo_ids = await classify_sources(session, Plan())

    await apply_purge(session, demo_ids)
    await session.commit()

    rows = (
        await session.execute(
            sa.text(
                "SELECT s.slug FROM trend_signals ts JOIN sources s ON s.id = ts.source_id "
                "WHERE ts.trend_id = :tid"
            ).bindparams(tid=ids["mixed"])
        )
    ).scalars().all()
    assert rows == ["live_github"]


async def test_user_data_survives_and_watchlists_keep_their_shape(session):
    ids = await _corpus(session)
    from scripts.purge_demo_data import Plan

    demo_ids = await classify_sources(session, Plan())

    await apply_purge(session, demo_ids)
    await session.commit()

    assert (await session.get(User, ids["user"])) is not None
    assert (await session.get(Watchlist, ids["watchlist"])) is not None
    # The entry pointing at a deleted demo opportunity is gone; the list is not.
    orphans = (
        await session.execute(
            sa.text(
                "SELECT count(*) FROM watchlist_items wi "
                "LEFT JOIN opportunities o ON o.id = wi.opportunity_id "
                "WHERE wi.opportunity_id IS NOT NULL AND o.id IS NULL"
            )
        )
    ).scalar()
    assert orphans == 0


async def test_the_purge_leaves_no_dangling_references(session):
    """The check that caught both original bugs, kept as a standing assertion."""
    await _corpus(session)
    from scripts.purge_demo_data import Plan

    demo_ids = await classify_sources(session, Plan())

    await apply_purge(session, demo_ids)
    await session.commit()

    for label, query in {
        "raw_records->sources": (
            "SELECT count(*) FROM raw_records r LEFT JOIN sources s ON s.id = r.source_id "
            "WHERE s.id IS NULL"
        ),
        "source_runs->sources": (
            "SELECT count(*) FROM source_runs r LEFT JOIN sources s ON s.id = r.source_id "
            "WHERE s.id IS NULL"
        ),
        "signals->sources": (
            "SELECT count(*) FROM signals g LEFT JOIN sources s ON s.id = g.source_id "
            "WHERE g.source_id IS NOT NULL AND s.id IS NULL"
        ),
        "observations->signals": (
            "SELECT count(*) FROM signal_observations o LEFT JOIN signals g ON g.id = o.signal_id "
            "WHERE g.id IS NULL"
        ),
        "trend_signals->trends": (
            "SELECT count(*) FROM trend_signals ts LEFT JOIN trends t ON t.id = ts.trend_id "
            "WHERE t.id IS NULL"
        ),
        "opportunities->trends": (
            "SELECT count(*) FROM opportunities o LEFT JOIN trends t ON t.id = o.primary_trend_id "
            "WHERE o.primary_trend_id IS NOT NULL AND t.id IS NULL"
        ),
    }.items():
        count = (await session.execute(sa.text(query))).scalar()
        assert count == 0, f"{label} left {count} dangling row(s)"
