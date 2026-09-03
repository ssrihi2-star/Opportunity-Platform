"""End to end: the six Phase 4 demo scenarios must reach the right verdicts.

Each scenario has a known correct reading. If the hype case ever scores like the
real one, or the saturated equity ever outranks the genuine build, this fails.
"""

from __future__ import annotations

import pytest
import sqlalchemy as sa

from app.models.enums import ValidationStatus
from app.models.models import (
    Opportunity,
    OpportunityCondition,
    OpportunityDecision,
    OpportunityParticipation,
    OpportunityRisk,
    OpportunityScore,
    SkepticReview,
    Source,
    Trend,
    User,
    UserPreference,
)
from app.services.ingestion import run_source
from app.services.opportunities import generate_opportunities
from app.services.topics import rebuild_topics
from app.services.trends import evaluate_trends
from app.sources.adapters.scenario import STREAMS
from app.sources.adapters.scenario_context import SCENARIO_CONTEXT
from tests.test_trend_engine import RELIABILITY

pytestmark = pytest.mark.asyncio


#: The same window the seed uses. A shorter one would test a differently-calibrated
#: system and pass or fail for reasons that have nothing to do with the code.
DAYS = 200


def _find(candidates, fragment: str):
    """Look a candidate up by title, failing with a useful message rather than
    a bare StopIteration when the engine legitimately produced nothing."""
    for candidate in candidates:
        if fragment.lower() in candidate.title.lower():
            return candidate
    raise AssertionError(
        f"no candidate matching {fragment!r}. Generated: " + ", ".join(sorted(c.title for c in candidates))
    )


async def _pipeline(session, days: int = DAYS):
    """Collect every scenario, build trends, then generate opportunities."""
    for scenario, streams in STREAMS.items():
        for stream in streams:
            session.add(
                Source(
                    slug=f"op_{scenario}_{stream}",
                    name=f"{scenario}/{stream}",
                    adapter_key="scenario",
                    source_group=f"stream_{stream}",
                    source_class="demo",
                    reliability=RELIABILITY.get(stream, 0.5),
                    config={"scenario": scenario, "stream": stream, "days": days, "months": 37},
                )
            )
    user = User(email="prefs@example.com", full_name="t", password_hash="x", role="admin", locale="en")
    session.add(user)
    await session.flush()
    session.add(
        UserPreference(
            user_id=user.id,
            countries=["LY", "TN", "CN"],
            priority_geographies=["LY", "TN", "CN"],
            type_priority=["import_distribution", "business", "public_investment", "crypto"],
            experience_industries=["import_distribution"],
            has_supplier_access=True,
            technical_ability="medium",
            regulatory_access=["LY", "TN"],
            capital_max_usd=15000,
        )
    )
    await session.commit()

    for source in (await session.execute(sa.select(Source))).scalars().all():
        await run_source(session, source, trigger="test")
    await session.commit()
    await rebuild_topics(session)
    await session.commit()
    await evaluate_trends(session)
    await session.commit()
    result = await generate_opportunities(
        session, contexts=SCENARIO_CONTEXT, validation_status=ValidationStatus.DEMO
    )
    await session.commit()
    return result


async def test_the_six_scenarios_reach_the_right_verdicts(session):
    result = await _pipeline(session)
    by_title = {o.title: o for o in result.created + result.updated}
    refused = {(r.trend_name, r.opportunity_type): r for r in result.rejections}

    # --- A: a real, buildable technology opportunity ------------------------
    made = list(by_title.values())
    a = _find(made, "Edge inference")
    assert a.opportunity_score >= 45, a.opportunity_score
    assert a.confidence >= 55
    assert a.state in {"promising", "watchlist", "strong_evidence"}
    assert a.opportunity_type == "business"

    # --- B: hype with nothing underneath ------------------------------------
    b = _find(made, "quantum wellness")
    assert b.opportunity_score < 25, b.opportunity_score
    assert b.state == "candidate"

    # --- C: real trend, poor investment (the important one) -----------------
    c = _find(made, "ThermaCore")
    assert c.opportunity_type == "public_investment"
    assert c.opportunity_score < 25, c.opportunity_score
    assert c.risk_level in {"high", "very_high"}
    assert "extreme_valuation" in c.penalties

    # --- D: geographic import opportunity -----------------------------------
    d = _find(made, "Solar water pumps")
    assert d.opportunity_type == "import_distribution"
    assert d.country == "LY" or d.geo_scope == "LY" or d.geographic_gap is not None
    assert d.opportunity_score > b.opportunity_score
    assert d.geographic_gap is not None and d.geographic_gap > 0
    assert d.why_early, "a geographic lag case must say why it may still be early"

    # --- E: a real trend with nowhere to stand ------------------------------
    assert ("EUV lithography capacity", "business") in refused
    reason = refused[("EUV lithography capacity", "business")].reasons[0]
    assert "no accessible way to take part" in reason

    # --- F: a scam-shaped token ---------------------------------------------
    f = _find(made, "Luna9")
    assert f.opportunity_type == "crypto"
    assert f.risk_level == "very_high"
    assert f.opportunity_score < 15, f.opportunity_score
    assert f.state in {"candidate", "invalidated"}

    # --- and the restraint that makes all of the above meaningful ----------

    trends = (await session.execute(sa.select(sa.func.count()).select_from(Trend))).scalar_one()
    assert result.rejections, "the engine must be capable of refusing"
    assert len(made) < trends, (
        f"{len(made)} opportunities from {trends} trends — the gate is not refusing anything"
    )

    for rejection in result.rejections:
        assert rejection.reasons
        assert all(len(r) > 25 for r in rejection.reasons)

    cryptos = (
        (await session.execute(sa.select(Opportunity).where(Opportunity.opportunity_type == "crypto")))
        .scalars()
        .all()
    )
    assert cryptos
    for opp in cryptos:
        assert opp.risk_level in {"high", "very_high"}


