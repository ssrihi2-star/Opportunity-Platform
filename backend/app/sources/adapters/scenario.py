"""Synthetic scenarios, so the trend engine can be judged without live APIs.

Five situations with known right answers. They exist to make the engine's
behaviour checkable by a person: if the fake viral token ever scores like the
real trend, something has broken, and you can see it on the dashboard rather
than only in a test report.

    1. ai_coding_agents   - a genuine, accelerating trend across three streams
    2. zephyr_token       - a one-day mention spike plus syndicated coverage
    3. mycelium_packaging - slow, steady, unexciting growth
    4. dvd_authoring      - activity gradually falling away
    5. ceramic_tiles_tn   - a product that peaks the same month every year

Each `(scenario, stream)` pair becomes its own source with its own owner, which
is what lets independent corroboration be tested honestly.
"""

from __future__ import annotations

import hashlib
import math
from datetime import UTC, datetime, timedelta

from app.core.errors import SourceConfigError
from app.sources.base import BaseDataSource, RawSignal, SourceHealth
from app.sources.registry import register
from app.sources.series import build_gap, build_series

# scenario -> stream -> (entity, entity_type, signal_type, unit, is_proxy, geo)
STREAMS: dict[str, dict[str, tuple[str, str, str, str, bool, str]]] = {
    "ai_coding_agents": {
        "github": (
            "AI coding agents",
            "technology",
            "github_commit_velocity",
            "commits_per_week",
            False,
            "global",
        ),
        "news": (
            "AI coding agents",
            "technology",
            "media_coverage_growth",
            "articles_per_day",
            False,
            "global",
        ),
        "wikipedia": (
            "AI coding agents",
            "technology",
            "wiki_pageview_growth",
            "views_per_day",
            True,
            "global",
        ),
        "jobs": ("AI code review", "technology", "job_posting_growth", "postings", False, "US"),
        "packages": (
            "AI pair programming",
            "technology",
            "package_downloads",
            "downloads_per_day",
            False,
            "global",
        ),
    },
    "zephyr_token": {
        "social": (
            "ZephyrCoin",
            "crypto_asset",
            "social_discussion_growth",
            "mentions_per_day",
            True,
            "global",
        ),
        "news_wire": (
            "ZephyrCoin",
            "crypto_asset",
            "media_coverage_growth",
            "articles_per_day",
            False,
            "global",
        ),
    },
    "mycelium_packaging": {
        "github": (
            "mycelium packaging",
            "product",
            "github_commit_velocity",
            "commits_per_week",
            False,
            "global",
        ),
        "trade": ("mycelium packaging", "product", "import_growth", "tonnes", False, "TN"),
    },
    "dvd_authoring": {
        "github": (
            "DVD authoring software",
            "technology",
            "github_commit_velocity",
            "commits_per_week",
            False,
            "global",
        ),
        "wikipedia": (
            "DVD authoring software",
            "technology",
            "wiki_pageview_growth",
            "views_per_day",
            True,
            "global",
        ),
    },
    "ceramic_tiles_tn": {
        "trade": ("ceramic floor tiles", "product", "import_growth", "tonnes", False, "TN"),
        "customs_value": ("ceramic floor tiles", "product", "hs_code_volume_change", "value", False, "TN"),
    },
    # ---------------------------------------------------------------- phase 4
    # Scenario A: a real business/technology opportunity. Adoption accelerating
    # across three genuinely different kinds of evidence, attention still modest.
    "edge_inference": {
        "github": (
            "edge inference runtime",
            "software_project",
            "github_contributors",
            "contributors",
            False,
            "global",
        ),
        "packages": (
            "edge inference runtime",
            "software_project",
            "package_downloads",
            "downloads_per_day",
            False,
            "global",
        ),
        "jobs": (
            "edge inference runtime",
            "software_project",
            "job_posting_growth",
            "postings",
            False,
            "global",
        ),
        "complaints": (
            "edge inference runtime",
            "software_project",
            "unmet_need_mentions",
            "mentions",
            False,
            "global",
        ),
        "news": (
            "edge inference runtime",
            "software_project",
            "media_coverage_growth",
            "articles_per_day",
            False,
            "global",
        ),
    },
    # Scenario B: enormous noise, no substance. Distinct from the Phase 3 hype
    # case because the attention here is sustained rather than a single spike -
    # it has to be rejected on adoption grounds, not on spike detection.
    "quantum_wellness": {
        "news": (
            "quantum wellness devices",
            "product",
            "media_coverage_growth",
            "articles_per_day",
            False,
            "global",
        ),
        "social": (
            "quantum wellness devices",
            "product",
            "social_discussion_growth",
            "mentions_per_day",
            True,
            "global",
        ),
        "wikipedia": (
            "quantum wellness devices",
            "product",
            "wiki_pageview_growth",
            "views_per_day",
            True,
            "global",
        ),
        "sales": (
            "quantum wellness devices",
            "product",
            "transaction_growth",
            "orders_per_day",
            False,
            "global",
        ),
    },
    # Scenario C: real trend, poor investment. The industry grows; this company
    # carries the debt, the margins and the price of a much better one.
    "datacentre_cooling": {
        "capex": (
            "liquid cooling systems",
            "technology",
            "capex_announcement",
            "announcements",
            False,
            "global",
        ),
        "trade": ("liquid cooling systems", "technology", "import_growth", "units", False, "global"),
        "jobs": ("liquid cooling systems", "technology", "job_posting_growth", "postings", False, "global"),
        "filings": (
            "ThermaCore Industries",
            "public_company",
            "revenue_acceleration",
            "usd_millions",
            False,
            "US",
        ),
        "coverage": (
            "ThermaCore Industries",
            "public_company",
            "media_coverage_growth",
            "articles_per_day",
            False,
            "US",
        ),
        "filings2": (
            "ThermaCore Industries",
            "public_company",
            "sec_filing_activity",
            "filings",
            False,
            "US",
        ),
    },
    # Scenario D: geographic lag. Demand rising in China/Gulf/Europe, almost
    # nothing locally, and the shipping numbers are not absurd.
    "solar_water_pumps": {
        "trade_cn": ("solar water pumps", "product", "export_growth", "units", False, "CN"),
        "trade_eu": ("solar water pumps", "product", "import_growth", "units", False, "global"),
        "suppliers": ("solar water pumps", "product", "supplier_count_change", "suppliers", False, "global"),
        "local": ("solar water pumps", "product", "import_growth", "units", False, "LY"),
        "attention": (
            "solar water pumps",
            "product",
            "media_coverage_growth",
            "articles_per_day",
            False,
            "global",
        ),
    },
    # Scenario E: a real trend with no accessible way to take part. Every stream
    # is genuine; the opportunity has nowhere to stand.
    "euv_lithography": {
        "capex": (
            "EUV lithography capacity",
            "industry",
            "capex_announcement",
            "announcements",
            False,
            "global",
        ),
        "patents": ("EUV lithography capacity", "industry", "patent_activity", "filings", False, "global"),
        "trade": ("EUV lithography capacity", "industry", "export_growth", "units", False, "global"),
    },
    # Scenario F: a scam-shaped token. Loud, illiquid, anonymous, concentrated.
    "luna9_token": {
        "social": (
            "Luna9 Token",
            "crypto_asset",
            "social_discussion_growth",
            "mentions_per_day",
            True,
            "global",
        ),
        "news": ("Luna9 Token", "crypto_asset", "media_coverage_growth", "articles_per_day", False, "global"),
        "volume": ("Luna9 Token", "crypto_asset", "trading_volume_anomaly", "usd_per_day", False, "global"),
        # On-chain transaction counts, rising steadily. This is what makes the
        # case a hard one: the token clears the trend layer on real, non-proxy,
        # adoption-class numbers, and has to be rejected on what it *is* -
        # anonymous, concentrated, unaudited, illiquid - rather than on its chart.
        "onchain": (
            "Luna9 Token",
            "crypto_asset",
            "transaction_growth",
            "transfers_per_day",
            False,
            "global",
        ),
    },
}

