"""The default surfaces read live evidence only.

Trends and Opportunities let a reader choose their evidence; the personal feed,
scheduled alerts and scheduled digests do not. Those three arrive unbidden —
often as a Telegram message on a phone — with no dropdown beside them saying
what the number was computed from. So they are pinned to `live_only`.

Two properties are defended here, and they are different claims:

1. **Demo-inclusive candidates cannot enter these surfaces.** Not merely that
   they are labelled once they arrive — that they never arrive.
2. **An empty live-only result stays empty.** No fallback, no top-up, no
   "nothing live so here is a demo instead". An empty feed is a true statement
   about the evidence, and replacing it with demo rows would turn an honest
   silence into a false claim.

What is deliberately *not* asserted: that stored history is altered. Old demo
events and old demo deliveries stay in the database exactly as recorded. The
rule is about what may be read into a new feed, alert or digest, never about
deleting the record of what the system previously said.
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta

import pytest
import sqlalchemy as sa
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.enums import AnalysisMode
from app.models.models import (
    AlertDelivery,
    AlertRule,
    ConditionCheck,
    Digest,
    Opportunity,
    OpportunityChangeEvent,
    OpportunityCondition,
    User,
    UserOpportunityRelevance,
)
from app.services.alerts import build_digest, digest_period, dispatch, run_digests
from app.services.monitoring import detect_changes, monitor_all, recent_events
from app.services.profiles import build_context, ensure_profile
from app.services.surfaces import DEFAULT_SURFACE_MODE
from app.services.user_relevance import refresh_user
from tests.test_multi_user import make_opportunity

pytestmark = pytest.mark.asyncio


PROFILE = {
    "home_country": "LY",
    "residence_country": "LY",
    "target_countries": ["LY"],
    "industries": ["technology"],
    "skills": ["operations"],
    "capital_available_usd": 50_000.0,
    "risk_tolerance": "moderate",
    "time_commitment": "full_time",
}


async def make_rule(session: AsyncSession, user: User) -> AlertRule:
    rule = AlertRule(
        user_id=user.id,
        name="Any move",
        trigger="score_threshold",
        enabled=True,
        conditions={},
        channels=["in_app"],
        cooldown_hours=0,
    )
    session.add(rule)
    await session.commit()
    return rule


# ------------------------------------------------------------------ the pinning
@pytest.mark.asyncio(loop_scope="function")
async def test_the_push_surfaces_are_pinned_to_live_only():
    """One constant, so the surfaces cannot drift apart from each other.

    Stated as its own test because the value is a product decision, not an
    implementation detail: changing it silently changes what arrives in
    somebody's Telegram.
    """
    assert DEFAULT_SURFACE_MODE == AnalysisMode.LIVE_ONLY


# ------------------------------------------------------------------- /for-you
async def test_a_demo_candidate_never_enters_the_personal_feed(client, admin_headers, session):
    """The strong form: high score, perfect profile fit, still excluded.

    The candidate is made maximally attractive on every axis the feed ranks by,
    so that if it is absent it is absent because of its evidence and nothing
    else.
    """
    await client.put("/api/v1/me/profile", headers=admin_headers, json=PROFILE)
    live = await make_opportunity(
        session,
        slug="surface-live",
        title="Live candidate",
        score=60.0,
        country="LY",
        analysis_mode=AnalysisMode.LIVE_ONLY,
    )
    await make_opportunity(
        session,
        slug="surface-demo",
        title="Demo candidate",
        score=99.0,
        country="LY",
        analysis_mode=AnalysisMode.DEMO_INCLUSIVE,
    )

    body = (await client.get("/api/v1/for-you", headers=admin_headers)).json()
    slugs = {item["slug"] for item in body["items"]}

    assert slugs == {"surface-live"}
    assert all(item["analysis_mode"] == AnalysisMode.LIVE_ONLY for item in body["items"])
    # The demo row is not deleted, hidden or downgraded — it is simply not feed material.
    assert (await session.get(Opportunity, live.id)) is not None
    stored = (await session.execute(sa.select(Opportunity))).scalars().all()
    assert len(stored) == 2


async def test_the_demo_candidate_is_still_reachable_where_the_mode_is_explicit(
    client, admin_headers, session
):
    """Excluded from the feed is not excluded from the product.

    The counterpart to the test above: this is what keeps the change a routing
    decision rather than a quiet deletion of half the corpus.
    """
    await make_opportunity(
        session,
        slug="surface-demo-visible",
        title="Demo candidate",
        score=99.0,
        analysis_mode=AnalysisMode.DEMO_INCLUSIVE,
    )

    listing = (
        await client.get(
            "/api/v1/opportunities?analysis_mode=demo_inclusive", headers=admin_headers
        )
    ).json()

    assert "surface-demo-visible" in {item["slug"] for item in listing["items"]}


async def test_an_empty_live_set_returns_empty_rather_than_demo_rows(client, admin_headers, session):
    """No fallback. The honest answer to "nothing live yet" is nothing."""
    await client.put("/api/v1/me/profile", headers=admin_headers, json=PROFILE)
    for index in range(3):
        await make_opportunity(
            session,
            slug=f"only-demo-{index}",
            title=f"Demo {index}",
            score=95.0,
            country="LY",
            analysis_mode=AnalysisMode.DEMO_INCLUSIVE,
        )

    body = (await client.get("/api/v1/for-you", headers=admin_headers)).json()

    assert body["items"] == []
    assert body["total"] == 0


async def test_relevance_is_not_computed_for_demo_candidates(session, admin_user):
    """Stored relevance is only ever read by live-only surfaces.

    Writing it for a demo candidate would leave a row whose only possible future
    use is to be mistaken for something worth showing.
    """
    await make_opportunity(
        session, slug="rel-live", title="Live", score=70.0, analysis_mode=AnalysisMode.LIVE_ONLY
    )
    await make_opportunity(
        session,
        slug="rel-demo",
        title="Demo",
        score=90.0,
        analysis_mode=AnalysisMode.DEMO_INCLUSIVE,
    )
    profile = await ensure_profile(session, admin_user)
    context = await build_context(session, profile)

    count = await refresh_user(session, user_id=admin_user.id, user=context)
    await session.commit()

    assert count == 1
    scored = (
        await session.execute(
            sa.select(Opportunity.slug)
            .join(UserOpportunityRelevance, UserOpportunityRelevance.opportunity_id == Opportunity.id)
            .where(UserOpportunityRelevance.user_id == admin_user.id)
        )
    ).scalars().all()
    assert scored == ["rel-live"]


# -------------------------------------------------------------------- alerts
async def test_monitoring_does_not_generate_events_for_demo_candidates(session):
    """Filtered at the source, so no demo event is ever written to be found later."""
    live = await make_opportunity(
        session, slug="mon-live", title="Live", score=70.0, analysis_mode=AnalysisMode.LIVE_ONLY
    )
    demo = await make_opportunity(
        session, slug="mon-demo", title="Demo", score=70.0, analysis_mode=AnalysisMode.DEMO_INCLUSIVE
    )
    # Both are equally checkable; only the mode separates them.
    for opp in (live, demo):
        session.add(
            OpportunityCondition(
                opportunity_id=opp.id,
                kind="confirmation",
                description="Imports grow",
                measurable={"signal_type": "import_growth", "comparator": "gt", "value": 10.0},
            )
        )
    await session.commit()

    counts = await monitor_all(session)
    await session.commit()

    assert counts["opportunities"] == 1, "exactly one candidate is swept"
    # A count alone would pass whichever one was chosen, so name it.
    checked = (
        await session.execute(
            sa.select(Opportunity.slug)
            .join(OpportunityCondition, OpportunityCondition.opportunity_id == Opportunity.id)
            .join(ConditionCheck, ConditionCheck.condition_id == OpportunityCondition.id)
            .distinct()
        )
    ).scalars().all()
    assert checked == ["mon-live"]


async def test_a_pre_existing_demo_event_is_not_eligible_for_a_new_alert(session):
    """History is preserved; eligibility is decided on read.

    Events recorded against demo candidates before this rule existed are still in
    the table — deleting them to implement a display rule would destroy the record
    of what the system actually said. They are simply never picked up again.
    """
    demo = await make_opportunity(
        session,
        slug="old-demo",
        title="Old demo",
        score=80.0,
        analysis_mode=AnalysisMode.DEMO_INCLUSIVE,
    )
    live = await make_opportunity(
        session, slug="new-live", title="New live", score=80.0, analysis_mode=AnalysisMode.LIVE_ONLY
    )
    await detect_changes(session, opportunity=demo, previous={"opportunity_score": 50.0})
    await detect_changes(session, opportunity=live, previous={"opportunity_score": 50.0})
    await session.commit()

    stored = (await session.execute(sa.select(OpportunityChangeEvent))).scalars().all()
    assert len(stored) == 2, "both events remain on record"

    eligible = await recent_events(session, since=datetime.now(UTC) - timedelta(days=1))

    assert [opp.slug for _, opp in eligible] == ["new-live"]


async def test_dispatch_refuses_a_demo_event_even_when_handed_one(session, admin_user):
    """The last gate before something leaves for Telegram.

    `recent_events` already filters, but `dispatch` is reachable from the admin
    monitoring endpoint too, and a delivery is the one action here that cannot be
    undone once sent. Re-checking an in-memory attribute is cheaper than trusting
    every present and future caller.
    """
    demo = await make_opportunity(
        session,
        slug="dispatch-demo",
        title="Demo",
        score=80.0,
        analysis_mode=AnalysisMode.DEMO_INCLUSIVE,
    )
    await make_rule(session, admin_user)
    events = await detect_changes(session, opportunity=demo, previous={"opportunity_score": 50.0})
    await session.commit()

    outcome = await dispatch(session, events=[(events[0], demo)])
    await session.commit()

    assert outcome.sent == 0
    assert (await session.execute(sa.select(AlertDelivery))).scalars().all() == []


# ------------------------------------------------------------------- digests
async def test_a_digest_is_built_only_from_live_evidence(session, admin_user):
    """Both halves of the digest are filtered, not just the newly written one.

    A digest is assembled from history. Filtering only new events would let an
    alert recorded against a demo candidate reappear inside a freshly generated
    summary, which is the exact leak this guards.
    """
    live = await make_opportunity(
        session, slug="dig-live", title="Live mover", score=80.0, analysis_mode=AnalysisMode.LIVE_ONLY
    )
    demo = await make_opportunity(
        session,
        slug="dig-demo",
        title="Demo mover",
        score=80.0,
        analysis_mode=AnalysisMode.DEMO_INCLUSIVE,
    )
    for opp in (live, demo):
        await detect_changes(session, opportunity=opp, previous={"opportunity_score": 50.0})
    await session.commit()

    digest = await build_digest(session, user=admin_user, frequency="daily")
    await session.commit()

    titles = [
        item["title"]
        for section in ("watchlist_changes", "other_changes")
        for item in digest.sections.get(section, [])
    ]
    assert titles == ["Live mover"]


async def test_a_recorded_demo_delivery_does_not_reappear_in_a_new_digest(session, admin_user):
    """The historical-delivery half of the same rule.

    An `AlertDelivery` has no mode of its own; it is judged by the opportunity it
    points at. Deliveries with no opportunity — rule-level or system notices —
    are kept, because they are not demo results and dropping them would silently
    lose real ones.
    """
    demo = await make_opportunity(
        session,
        slug="deliv-demo",
        title="Demo delivered",
        score=80.0,
        analysis_mode=AnalysisMode.DEMO_INCLUSIVE,
    )
    rule = await make_rule(session, admin_user)
    session.add(
        AlertDelivery(
            user_id=admin_user.id,
            rule_id=rule.id,
            opportunity_id=demo.id,
            channel="in_app",
            dedupe_key="legacy-demo-delivery",
            status="sent",
            title="Demo delivered",
            body="recorded before the surface rule existed",
        )
    )
    await session.commit()

    digest = await build_digest(session, user=admin_user, frequency="daily")
    await session.commit()

    rendered = str(digest.sections)
    assert "Demo delivered" not in rendered
    # Still on record. The rule governs what may be read, never what is kept.
    surviving = (await session.execute(sa.select(AlertDelivery))).scalars().all()
    assert len(surviving) == 1


async def test_an_all_demo_period_produces_an_honest_empty_digest(session, admin_user):
    """Not silence, and not a demo top-up: an explicit "nothing to report"."""
    demo = await make_opportunity(
        session,
        slug="quiet-demo",
        title="Demo mover",
        score=80.0,
        analysis_mode=AnalysisMode.DEMO_INCLUSIVE,
    )
    await detect_changes(session, opportunity=demo, previous={"opportunity_score": 50.0})
    await session.commit()

    digest = await build_digest(session, user=admin_user, frequency="daily")
    await session.commit()

    assert digest is not None
    assert digest.item_count == 0
    assert digest.sections.get("empty_note")


async def test_the_scheduled_digest_period_is_still_written_only_once(session, admin_user):
    """Filtering content must not turn into re-sending periods.

    The dedupe guarantee belongs to the *scheduled* path, which carries a
    `DigestPeriod` and a `period_key`; the on-demand endpoint is deliberately
    re-runnable and is not what this defends. If mode filtering had been
    implemented by rebuilding or re-keying digests, an already-delivered day
    could be written a second time — somebody receiving yesterday's digest
    twice. It is asserted here because this change touched the query that
    populates exactly those rows.
    """
    live = await make_opportunity(
        session,
        slug="policy-live",
        title="Live mover",
        score=80.0,
        analysis_mode=AnalysisMode.LIVE_ONLY,
    )
    await detect_changes(session, opportunity=live, previous={"opportunity_score": 50.0})
    await session.commit()

    profile = await ensure_profile(session, admin_user)
    profile.digest_frequency = "daily"
    await session.commit()

    period = digest_period("daily", now=datetime.now(UTC) + timedelta(days=1))
    assert period is not None

    first = await run_digests(session, frequency="daily", period=period)
    await session.commit()
    second = await run_digests(session, frequency="daily", period=period)
    await session.commit()

    assert first.written == 1
    assert second.written == 0, "the period is not regenerated"
    assert second.duplicates == 1, "the second attempt is a recognised duplicate, not a failure"
    rows = (
        await session.execute(sa.select(Digest).where(Digest.period_key == period.key))
    ).scalars().all()
    assert len(rows) == 1
    assert [item["title"] for item in rows[0].sections["other_changes"]] == ["Live mover"]
