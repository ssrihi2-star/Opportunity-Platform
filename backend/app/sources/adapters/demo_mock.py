"""Deterministic offline source.

Exists so the dashboard and the whole pipeline can be exercised with no API keys
and no network. It is NOT a placeholder for a real source: it is a real adapter
whose upstream happens to be a seeded generator. Every value it produces is
reproducible from `seed` in the source config, and independent of the order in
which days are generated - which is what lets the dedup layer recognise a
re-fetch as a duplicate.
"""

from __future__ import annotations

import hashlib
import math
from datetime import UTC, datetime, timedelta

from app.sources.base import BaseDataSource, RawSignal, SourceHealth
from app.sources.registry import register

# (entity, entity_type, signal_type, signal_class, unit, is_proxy, base, growth/day)
_SERIES: list[tuple[str, str, str, str, str, bool, float, float]] = [
    ("local-first sync", "technology", "github_stars", "developer", "stars", False, 1200, 0.021),
    ("local-first sync", "technology", "job_posting_growth", "talent", "postings", False, 40, 0.012),
    ("local-first sync", "technology", "wiki_pageview_growth", "attention", "views", True, 900, 0.008),
    ("solid-state battery", "technology", "patent_activity", "industrial", "patents", False, 60, 0.010),
    ("solid-state battery", "technology", "capex_announcement", "industrial", "usd_m", False, 300, 0.017),
    ("solid-state battery", "technology", "media_coverage_growth", "attention", "articles", False, 25, 0.014),
    ("heat pump water heater", "product", "import_growth", "trade", "tonnes", False, 800, 0.011),
    ("heat pump water heater", "product", "search_growth", "attention", "index", True, 55, 0.013),
    ("heat pump water heater", "product", "supply_shortage", "industrial", "lead_days", False, 30, 0.006),
    (
        "NVIDIA Corporation",
        "public_company",
        "institutional_activity",
        "capital",
        "holders",
        False,
        3100,
        0.003,
    ),
    ("NVIDIA Corporation", "public_company", "revenue_acceleration", "commercial", "pct", False, 12, 0.004),
    ("sanitary ware", "product", "hs_code_volume_change", "trade", "tonnes", False, 2200, 0.005),
    ("sanitary ware", "product", "customer_complaint_frequency", "demand_pain", "mentions", False, 18, 0.016),
    ("AI workflow automation", "industry", "funding_round", "capital", "usd_m", False, 90, 0.019),
    (
        "AI workflow automation",
        "industry",
        "unmet_need_mentions",
        "demand_pain",
        "mentions",
        False,
        130,
        0.022,
    ),
]


@register("demo_mock")
class DemoMockSource(BaseDataSource):
    """Reproducible multi-entity, multi-signal history with no network access."""

    requires_network = False
    default_source_class = "demo"
    documented_rate_limit = "not applicable (offline)"

    @staticmethod
    def _value(seed: int, entity: str, stype: str, base: float, growth: float, elapsed: int) -> float:
        """Deterministic value for a given day index.

        Depends only on (seed, entity, signal type, day index), never on the order
        in which days are generated, so an incremental re-fetch produces
        byte-identical records.
        """
        elapsed = max(elapsed, 0)
        trend = base * math.exp(growth * elapsed)
        season = 1.0 + 0.05 * math.sin(2 * math.pi * elapsed / 7)
        digest = hashlib.sha256(f"{seed}|{entity}|{stype}|{elapsed}".encode()).digest()
        noise = 1.0 + ((digest[0] / 255.0) - 0.5) * 0.04  # +/- 2%
        return round(trend * season * noise, 3)

    async def fetch(self, since: datetime | None = None) -> list[RawSignal]:
        days = int(self.cfg("days", 60))
        seed = int(self.cfg("seed", 20260801))
        geo = self.cfg("geo_scope", "global")
        now = datetime.now(UTC).replace(hour=0, minute=0, second=0, microsecond=0)
        origin = now - timedelta(days=days - 1)

        start = origin
        if since is not None:
            if since.tzinfo is None:
                since = since.replace(tzinfo=UTC)
            start = max(start, since.replace(hour=0, minute=0, second=0, microsecond=0))

        out: list[RawSignal] = []
        for entity, etype, stype, sclass, unit, is_proxy, base, growth in _SERIES:
            day = start
            while day <= now:
                elapsed = (day - origin).days
                value = self._value(seed, entity, stype, base, growth, elapsed)
                previous = (
                    self._value(seed, entity, stype, base, growth, elapsed - 1) if elapsed > 0 else None
                )
                out.append(
                    RawSignal(
                        external_id=f"{entity}|{stype}|{day.date().isoformat()}",
                        title=f"{entity} - {stype} on {day.date().isoformat()}",
                        content=f"Observed {stype} for {entity}: {value} {unit}.",
                        url=None,
                        published_at=day,
                        metric_name=stype,
                        metric_value=value,
                        metric_unit=unit,
                        previous_value=previous,
                        entity_name=entity,
                        entity_type=etype,
                        signal_type=stype,
                        signal_class=sclass,
                        is_proxy=is_proxy,
                        geo_scope=geo,
                        confidence=0.6,
                        payload={"generator": "demo_mock", "seed": seed, "elapsed_days": elapsed},
                    )
                )
                day += timedelta(days=1)
        return out

    async def health_check(self) -> SourceHealth:
        return SourceHealth(
            healthy=True, detail="Offline deterministic generator; always available.", latency_ms=0
        )
