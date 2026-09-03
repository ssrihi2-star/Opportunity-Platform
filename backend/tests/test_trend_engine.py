"""End-to-end: the demo scenarios must come out with the right answers.

This is the test that matters most. Each scenario has a known correct reading, and
if the fake viral token ever scores like the genuine trend, this fails.
"""

import pytest
import sqlalchemy as sa

from app.models.models import Source, Trend, TrendSignal, TrendSnapshot
from app.services.ingestion import run_source
from app.services.topics import rebuild_topics
from app.services.trends import evaluate_trends
from app.sources.adapters.scenario import STREAMS

pytestmark = pytest.mark.asyncio

#: Mirrors scripts/seed.py. A stream with no entry falls back to 0.5 so adding a
#: scenario never silently breaks this test with a KeyError.
RELIABILITY = {
    "github": 0.80,
    "news": 0.60,
    "wikipedia": 0.70,
    "jobs": 0.75,
    "packages": 0.80,
    "trade": 0.90,
    "customs_value": 0.90,
    "social": 0.35,
    "news_wire": 0.45,
    "complaints": 0.55,
    "sales": 0.85,
    "capex": 0.80,
    "filings": 0.95,
    "filings2": 0.95,
    "coverage": 0.55,
    "trade_cn": 0.90,
    "trade_eu": 0.90,
    "suppliers": 0.75,
    "local": 0.85,
    "attention": 0.55,
    "patents": 0.85,
    "volume": 0.40,
    "onchain": 0.75,
}


async def _run_scenarios(session, days: int = 70):
    for scenario, streams in STREAMS.items():
        for stream in streams:
            session.add(
                Source(
                    slug=f"sc_{scenario}_{stream}",
                    name=f"{scenario}/{stream}",
                    adapter_key="scenario",
                    source_group=f"stream_{stream}",
                    source_class="demo",
                    reliability=RELIABILITY.get(stream, 0.5),
                    config={"scenario": scenario, "stream": stream, "days": days, "months": 37},
                )
            )
    await session.commit()
    sources = (await session.execute(sa.select(Source))).scalars().all()
    for source in sources:
        await run_source(session, source, trigger="test")
    await session.commit()
    await rebuild_topics(session)
    await session.commit()
    trends = await evaluate_trends(session)
    await session.commit()
    return {t.name: t for t in trends}


@pytest.fixture(scope="module")
def _slow_marker():
    return None


async def test_five_scenarios_are_classified_correctly(session):
    trends = await _run_scenarios(session)

    real = trends["AI coding agents"]
    hype = trends["ZephyrCoin"]
    steady = trends["mycelium packaging"]
    declining = trends["DVD authoring software"]
    seasonal = trends["ceramic floor tiles"]

    # 1. a genuine, corroborated, accelerating trend
    assert real.stage == "accelerating"
    assert real.trend_score >= 55
    assert real.confidence >= 60
    assert real.independent_source_count >= 3
    assert real.is_spike is False

    # 2. fake viral hype: enormous percentage growth, no substance
    assert hype.is_spike is True
    assert hype.trend_score <= 20
    assert hype.state == "candidate"
    assert "one_day_spike" in hype.penalties
    assert hype.trend_score < steady.trend_score, "a one-day spike must never outrank ordinary steady growth"

    # 3. steady, unspectacular growth
    assert steady.stage in {"emerging", "early_adoption"}
    assert 25 <= steady.trend_score <= 70

    # 4. activity falling away
    assert declining.stage == "declining"
    assert declining.state in {"weakening", "candidate"}

    # 5. a product that peaks every year
    assert seasonal.is_seasonal is True
    assert seasonal.state == "candidate", "a season is not a new trend"
    assert any("every year" in w or "seasonal" in w.lower() for w in seasonal.warnings)


async def test_the_real_trend_beats_every_other_scenario(session):
    trends = await _run_scenarios(session)
    real = trends["AI coding agents"]
    for name in ("ZephyrCoin", "DVD authoring software", "ceramic floor tiles"):
        assert real.trend_score > trends[name].trend_score


async def test_evaluating_twice_updates_rather_than_duplicates(session):
    await _run_scenarios(session)
    first_count = (await session.execute(sa.select(sa.func.count(Trend.id)))).scalar_one()
    first_snapshots = (await session.execute(sa.select(sa.func.count(TrendSnapshot.id)))).scalar_one()

    await evaluate_trends(session)
    await session.commit()

    assert (await session.execute(sa.select(sa.func.count(Trend.id)))).scalar_one() == first_count
    assert (
        await session.execute(sa.select(sa.func.count(TrendSnapshot.id)))
    ).scalar_one() > first_snapshots, "each evaluation appends to the history"


async def test_supporting_signals_are_recorded_with_their_owners(session):
    trends = await _run_scenarios(session)
    real = trends["AI coding agents"]
    links = (
        (await session.execute(sa.select(TrendSignal).where(TrendSignal.trend_id == real.id))).scalars().all()
    )
    assert len(links) >= 3
    assert len({link.source_group for link in links}) >= 3
    assert any(link.is_proxy for link in links), "the Wikipedia stream is a proxy and is marked"


async def test_a_topic_is_formed_and_scored(session):
    trends = await _run_scenarios(session)
    topics = [t for t in trends.values() if t.subject_type == "topic"]
    assert topics, "the three AI entities should cluster into a topic"
    assert all(t.trend_score >= 0 for t in topics)


async def test_syndicated_coverage_is_penalised(session):
    trends = await _run_scenarios(session)
    hype = trends["ZephyrCoin"]
    assert hype.metrics["duplication_ratio"] >= 0.4
    assert "duplicate_information" in hype.penalties
    assert any("republished" in w for w in hype.warnings)