#: Syndicated headlines for the hype scenario: one story, many outlets.
_WIRE_HEADLINES = [
    "ZephyrCoin surges as traders pile in - Reuters",
    "ZephyrCoin surges as traders pile in | CoinDesk",
    "ZephyrCoin surges as traders pile in - AP",
    "Traders pile in as ZephyrCoin surges, say analysts - Bloomberg",
    "ZephyrCoin surges as traders pile in, report says",
]


def _jitter(seed: str, index: int, amplitude: float = 0.03) -> float:
    """Deterministic pseudo-noise: same series every run, so tests stay stable."""
    digest = hashlib.sha256(f"{seed}|{index}".encode()).digest()
    return 1.0 + ((digest[0] / 255.0) - 0.5) * 2 * amplitude


#: Days (counted back from today) the customs office simply did not publish on the
#: mycelium import stream. The demo needs at least one real gap, because "missing is
#: not zero" is only believable if you can see it on a chart.
UNREPORTED_DAYS: dict[tuple[str, str], set[int]] = {
    ("mycelium_packaging", "trade"): {58, 57, 22},
}


def _daily_points(scenario: str, stream: str, days: int, now: datetime) -> list[tuple[datetime, float]]:
    start = now - timedelta(days=days - 1)
    skip = UNREPORTED_DAYS.get((scenario, stream), set())
    points: list[tuple[datetime, float]] = []
    for i in range(days):
        day = start + timedelta(days=i)
        if (days - 1 - i) in skip:
            continue  # emitted as an explicit gap, not as a value
        progress = i / max(days - 1, 1)
        seed = f"{scenario}|{stream}"

        if scenario == "ai_coding_agents":
            base = {"github": 40, "news": 6, "wikipedia": 900, "jobs": 25, "packages": 4000}[stream]
            # Superlinear: the growth rate itself rises, which is what "accelerating" means.
            value = base * (1 + 9 * progress**2.2)
        elif scenario == "zephyr_token" and stream == "social":
            value = 10 + 2 * math.sin(i / 3.0)
            if i == days - 4:
                value = 300.0  # the one day everybody talked about it
        elif scenario == "zephyr_token":
            value = 1 + (3 if i >= days - 5 else 0)
        elif scenario == "mycelium_packaging":
            base = {"github": 30, "trade": 120}[stream]
            value = base * (1 + 0.9 * progress)  # straight line: steady, unspectacular
        elif scenario == "dvd_authoring":
            base = {"github": 90, "wikipedia": 5200}[stream]
            value = base * (1 - 0.55 * progress)

        # ---------------------------------------------------------- phase 4
        elif scenario == "edge_inference":
            # Real adoption rising fast; press coverage rising slowly. This is
            # the shape the adoption-to-attention ratio is meant to reward.
            base = {"github": 22, "packages": 900, "jobs": 14, "complaints": 8, "news": 3}[stream]
            rate = 0.6 if stream == "news" else 4.5
            value = base * (1 + rate * progress**1.6)
        elif scenario == "quantum_wellness":
            # The mirror image: enormous, sustained attention over a flat
            # order book. Not a spike - a story that will not die.
            if stream == "sales":
                value = 40 + 3 * progress  # essentially flat
            elif stream == "news":
                value = 4 * (1 + 24 * progress**1.4)
            else:
                value = 60 * (1 + 18 * progress**1.3)
        elif scenario == "datacentre_cooling":
            if stream in {"capex", "trade", "jobs"}:
                base = {"capex": 5, "trade": 1200, "jobs": 30}[stream]
                value = base * (1 + 3.2 * progress**1.5)  # the industry really is growing
            elif stream == "filings":
                value = 210 * (1 + 0.10 * progress)  # the company barely is
            elif stream == "filings2":
                value = 3 + 2 * progress
            else:
                value = 5 * (1 + 6 * progress)  # but the coverage is loud
        elif scenario == "solar_water_pumps":
            if stream == "trade_cn":
                value = 900 * (1 + 3.4 * progress**1.3)
            elif stream == "trade_eu":
                value = 400 * (1 + 2.1 * progress**1.2)
            elif stream == "suppliers":
                value = 40 * (1 + 1.6 * progress)
            elif stream == "local":
                value = 6 + 2 * progress  # almost nothing, locally
            else:
                value = 3 * (1 + 1.2 * progress)
        elif scenario == "euv_lithography":
            base = {"capex": 4, "patents": 30, "trade": 55}[stream]
            value = base * (1 + 2.4 * progress**1.4)
        elif scenario == "luna9_token":
            # Deliberately strong and sustained, not a one-day spike: this case
            # has to be rejected on what it *is*, not on the shape of its chart.
            if stream == "volume":
                value = 30_000 * (1 + 60 * progress**1.8)
            elif stream == "onchain":
                value = 800 * (1 + 14 * progress**1.5)
            else:
                base = {"social": 120, "news": 8}[stream]
                value = base * (1 + 30 * progress**1.6)

        else:  # pragma: no cover - seasonal scenario uses monthly points
            value = 100.0
        points.append((day, round(max(value, 0.0) * _jitter(seed, i), 3)))
    return points