async def test_running_twice_updates_and_never_duplicates(session):
    """Also covers: each evaluation appends an immutable score snapshot."""
    first = await _pipeline(session)
    snapshots_before = (
        await session.execute(sa.select(sa.func.count()).select_from(OpportunityScore))
    ).scalar_one()
    before = (await session.execute(sa.select(sa.func.count()).select_from(Opportunity))).scalar_one()

    second = await generate_opportunities(
        session, contexts=SCENARIO_CONTEXT, validation_status=ValidationStatus.DEMO
    )
    await session.commit()
    after = (await session.execute(sa.select(sa.func.count()).select_from(Opportunity))).scalar_one()

    assert after == before
    assert not second.created
    assert len(second.updated) == len(first.created) + len(first.updated)

    snapshots_after = (
        await session.execute(sa.select(sa.func.count()).select_from(OpportunityScore))
    ).scalar_one()
    assert snapshots_after > snapshots_before, "history must accumulate, not be overwritten"


async def test_every_candidate_is_complete_and_says_nothing_promotional(session):
    """Also covers: DEMO labelling, and the sweep for forbidden wording."""
    result = await _pipeline(session)
    for opp in result.created:
        conditions = (
            (
                await session.execute(
                    sa.select(OpportunityCondition).where(OpportunityCondition.opportunity_id == opp.id)
                )
            )
            .scalars()
            .all()
        )
        kinds = {c.kind for c in conditions}
        assert "confirmation" in kinds, f"{opp.title} has nothing that would confirm it"
        assert "invalidation" in kinds, f"{opp.title} has nothing that would prove it wrong"

        participation = (
            (
                await session.execute(
                    sa.select(OpportunityParticipation).where(
                        OpportunityParticipation.opportunity_id == opp.id
                    )
                )
            )
            .scalars()
            .all()
        )
        assert participation, f"{opp.title} has no stated way to take part"

        skeptic = (
            (await session.execute(sa.select(SkepticReview).where(SkepticReview.opportunity_id == opp.id)))
            .scalars()
            .first()
        )
        assert skeptic is not None and skeptic.strongest_counterargument

        assert opp.validation_status == ValidationStatus.DEMO

    banned = ("guaranteed", "sure thing", "100x", "next bitcoin", "buy now", "risk-free")
    for opp in result.created:
        blob = " ".join(
            filter(
                None,
                [
                    opp.title,
                    opp.summary,
                    opp.thesis,
                    opp.counter_thesis,
                    opp.mechanism,
                    *opp.warnings,
                    *opp.why_early,
                    *opp.missing_evidence,
                    *opp.next_research_steps,
                ],
            )
        ).lower()
        for phrase in banned:
            assert phrase not in blob, f"{opp.title} contains {phrase!r}"

        risks = (
            (
                await session.execute(
                    sa.select(OpportunityRisk).where(OpportunityRisk.opportunity_id == opp.id)
                )
            )
            .scalars()
            .all()
        )
        for risk in risks:
            for phrase in banned:
                assert phrase not in risk.rationale.lower()


async def test_a_decision_freezes_the_numbers_as_they_stood(session):
    from datetime import UTC, datetime

    result = await _pipeline(session)
    opp = result.created[0]
    user = (await session.execute(sa.select(User))).scalars().first()

    session.add(
        OpportunityDecision(
            opportunity_id=opp.id,
            user_id=user.id,
            interest="researching",
            note="checking",
            decided_at=datetime.now(UTC),
            score_at_decision=opp.opportunity_score,
            confidence_at_decision=opp.confidence,
            risk_at_decision=opp.risk_level,
            relevance_at_decision=None,
            state_at_decision=opp.state,
            algorithm_version=opp.algorithm_version,
        )
    )
    await session.commit()

    original_score = opp.opportunity_score
    opp.opportunity_score = 1.0
    await session.commit()

    decision = (
        (
            await session.execute(
                sa.select(OpportunityDecision).where(OpportunityDecision.opportunity_id == opp.id)
            )
        )
        .scalars()
        .one()
    )
    assert decision.score_at_decision == original_score, (
        "the decision must remember what the system said at the time"
    )
