"""Seed demo data so the dashboard is usable with no API keys and no network.

Idempotent: running it twice does not duplicate anything.

    python -m scripts.seed

Offline sources are created *enabled* and run immediately. Every real network
source is created *disabled*, pre-configured with sensible defaults, so nothing
reaches the internet until the operator deliberately turns it on (and, where
needed, supplies a credential).
"""

from __future__ import annotations

import asyncio
import os
import sys
from datetime import UTC, datetime, timedelta

import sqlalchemy as sa

from app.ai.prompts import ALL_PROMPTS
from app.core.config import settings
from app.core.security import hash_password
from app.db.session import SessionLocal
from app.models.enums import AnalysisMode, Role, ValidationStatus
from app.models.models import Alert, PromptVersion, Source, User, UserPreference, Watchlist
from app.services.entities import resolve_entity
from app.services.ingestion import run_source
from app.services.opportunities import generate_opportunities
from app.services.topics import rebuild_topics
from app.services.trends import evaluate_trends
from app.sources.adapters.csv_import import parse_rows
from app.sources.adapters.scenario import STREAMS
from app.sources.adapters.scenario_context import SCENARIO_CONTEXT

DEMO_CSV = """observed_at,entity_name,entity_type,signal_type,value,previous_value,unit,geo_scope,note,is_proxy
{d0},heat pump water heater,product,import_growth,412,,tonnes,TN,Customs summary sheet supplied manually.,false
{d1},heat pump water heater,product,import_growth,455,412,tonnes,TN,Customs summary sheet supplied manually.,false
{d2},heat pump water heater,product,import_growth,503,455,tonnes,TN,Customs summary sheet supplied manually.,false
{d3},heat pump water heater,product,import_growth,548,503,tonnes,TN,Customs summary sheet supplied manually.,false
{d0},sanitary ware,product,supplier_count_change,31,,suppliers,LY,Trade fair exhibitor list.,false
{d1},sanitary ware,product,supplier_count_change,34,31,suppliers,LY,Trade fair exhibitor list.,false
{d2},sanitary ware,product,supplier_count_change,38,34,suppliers,LY,Trade fair exhibitor list.,false
{d3},sanitary ware,product,supplier_count_change,45,38,suppliers,LY,Trade fair exhibitor list.,false
{d0},AI workflow automation,industry,customer_complaint_frequency,54,,mentions,global,Public forum thread counts.,false
{d1},AI workflow automation,industry,customer_complaint_frequency,61,54,mentions,global,Public forum thread counts.,false
{d2},AI workflow automation,industry,customer_complaint_frequency,73,61,mentions,global,Public forum thread counts.,false
{d3},AI workflow automation,industry,customer_complaint_frequency,88,73,mentions,global,Public forum thread counts.,false
"""

#: How much each synthetic stream is trusted. Deliberately uneven: a rumour feed
#: and an official customs return should not carry the same weight.
SCENARIO_RELIABILITY = {
    "github": 0.80, "news": 0.60, "wikipedia": 0.70, "jobs": 0.75, "packages": 0.80,
    "trade": 0.90, "customs_value": 0.90, "social": 0.35, "news_wire": 0.45,
    # phase 4 streams
    "complaints": 0.55, "sales": 0.85, "capex": 0.80, "filings": 0.95, "filings2": 0.95,
    "coverage": 0.55, "trade_cn": 0.90, "trade_eu": 0.90, "suppliers": 0.75,
    "local": 0.85, "attention": 0.55, "patents": 0.85, "volume": 0.40, "onchain": 0.75,
}