def _monthly_seasonal_points(stream: str, months: int, now: datetime) -> list[tuple[datetime, float]]:
    """Three years of a product that peaks the same month every year.

    The peak is aligned to the current month on purpose, so whenever the demo is
    run the newest reading sits at the top of its seasonal curve - which is
    exactly the situation a naive engine would call a brand-new trend.
    """
    peak_month = now.month
    base = 900.0 if stream == "trade" else 2_400_000.0
    points: list[tuple[datetime, float]] = []
    for k in range(months):
        offset = months - 1 - k
        month_date = (now.replace(day=15) - timedelta(days=30 * offset)).replace(day=15)
        seasonal = 1 + 0.55 * math.cos(2 * math.pi * (month_date.month - peak_month) / 12)
        drift = 1 + 0.01 * (k / max(months - 1, 1))  # a flat year-on-year total
        points.append((month_date, round(base * seasonal * drift * _jitter(stream, k, 0.02), 2)))
    return points


@register("scenario")
class ScenarioSource(BaseDataSource):
    """Deterministic demo scenarios with known correct classifications."""

    requires_network = False
    default_source_class = "demo"
    documented_rate_limit = "not applicable (offline)"

    async def fetch(self, since: datetime | None = None) -> list[RawSignal]:
        scenario = self.cfg("scenario", required=True)
        stream = self.cfg("stream", required=True)
        if scenario not in STREAMS:
            raise SourceConfigError(f"Unknown scenario {scenario!r}. Available: {sorted(STREAMS)}.")
        if stream not in STREAMS[scenario]:
            raise SourceConfigError(
                f"Scenario {scenario!r} has no stream {stream!r}. Available: {sorted(STREAMS[scenario])}."
            )

        entity, entity_type, signal_type, unit, is_proxy, geo = STREAMS[scenario][stream]
        now = datetime.now(UTC).replace(hour=0, minute=0, second=0, microsecond=0)

        if scenario == "ceramic_tiles_tn":
            points = _monthly_seasonal_points(stream, int(self.cfg("months", 37)), now)
            currency = "TND" if stream == "customs_value" else None
        else:
            points = _daily_points(scenario, stream, int(self.cfg("days", 180)), now)
            currency = None

        records = build_series(
            points=points,
            entity_name=entity,
            entity_type=entity_type,
            signal_type=signal_type,
            source_prefix=f"scenario:{scenario}:{stream}",
            unit=unit,
            geo_scope=geo,
            confidence=0.6,
            currency=currency,
            is_proxy=is_proxy,
            payload_extra={"scenario": scenario, "stream": stream, "synthetic": True},
        )

        for offset in sorted(UNREPORTED_DAYS.get((scenario, stream), set()), reverse=True):
            records.append(
                build_gap(
                    observed_at=now - timedelta(days=offset),
                    entity_name=entity,
                    entity_type=entity_type,
                    signal_type=signal_type,
                    source_prefix=f"scenario:{scenario}:{stream}",
                    reason="The customs office published no figure for this period.",
                    geo_scope=geo,
                    unit=unit,
                )
            )

        # The hype scenario also emits the same story from several outlets, so the
        # syndication detector has something real to catch.
        if scenario == "zephyr_token" and stream == "news_wire":
            for index, headline in enumerate(_WIRE_HEADLINES):
                published = now - timedelta(days=4, hours=index)
                records.append(
                    RawSignal(
                        external_id=f"scenario:zephyr:wire:{index}",
                        title=headline,
                        content="Syndicated coverage of the same ZephyrCoin price move.",
                        url=f"https://example.invalid/zephyr/{index}",
                        published_at=published,
                        entity_name=entity,
                        entity_type=entity_type,
                        geo_scope=geo,
                        confidence=0.3,
                        payload={"scenario": scenario, "syndicated": True, "synthetic": True},
                    )
                )
        return records

    async def health_check(self) -> SourceHealth:
        return SourceHealth(
            healthy=True,
            detail=f"Synthetic scenario '{self.config.get('scenario')}' / "
            f"stream '{self.config.get('stream')}'; no network involved.",
        )
