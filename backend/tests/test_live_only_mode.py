"""Live-only analysis must exclude demo evidence from the numbers, not just the list.

The failure this guards against is concrete. Hacker News and Wikipedia collection
started working, and their observations landed in the same tables as the demo
scenarios and the seeded manual CSV. The "Pump Heat" trend then reported
confidence 93 over a mixture of the two, and there was no way to ask what the
live evidence alone supported.

Every test here builds the same deliberately mixed corpus — live-capable
adapters, a demo generator, a scenario stream, and a manual CSV import — and
then checks that live-only mode removed the demo evidence *before* anything was
measured. Checking the displayed source list alone would pass even if every
score were still computed from the mix, so the assertions are on the corroboration
counts, the observation counts and the stored trend_signals rows.
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta

import pytest
import sqlalchemy as sa

from app.models.enums import AnalysisMode, ValidationStatus
from app.models.models import (
    Entity,
    Opportunity,
    Signal,
    SignalObservation,
    Source,
    Trend,
    TrendSignal,
)
from app.services.opportunities import generate_opportunities
from app.services.trends import evaluate_trends
from app.sources.provenance import is_live_source, live_eligibility

pytestmark = pytest.mark.asyncio

#: Enough points to clear MIN_OBSERVATIONS with room for a growth window.
POINTS = 40


async def _entity(session, name: str, entity_type: str = "product") -> Entity:
    entity = Entity(entity_type=entity_type, canonical_name=name, normalized=name.lower())
    session.add(entity)
    await session.flush()
    return entity


async def _series(
    session,
    *,
    entity: Entity,
    source: Source,
    signal_type: str,
    base: float = 100.0,
    growth: float = 0.02,
    is_proxy: bool = False,
) -> Signal:
    """A clean rising series, so nothing fails for want of a measurable trend."""
    signal = Signal(
        entity_id=entity.id,
        signal_type=signal_type,
        signal_class="attention",
        geo_scope="global",
        source_id=source.id,
        unit="count",
        is_proxy=is_proxy,
    )
    session.add(signal)
    await session.flush()

    start = datetime.now(UTC) - timedelta(days=POINTS)
    for day in range(POINTS):
        session.add(
            SignalObservation(
                signal_id=signal.id,
                observed_at=start + timedelta(days=day),
                value=base * (1.0 + growth) ** day,
                status="ok",
                confidence=0.7,
                source_reliability=source.reliability,
                is_proxy=is_proxy,
                collected_at=start + timedelta(days=day),
            )
        )
    await session.flush()
    return signal


async def _mixed_corpus(session) -> dict[str, Source]:
    """One subject measured by live sources, demo sources and a manual CSV at once.

    This is the shape that produced the reported bug: a single entity whose
    corroboration count is inflated by evidence nobody collected from anywhere.
    """
    sources = {
        # --- genuinely live -------------------------------------------------
        "hn": Source(
            slug="hn_keywords",
            name="Hacker News",
            adapter_key="hackernews",
            source_group="hackernews",
            source_class="forum_social",
            reliability=0.4,
        ),
        "wiki": Source(
            slug="wikipedia_attention",
            name="Wikipedia pageviews",
            adapter_key="wikipedia_pageviews",
            source_group="wikimedia",
            # NOTE: "official" — a trust label about Wikimedia, not proof that a
            # row was fetched. Included so the test proves the rule does not key
            # off source_class alone.
            source_class="official",
            reliability=0.7,
        ),
        # --- not live -------------------------------------------------------
        "demo": Source(
            slug="demo_dev_signals",
            name="Demo generator",
            adapter_key="demo_mock",
            source_group="demo_a",
            source_class="demo",
            reliability=0.55,
        ),
        "scenario": Source(
            slug="scenario_solar_water_pumps_trade_cn",
            name="Demo scenario",
            adapter_key="scenario",
            source_group="stream_trade_cn",
            source_class="demo",
            reliability=0.9,
        ),
        "csv": Source(
            slug="manual_csv",
            name="Manual CSV import",
            adapter_key="csv_import",
            source_group="manual",
            # As seeded: classified manual_import, which says nothing about demo
            # data, yet its rows are a hard-coded fixture in scripts/seed.py.
            source_class="manual_import",
            reliability=0.7,
        ),
    }
    for source in sources.values():
        session.add(source)
    await session.flush()

    entity = await _entity(session, "heat pump water heater")
    await _series(session, entity=entity, source=sources["hn"], signal_type="social_discussion_growth")
    await _series(
        session,
        entity=entity,
        source=sources["wiki"],
        signal_type="wiki_pageview_growth",
        is_proxy=True,
    )
    await _series(session, entity=entity, source=sources["demo"], signal_type="github_stars")
    await _series(session, entity=entity, source=sources["scenario"], signal_type="import_growth")
    await _series(session, entity=entity, source=sources["csv"], signal_type="supplier_count_change")
    await session.commit()
    return sources


def _by_name(trends: list[Trend], name: str) -> Trend:
    for trend in trends:
        if trend.name == name:
            return trend
    raise AssertionError(f"no trend named {name!r}; got {[t.name for t in trends]}")


# --------------------------------------------------------------- the rule itself
@pytest.mark.filterwarnings("ignore::pytest.PytestWarning")
async def test_the_eligibility_rule_is_the_adapter_not_the_source_class():
    """Provenance is decided by whether the adapter can reach a live upstream."""
    # Live: network adapters, whatever their trust label.
    assert is_live_source("hackernews", "forum_social")
    assert is_live_source("wikipedia_pageviews", "official")
    assert is_live_source("github", "primary_api")

    # An "official" class does NOT make an offline adapter live. This is the
    # case the clarification called out explicitly.
    assert not is_live_source("csv_import", "official")

    # Seeded manual_csv: excluded from live-only for this MVP.
    eligible, reason = live_eligibility("csv_import", "manual_import")
    assert not eligible
    assert "never contacts a live source" in reason

    # Generators, refused by class and independently by adapter name.
    assert not is_live_source("demo_mock", "demo")
    assert not is_live_source("scenario", "demo")
    assert not is_live_source("hackernews", "demo"), "a demo-classed source is never live"

    # Unknown adapters fail closed rather than being assumed live.
    assert not is_live_source("something_invented", "official")


# ------------------------------------------------------------------- the engine
async def test_live_only_excludes_demo_evidence_from_the_numbers(session):
    """The corroboration counts, not just the source list, must drop demo evidence."""
    await _mixed_corpus(session)

    mixed = await evaluate_trends(session, analysis_mode=AnalysisMode.DEMO_INCLUSIVE)
    await session.commit()
    live = await evaluate_trends(session, analysis_mode=AnalysisMode.LIVE_ONLY)
    await session.commit()

    mixed_trend = _by_name(mixed, "heat pump water heater")
    live_trend = _by_name(live, "heat pump water heater")

    assert mixed_trend.id != live_trend.id, "a live-only run must not overwrite the mixed row"
    assert mixed_trend.analysis_mode == AnalysisMode.DEMO_INCLUSIVE
    assert live_trend.analysis_mode == AnalysisMode.LIVE_ONLY

    # The corroboration count is an input to scoring, so this proves the demo
    # evidence was gone before the score was computed rather than hidden after.
    assert mixed_trend.independent_source_count == 5
    assert live_trend.independent_source_count == 2

    # Same for the raw evidence volume: two live series, not five.
    assert live_trend.observation_count == 2 * POINTS
    assert mixed_trend.observation_count == 5 * POINTS

    # And the metrics blob the UI reads its breakdown from.
    assert live_trend.metrics["independent_sources"] == 2
    assert live_trend.metrics["analysis_mode"] == AnalysisMode.LIVE_ONLY


async def test_live_only_trend_signals_contain_no_demo_source(session):
    """Displayed provenance and the evaluated evidence must be the same set."""
    await _mixed_corpus(session)
    await evaluate_trends(session, analysis_mode=AnalysisMode.DEMO_INCLUSIVE)
    await session.commit()
    live = await evaluate_trends(session, analysis_mode=AnalysisMode.LIVE_ONLY)
    await session.commit()

    live_trend = _by_name(live, "heat pump water heater")
    rows = (
        await session.execute(
            sa.select(Source.slug, Source.adapter_key, Source.source_class)
            .join(TrendSignal, TrendSignal.source_id == Source.id)
            .where(TrendSignal.trend_id == live_trend.id)
        )
    ).all()

    assert {slug for slug, _a, _c in rows} == {"hn_keywords", "wikipedia_attention"}
    for slug, adapter_key, source_class in rows:
        assert is_live_source(adapter_key, source_class), f"{slug} is not a live source"


async def test_the_mixed_result_survives_a_live_only_run(session):
    """Preserve demo data and existing results. No wipe, no relabelling."""
    await _mixed_corpus(session)
    mixed = await evaluate_trends(session, analysis_mode=AnalysisMode.DEMO_INCLUSIVE)
    await session.commit()
    before = {
        "id": _by_name(mixed, "heat pump water heater").id,
        "score": _by_name(mixed, "heat pump water heater").trend_score,
        "sources": _by_name(mixed, "heat pump water heater").independent_source_count,
    }

    await evaluate_trends(session, analysis_mode=AnalysisMode.LIVE_ONLY)
    await session.commit()

    after = await session.get(Trend, before["id"])
    assert after is not None, "the demo-inclusive trend must not be deleted"
    assert after.analysis_mode == AnalysisMode.DEMO_INCLUSIVE, "a mixed result stays labelled mixed"
    assert after.trend_score == before["score"]
    assert after.independent_source_count == before["sources"]

    # Every observation is still stored; nothing was deleted to make live-only work.
    assert (await session.execute(sa.select(sa.func.count(SignalObservation.id)))).scalar_one() == 5 * POINTS
    assert (await session.execute(sa.select(sa.func.count(Source.id)))).scalar_one() == 5


async def test_live_only_says_so_in_the_trend_warnings(session):
    """Insufficiency and exclusion are stated, never silently papered over."""
    await _mixed_corpus(session)
    live = await evaluate_trends(session, analysis_mode=AnalysisMode.LIVE_ONLY)
    await session.commit()

    warnings = " ".join(_by_name(live, "heat pump water heater").warnings)
    assert "Live-only analysis" in warnings
    assert "excluded" in warnings
    # The excluded sources are named, so a reader can judge what is missing.
    for slug in ("demo_dev_signals", "manual_csv"):
        assert slug in warnings


async def test_no_live_evidence_yields_no_live_trends_rather_than_demo_ones(session):
    """With only demo sources present, live-only must return nothing at all."""
    demo = Source(
        slug="demo_only",
        name="Demo generator",
        adapter_key="demo_mock",
        source_group="demo_a",
        source_class="demo",
        reliability=0.55,
    )
    session.add(demo)
    await session.flush()
    entity = await _entity(session, "solar water pumps")
    await _series(session, entity=entity, source=demo, signal_type="import_growth")
    await session.commit()

    mixed = await evaluate_trends(session, analysis_mode=AnalysisMode.DEMO_INCLUSIVE)
    await session.commit()
    live = await evaluate_trends(session, analysis_mode=AnalysisMode.LIVE_ONLY)
    await session.commit()

    assert len(mixed) == 1, "the demo-inclusive view is unchanged"
    assert live == [], "an empty live result is the honest answer, not a demo-backed one"


# ------------------------------------------------------------- the opportunities
async def test_live_only_opportunities_are_generated_from_live_trends_only(session):
    """Generation must inherit the mode, not re-widen to every stored trend."""
    await _mixed_corpus(session)
    await evaluate_trends(session, analysis_mode=AnalysisMode.DEMO_INCLUSIVE)
    await session.commit()
    await evaluate_trends(session, analysis_mode=AnalysisMode.LIVE_ONLY)
    await session.commit()

    result = await generate_opportunities(
        session,
        validation_status=ValidationStatus.UNVALIDATED,
        analysis_mode=AnalysisMode.LIVE_ONLY,
    )
    await session.commit()

    produced = result.created + result.updated
    for opp in produced:
        assert opp.analysis_mode == AnalysisMode.LIVE_ONLY
        trend = await session.get(Trend, opp.primary_trend_id)
        assert trend is not None
        assert trend.analysis_mode == AnalysisMode.LIVE_ONLY

    # Whatever it produced or refused, it must have been reasoning about the
    # live-only trends and no others.
    considered = {r.trend_id for r in result.rejections} | {str(o.primary_trend_id) for o in produced}
    demo_trend_ids = {
        str(row)
        for row in (
            await session.execute(
                sa.select(Trend.id).where(Trend.analysis_mode == AnalysisMode.DEMO_INCLUSIVE)
            )
        )
        .scalars()
        .all()
    }
    assert not (considered & demo_trend_ids)


async def test_live_only_generation_refuses_the_demo_scenario_contexts(session):
    """Hand-written scenario facts are demo evidence and must not leak back in."""
    from app.sources.adapters.scenario_context import SCENARIO_CONTEXT

    await _mixed_corpus(session)
    await evaluate_trends(session, analysis_mode=AnalysisMode.LIVE_ONLY)
    await session.commit()

    # Passing the demo contexts in must not change a live-only outcome, because
    # the generator drops them. Compare against a run given none at all.
    with_contexts = await generate_opportunities(
        session,
        contexts=SCENARIO_CONTEXT,
        validation_status=ValidationStatus.UNVALIDATED,
        analysis_mode=AnalysisMode.LIVE_ONLY,
    )
    await session.commit()
    scores_with = {o.slug: o.opportunity_score for o in with_contexts.created + with_contexts.updated}

    without = await generate_opportunities(
        session,
        contexts=None,
        validation_status=ValidationStatus.UNVALIDATED,
        analysis_mode=AnalysisMode.LIVE_ONLY,
    )
    await session.commit()
    scores_without = {o.slug: o.opportunity_score for o in without.created + without.updated}

    assert scores_with == scores_without


async def test_demo_inclusive_generation_still_uses_its_contexts(session):
    """The demo path is untouched: contexts still reach the analyzer there."""
    await _mixed_corpus(session)
    await evaluate_trends(session, analysis_mode=AnalysisMode.DEMO_INCLUSIVE)
    await session.commit()

    result = await generate_opportunities(
        session,
        contexts={
            "heat pump water heater": {
                "market_size_band": "medium",
                "market_size_evidence": "test fixture",
                "has_commercial_data": True,
            }
        },
        validation_status=ValidationStatus.DEMO,
        analysis_mode=AnalysisMode.DEMO_INCLUSIVE,
    )
    await session.commit()

    for opp in result.created + result.updated:
        assert opp.analysis_mode == AnalysisMode.DEMO_INCLUSIVE
        assert opp.validation_status == ValidationStatus.DEMO


# ------------------------------------------------------------------- the API
async def test_the_api_keeps_the_two_modes_apart(session, client, admin_headers):
    await _mixed_corpus(session)

    mixed = await client.post(
        "/api/v1/trends/evaluate?analysis_mode=demo_inclusive", headers=admin_headers
    )
    assert mixed.status_code == 200, mixed.text
    assert mixed.json()["analysis_mode"] == "demo_inclusive"

    live = await client.post("/api/v1/trends/evaluate?analysis_mode=live_only", headers=admin_headers)
    assert live.status_code == 200, live.text
    assert live.json()["analysis_mode"] == "live_only"

    listed_live = (
        await client.get("/api/v1/trends?analysis_mode=live_only", headers=admin_headers)
    ).json()
    listed_mixed = (
        await client.get("/api/v1/trends?analysis_mode=demo_inclusive", headers=admin_headers)
    ).json()

    assert listed_live["items"], "live-only evidence exists in this corpus"
    assert all(item["analysis_mode"] == "live_only" for item in listed_live["items"])
    assert all(item["analysis_mode"] == "demo_inclusive" for item in listed_mixed["items"])

    # The default is the mixed view, so existing callers see what they always saw.
    default = (await client.get("/api/v1/trends", headers=admin_headers)).json()
    assert all(item["analysis_mode"] == "demo_inclusive" for item in default["items"])

    # A live-only trend's displayed sources agree with what was evaluated.
    trend_id = listed_live["items"][0]["id"]
    detail = (await client.get(f"/api/v1/trends/{trend_id}", headers=admin_headers)).json()
    assert detail["signals"], "a live-only trend still shows its supporting series"
    assert all(sig["is_live_source"] for sig in detail["signals"])


async def test_the_api_reports_insufficient_live_evidence_plainly(session, client, admin_headers):
    """No live evidence must read as "we could not look", not as an empty list."""
    demo = Source(
        slug="demo_only",
        name="Demo generator",
        adapter_key="demo_mock",
        source_group="demo_a",
        source_class="demo",
        reliability=0.55,
    )
    session.add(demo)
    await session.flush()
    entity = await _entity(session, "solar water pumps")
    await _series(session, entity=entity, source=demo, signal_type="import_growth")
    await session.commit()

    evaluated = (
        await client.post("/api/v1/trends/evaluate?analysis_mode=live_only", headers=admin_headers)
    ).json()
    assert evaluated["evaluated"] == 0
    assert "no live source" in evaluated["detail"].lower()

    generated = (
        await client.post("/api/v1/opportunities/generate?analysis_mode=live_only", headers=admin_headers)
    ).json()
    assert generated["insufficient_live_evidence"] is True
    assert generated["created"] == 0
    assert "not enough eligible live evidence" in generated["detail"]

    # And the demo-inclusive path still works normally alongside it.
    demo_run = (
        await client.post(
            "/api/v1/opportunities/generate?analysis_mode=demo_inclusive", headers=admin_headers
        )
    ).json()
    assert demo_run["insufficient_live_evidence"] is False
    assert demo_run["validation_status"] == ValidationStatus.DEMO


async def test_listing_opportunities_never_mixes_the_two_modes(session, client, admin_headers):
    await _mixed_corpus(session)
    for mode in ("demo_inclusive", "live_only"):
        await client.post(f"/api/v1/trends/evaluate?analysis_mode={mode}", headers=admin_headers)
        await client.post(f"/api/v1/opportunities/generate?analysis_mode={mode}", headers=admin_headers)

    for mode in ("demo_inclusive", "live_only"):
        page = (
            await client.get(f"/api/v1/opportunities?analysis_mode={mode}", headers=admin_headers)
        ).json()
        assert all(item["analysis_mode"] == mode for item in page["items"])

    # Nothing generated under the mixed mode may ever be served as live-only.
    live_rows = (
        (
            await session.execute(
                sa.select(Opportunity).where(Opportunity.analysis_mode == AnalysisMode.LIVE_ONLY)
            )
        )
        .scalars()
        .all()
    )
    for opp in live_rows:
        trend = await session.get(Trend, opp.primary_trend_id)
        assert trend.analysis_mode == AnalysisMode.LIVE_ONLY
        assert opp.validation_status != ValidationStatus.DEMO