SCENARIO_NOTES = {
    "ai_coding_agents": "Scenario 1 - a genuine accelerating trend across five independent streams.",
    "zephyr_token": "Scenario 2 - fake viral hype: one huge day plus the same story on five outlets.",
    "mycelium_packaging": "Scenario 3 - slow, steady, unremarkable growth.",
    "dvd_authoring": "Scenario 4 - activity gradually falling away.",
    "ceramic_tiles_tn": "Scenario 5 - a product that peaks the same month every year.",
    "edge_inference": "Phase 4 scenario A - real adoption accelerating ahead of attention.",
    "quantum_wellness": "Phase 4 scenario B - sustained hype over a flat order book.",
    "datacentre_cooling": "Phase 4 scenario C - real industry trend, poor investment case.",
    "solar_water_pumps": "Phase 4 scenario D - geographic lag: strong abroad, absent locally.",
    "euv_lithography": "Phase 4 scenario E - a real trend with no accessible way to take part.",
    "luna9_token": "Phase 4 scenario F - loud, illiquid, anonymous, concentrated.",
}


def scenario_sources() -> list[dict]:
    """One source per (scenario, stream), each with its own owner."""
    out: list[dict] = []
    for scenario, streams in STREAMS.items():
        for stream in streams:
            out.append(
                {
                    "slug": f"scenario_{scenario}_{stream}",
                    "name": f"Demo scenario: {scenario.replace('_', ' ')} / {stream}",
                    "adapter_key": "scenario",
                    # Grouping by stream, not by scenario, is what makes the five
                    # streams count as independent corroboration.
                    "source_group": f"stream_{stream}",
                    "source_class": "demo",
                    "reliability": SCENARIO_RELIABILITY.get(stream, 0.5),
                    "config": {"scenario": scenario, "stream": stream, "days": 200, "months": 37},
                    "notes": SCENARIO_NOTES[scenario],
                }
            )
    return out


OFFLINE_SOURCES = [
    {
        "slug": "demo_dev_signals",
        "name": "Demo generator - developer and attention signals",
        "adapter_key": "demo_mock",
        "source_group": "demo_a",
        "source_class": "demo",
        "reliability": 0.55,
        "config": {"days": 75, "seed": 20260801, "geo_scope": "global"},
        "notes": "Offline deterministic generator, so the dashboard works with no keys.",
    },
    {
        "slug": "demo_trade_signals",
        "name": "Demo generator - trade and industrial signals",
        "adapter_key": "demo_mock",
        "source_group": "demo_b",
        "source_class": "demo",
        "reliability": 0.5,
        "config": {"days": 75, "seed": 771003, "geo_scope": "TN"},
        "notes": "Second independent demo stream so the multi-source gate can be exercised.",
    },
    {
        "slug": "manual_csv",
        "name": "Manual CSV import (customs and field notes)",
        "adapter_key": "csv_import",
        "source_group": "manual",
        "source_class": "manual_import",
        "reliability": 0.7,
        "config": {},
        "notes": "For data you are entitled to use that has no compliant API. "
                 "Upload with POST /api/v1/sources/{id}/upload-csv.",
    },
]

# Real sources: configured, disabled, documented. Turn on deliberately.
NETWORK_SOURCES = [
    {
        "slug": "github_devtools",
        "name": "GitHub - local-first and sync tooling",
        "adapter_key": "github",
        "source_group": "github",
        "source_class": "primary_api",
        "reliability": 0.8,
        "rate_limit_per_minute": 60,
        "enabled": False,
        "config": {
            "repos": ["electric-sql/electric", "rocicorp/replicache", "yjs/yjs", "automerge/automerge"],
            "weeks": 52,
        },
        "notes": "Add a 'token' credential (fine-grained PAT, public read) before enabling: "
                 "GitHub allows only 60 requests/hour without one.",
    },
    {
        "slug": "hn_keywords",
        "name": "Hacker News - keyword discussion volume",
        "adapter_key": "hackernews",
        "source_group": "hackernews",
        "source_class": "forum_social",
        "reliability": 0.4,
        "rate_limit_per_minute": 20,
        "enabled": False,
        "config": {
            "keywords": ["local-first", "solid state battery", "heat pump", "AI automation agency"],
            "days": 120,
        },
        "notes": "No credential needed. Forum volume is weak evidence on its own - it exists "
                 "to corroborate other signals, never to carry an opportunity by itself.",
    },
    {
        "slug": "wikipedia_attention",
        "name": "Wikipedia pageviews - attention proxy",
        "adapter_key": "wikipedia_pageviews",
        "source_group": "wikimedia",
        "source_class": "official",
        "reliability": 0.7,
        "rate_limit_per_minute": 60,
        "enabled": False,
        "config": {
            "articles": [
                {"title": "Solid-state_battery", "entity_name": "solid-state battery",
                 "entity_type": "technology"},
                {"title": "Heat_pump", "entity_name": "heat pump", "entity_type": "product"},
                {"title": "Local-first_software", "entity_name": "local-first sync",
                 "entity_type": "technology"},
            ],
            "days": 180,
        },
        "notes": "Stands in for Google Trends, which has no free official API. Every "
                 "observation is flagged is_proxy=true and cannot alone satisfy the gate.",
    },
    {
        "slug": "regulator_feeds",
        "name": "RSS - regulators and trade press",
        "adapter_key": "rss",
        "source_group": "rss_mixed",
        "source_class": "aggregator",
        "reliability": 0.6,
        "rate_limit_per_minute": 20,
        "enabled": False,
        "config": {
            "feeds": [
                {"url": "https://www.federalregister.gov/api/v1/documents.rss?conditions%5Bterm%5D=heat+pump",
                 "entity_name": "heat pump", "entity_type": "product",
                 "signal_type": "regulatory_catalyst", "geo_scope": "US"},
            ],
            "days": 180,
        },
        "notes": "Honours robots.txt and stores only title, excerpt and link - never the "
                 "full article. Replace the feed list with the regulators you care about.",
    },
    {
        "slug": "sec_filings",
        "name": "SEC EDGAR - filings and reported revenue",
        "adapter_key": "sec_edgar",
        "source_group": "sec",
        "source_class": "regulated_filing",
        "reliability": 0.9,
        "rate_limit_per_minute": 60,
        "enabled": False,
        "config": {
            "companies": [
                {"cik": "0001318605", "name": "Tesla, Inc.", "ticker": "TSLA"},
                {"cik": "0001045810", "name": "NVIDIA Corporation", "ticker": "NVDA"},
            ],
            "concept": "Revenues",
            "taxonomy": "us-gaap",
            "days": 1460,
        },
        "notes": "No credential needed. SEC requires a descriptive User-Agent with contact "
                 "details - set USER_AGENT and CONTACT_EMAIL in .env before enabling.",
    },
    {
        "slug": "fred_macro",
        "name": "FRED - macro and producer prices",
        "adapter_key": "fred",
        "source_group": "fred",
        "source_class": "official",
        "reliability": 0.95,
        "rate_limit_per_minute": 60,
        "enabled": False,
        "config": {
            "series": [
                {"id": "PCU327320327320", "entity_name": "US ready-mix concrete PPI",
                 "signal_type": "producer_price_index", "geo_scope": "US"},
                {"id": "FEDFUNDS", "entity_name": "US federal funds rate",
                 "signal_type": "policy_rate", "geo_scope": "US"},
            ],
            "days": 3650,
        },
        "notes": "Needs a free 'api_key' credential from fred.stlouisfed.org.",
    },
    {
        "slug": "comtrade_north_africa",
        "name": "UN Comtrade - North Africa import flows",
        "adapter_key": "un_comtrade",
        "source_group": "comtrade",
        "source_class": "official",
        "reliability": 0.9,
        "rate_limit_per_minute": 10,
        "enabled": False,
        "config": {
            "flows": [
                {"reporter": "788", "partner": "0", "cmd_code": "6910",
                 "entity_name": "ceramic sanitary ware (Tunisia imports)",
                 "entity_type": "product", "geo_scope": "TN", "flow": "M"},
                {"reporter": "434", "partner": "0", "cmd_code": "6910",
                 "entity_name": "ceramic sanitary ware (Libya imports, mirrored)",
                 "entity_type": "product", "geo_scope": "LY", "flow": "M", "use_mirror": True},
            ],
            "years": 8,
            "frequency": "A",
        },
        "notes": "Needs a free 'subscription_key' credential. Libya reports irregularly, so "
                 "that flow uses partner mirror statistics - labelled as such in the payload.",
    },
]


async def seed() -> None:
    now = datetime.now(UTC).replace(hour=0, minute=0, second=0, microsecond=0)
    async with SessionLocal() as session:
        # --- admin user ---------------------------------------------------
        password = settings.ADMIN_PASSWORD or "ChangeMeNow!2026"
        if not settings.ADMIN_PASSWORD:
            print(
                "WARNING: ADMIN_PASSWORD is not set. Seeding with 'ChangeMeNow!2026'. "
                "Set ADMIN_PASSWORD in .env and change it immediately.",
                file=sys.stderr,
            )
        email = settings.ADMIN_EMAIL.lower()
        user = (
            await session.execute(sa.select(User).where(User.email == email))
        ).scalar_one_or_none()
        if user is None:
            user = User(
                email=email, full_name="Primary analyst", password_hash=hash_password(password),
                role=Role.ADMIN, locale="en",
            )
            session.add(user)
            await session.flush()
            print(f"created admin user {email}")

        pref = (
            await session.execute(sa.select(UserPreference).where(UserPreference.user_id == user.id))
        ).scalar_one_or_none()
        if pref is None:
            session.add(
                UserPreference(
                    user_id=user.id,
                    countries=["TN", "LY", "DZ", "MA", "EG", "CN", "US"],
                    industries=["construction", "smart_home", "developer_tools", "logistics", "energy"],
                    excluded_asset_types=["meme_coin"],
                    ethical_exclusions=["gambling", "tobacco"],
                    preferred_categories=["import_distribution", "business", "technology"],
                    max_risk_level="high",
                    min_confidence=0.65,
                    capital_min_usd=0,
                    capital_max_usd=15000,
                    time_horizon="medium",
                    prefers_business_over_investment=True,
                    include_china_sourcing=True,
                    # Phase 4 relevance inputs. These describe a profile, not a
                    # person baked into the code: change the row, not the model.
                    type_priority=[
                        "import_distribution", "business", "public_investment", "crypto",
                    ],
                    priority_geographies=["LY", "TN", "CN"],
                    experience_industries=["construction", "import_distribution"],
                    has_supplier_access=True,
                    has_distribution_access=False,
                    technical_ability="medium",
                    weekly_hours_available=15,
                    regulatory_access=["LY", "TN"],
                )
            )

        # --- prompt versions (immutable audit trail) ----------------------
        for prompt in ALL_PROMPTS:
            exists = (
                await session.execute(
                    sa.select(PromptVersion.id).where(
                        PromptVersion.name == prompt.name, PromptVersion.version == prompt.version
                    )
                )
            ).scalar_one_or_none()
            if exists is None:
                session.add(
                    PromptVersion(
                        name=prompt.name, version=prompt.version,
                        template=prompt.system, template_hash=prompt.hash,
                    )
                )

        # --- sources ------------------------------------------------------
        offline_created: list[Source] = []
        for spec in OFFLINE_SOURCES + scenario_sources() + NETWORK_SOURCES:
            spec = dict(spec)
            slug = spec["slug"]
            source = (
                await session.execute(sa.select(Source).where(Source.slug == slug))
            ).scalar_one_or_none()
            if source is None:
                if slug == "manual_csv":
                    csv_text = DEMO_CSV.format(
                        d0=(now - timedelta(days=90)).date().isoformat(),
                        d1=(now - timedelta(days=60)).date().isoformat(),
                        d2=(now - timedelta(days=30)).date().isoformat(),
                        d3=now.date().isoformat(),
                    )
                    spec["config"] = {"rows": parse_rows(csv_text)}
                source = Source(**spec)
                session.add(source)
                await session.flush()
                state = "enabled" if source.enabled else "disabled"
                print(f"created source {slug} ({source.adapter_key}, {state})")
            if source.enabled:
                offline_created.append(source)

        # --- watchlist + alert --------------------------------------------
        wl = (
            await session.execute(sa.select(Watchlist).where(Watchlist.user_id == user.id))
        ).scalar_one_or_none()
        if wl is None:
            session.add(
                Watchlist(
                    user_id=user.id, name="North Africa import ideas",
                    description="Products gaining demand elsewhere that are not yet common locally.",
                    min_score=45, max_risk_level="high",
                )
            )
        alert = (
            await session.execute(sa.select(Alert).where(Alert.user_id == user.id))
        ).scalar_one_or_none()
        if alert is None:
            session.add(
                Alert(
                    user_id=user.id, name="Score above 60", trigger_type="score_threshold",
                    threshold=60, channels=["in_app", "telegram"],
                )
            )

        await session.commit()

        # --- run the enabled (offline) sources -----------------------------
        for source in offline_created:
            result = await run_source(session, source, trigger="seed")
            await session.commit()
            print(
                f"ran {source.slug}: status={result.status} fetched={result.fetched} "
                f"stored={result.stored} duplicates={result.duplicates} "
                f"observations={result.observations}"
            )

        # --- Phase 3: a couple of genuinely uncertain name matches ----------
        # These are the two shapes that actually occur: a spacing variant that is
        # almost certainly the same organisation, and a containment that is almost
        # certainly not. The system refuses to decide either on its own.
        review_pairs = [
            ("OpenAI", "Open AI", "company", {}),
            ("Apple", "Apple Bank", "public_company", {"sec_cik": "320193"}),
        ]
        for original, variant, entity_type, ids in review_pairs:
            await resolve_entity(session, original, entity_type, external_ids=ids)
            result = await resolve_entity(session, variant, entity_type)
            if result.candidate is not None:
                print(
                    f"raised review candidate: '{variant}' vs '{original}' "
                    f"({result.candidate.confidence:.0%} name similarity)"
                )
        await session.commit()

        # --- Phase 3: cluster topics and evaluate trends --------------------
        topics = await rebuild_topics(session)
        await session.commit()
        # Seeded evidence is demo evidence, so the seed only ever writes the
        # demo-inclusive evaluation. A live-only set appears when a live source
        # has actually collected something; inventing one here from demo data is
        # precisely the confusion this mode exists to remove.
        trends = await evaluate_trends(session, analysis_mode=AnalysisMode.DEMO_INCLUSIVE)
        await session.commit()
        print(f"clustered {len(topics)} topic(s); evaluated {len(trends)} trend(s)")
        for trend in sorted(trends, key=lambda t: -t.trend_score)[:10]:
            print(
                f"  {trend.name[:34]:<36} score={trend.trend_score:>5.0f} "
                f"confidence={trend.confidence:>5.0f}  {trend.stage:<15} {trend.state}"
            )

        # --- Phase 4: opportunity candidates --------------------------------
        # DEMO, not LIVE_VALIDATED: every number underneath comes from generated
        # scenarios, and the UI says so on every card.
        result = await generate_opportunities(
            session,
            contexts=SCENARIO_CONTEXT,
            validation_status=ValidationStatus.DEMO,
            analysis_mode=AnalysisMode.DEMO_INCLUSIVE,
        )
        await session.commit()
        print(
            f"\nopportunities: {len(result.created)} created, {len(result.updated)} updated, "
            f"{len(result.rejections)} trend/type pair(s) refused"
        )
        for opp in sorted(result.created + result.updated, key=lambda o: -o.opportunity_score):
            print(
                f"  {opp.title[:40]:<42} score={opp.opportunity_score:>5.0f} "
                f"conf={opp.confidence:>5.0f} "
                f"{opp.risk_level:<10} {opp.state}"
            )
        if result.rejections:
            print("\n  refused (a trend is not an opportunity):")
            for rej in result.rejections[:8]:
                print(f"    {rej.trend_name[:34]:<36} [{rej.opportunity_type}] {rej.reasons[0]}")

    print("seed complete")


if __name__ == "__main__":
    if os.getenv("SEED_DEMO_DATA", "true").lower() not in {"1", "true", "yes"}:
        print("SEED_DEMO_DATA is false; nothing to do.")
    else:
        asyncio.run(seed())
