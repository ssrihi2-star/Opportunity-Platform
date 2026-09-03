"""Storing what we know about countries, and being honest about what we don't.

Every fact here arrives with a source, a date, a confidence and its original
unit. There is no code path that writes a country fact without a source name,
which is the mechanism — not the intention — that stops a language model from
inventing a country's import rules.

The coverage score computed here is a measure of **our** blindness. A country
can be enormous and sophisticated and score 12% because nobody has connected a
source that covers it. Section 29 of the brief says exactly this, and the API
repeats it on every response, because a number labelled "coverage" next to a
country name is otherwise read as a judgement of the country.
"""

from __future__ import annotations

import uuid
from datetime import UTC, datetime
from typing import Any

import sqlalchemy as sa
from sqlalchemy.ext.asyncio import AsyncSession

from app.analytics.country import (
    COVERAGE_VERSION,
    AdoptionReading,
    FactInput,
    compute_coverage,
)
from app.analytics.geographic import MarketFacts
from app.core.logging import get_logger
from app.models.enums import EvidenceStatus
from app.models.models import CountryAdoption, CountryFact, CountryProfile, Signal

logger = get_logger(__name__)

COVERAGE_DISCLAIMER = (
    "This is a measure of how much evidence this system holds about the country, "
    "not a judgement of the country. A low score means we have not looked, which "
    "is different from having looked and found nothing."
)

#: How a stored fact maps onto the 0-1 normalised inputs the geographic engine
#: reads, with the natural range of each. A value outside its range is clamped
#: rather than rejected: an odd figure is still evidence.
NORMALISERS: dict[str, tuple[str, float, float, bool]] = {
    # key: (MarketFacts attribute, low, high, higher_is_better)
    "internet_penetration": ("internet_penetration", 0.0, 100.0, True),
    "payment_infrastructure": ("payment_infrastructure", 0.0, 100.0, True),
    "logistics_performance": ("logistics_performance", 1.0, 5.0, True),
    "electricity_reliability": ("electricity_reliability", 0.0, 100.0, True),
    "business_formation_days": ("business_formation_ease", 1.0, 120.0, False),
    "import_restrictions": ("import_openness", 0.0, 100.0, False),
    "regulatory_environment": ("regulatory_environment", 0.0, 100.0, True),
    "gdp_per_capita_ppp": ("purchasing_power", 500.0, 80_000.0, True),
}


def normalise(key: str, value: float) -> tuple[str, float] | None:
    """Put one stored figure on a 0-1 scale, keeping the original untouched."""
    spec = NORMALISERS.get(key)
    if spec is None:
        return None
    attribute, low, high, higher_better = spec
    if high == low:
        return None
    share = (value - low) / (high - low)
    share = max(0.0, min(1.0, share))
    return attribute, round(share if higher_better else 1.0 - share, 4)


async def get_or_create(session: AsyncSession, *, iso_code: str, name: str | None = None) -> CountryProfile:
    """One row per country, created on demand. No country is special-cased."""
    code = iso_code.upper()
    existing = (
        await session.execute(sa.select(CountryProfile).where(CountryProfile.iso_code == code))
    ).scalar_one_or_none()
    if existing is not None:
        return existing
    profile = CountryProfile(iso_code=code, name=name or code)
    session.add(profile)
    await session.flush()
    return profile


async def record_fact(
    session: AsyncSession,
    *,
    iso_code: str,
    key: str,
    source_name: str,
    as_of: datetime,
    value_numeric: float | None = None,
    value_text: str | None = None,
    unit: str | None = None,
    currency: str | None = None,
    source_url: str | None = None,
    confidence: float = 0.6,
    status: str = EvidenceStatus.MEASURED,
) -> CountryFact:
    """Store one evidenced country fact.

    `source_name` is required by the signature, not by a validation branch, so
    there is no way to call this without saying where the number came from.
    """
    if not source_name.strip():
        raise ValueError(
            "A country fact needs a source. A fact with no source is an assertion, "
            "and assertions are what this system exists to avoid."
        )
    profile = await get_or_create(session, iso_code=iso_code)
    existing = (
        await session.execute(
            sa.select(CountryFact).where(
                CountryFact.country_id == profile.id,
                CountryFact.key == key,
                CountryFact.as_of == as_of,
            )
        )
    ).scalar_one_or_none()
    fact = existing or CountryFact(country_id=profile.id, key=key, as_of=as_of)
    fact.value_numeric = value_numeric
    fact.value_text = value_text
    fact.unit = unit
    fact.currency = currency.upper() if currency else None
    fact.source_name = source_name
    fact.source_url = source_url
    fact.confidence = confidence
    fact.status = status
    if existing is None:
        session.add(fact)
    await session.flush()
    return fact


async def facts_for(session: AsyncSession, iso_code: str) -> list[CountryFact]:
    """The latest value of each fact key for one country."""
    profile = (
        await session.execute(sa.select(CountryProfile).where(CountryProfile.iso_code == iso_code.upper()))
    ).scalar_one_or_none()
    if profile is None:
        return []
    rows = list(
        (
            await session.execute(
                sa.select(CountryFact)
                .where(CountryFact.country_id == profile.id)
                .order_by(CountryFact.key, CountryFact.as_of.desc())
            )
        ).scalars()
    )
    latest: dict[str, CountryFact] = {}
    for row in rows:
        latest.setdefault(row.key, row)
    return list(latest.values())


async def market_facts(session: AsyncSession, iso_code: str) -> MarketFacts:
    """The normalised view the geographic engine reads. Unknown stays None."""
    facts = MarketFacts()
    profile = (
        await session.execute(sa.select(CountryProfile).where(CountryProfile.iso_code == iso_code.upper()))
    ).scalar_one_or_none()
    if profile is None:
        return facts
    facts.data_coverage = profile.data_coverage
    facts.currency = profile.currency
    facts.language = (profile.languages or [None])[0]
    for fact in await facts_for(session, iso_code):
        if fact.status != EvidenceStatus.MEASURED or fact.value_numeric is None:
            continue
        mapped = normalise(fact.key, fact.value_numeric)
        if mapped is not None:
            setattr(facts, mapped[0], mapped[1])
    return facts


async def refresh_coverage(
    session: AsyncSession, *, iso_code: str, now: datetime | None = None
) -> CountryProfile:
    """Recompute how much we hold about one country."""
    now = now or datetime.now(UTC)
    profile = await get_or_create(session, iso_code=iso_code)
    facts = await facts_for(session, iso_code)

    signal_count = (
        await session.execute(
            sa.select(sa.func.count()).select_from(Signal).where(Signal.geo_scope == profile.iso_code)
        )
    ).scalar_one()
    source_count = (
        await session.execute(
            sa.select(sa.func.count(sa.distinct(Signal.source_id))).where(
                Signal.geo_scope == profile.iso_code
            )
        )
    ).scalar_one()

    result = compute_coverage(
        facts=[
            FactInput(
                key=f.key,
                as_of=f.as_of if f.as_of.tzinfo else f.as_of.replace(tzinfo=UTC),
                confidence=f.confidence,
                status=f.status,
            )
            for f in facts
        ],
        signal_count=int(signal_count or 0),
        source_count=int(source_count or 0),
        now=now,
    )
    profile.data_coverage = result.coverage
    profile.coverage_parts = {
        "version": result.version,
        "parts": result.parts,
        "missing_facts": result.missing_facts,
        "is_low": result.is_low,
        "caveat": result.caveat,
        "disclaimer": COVERAGE_DISCLAIMER,
    }
    profile.last_coverage_at = now
    await session.flush()
    return profile


async def refresh_all_coverage(session: AsyncSession, *, now: datetime | None = None) -> dict[str, float]:
    codes = list((await session.execute(sa.select(CountryProfile.iso_code))).scalars())
    out: dict[str, float] = {}
    for code in codes:
        profile = await refresh_coverage(session, iso_code=code, now=now)
        out[code] = profile.data_coverage
    logger.info("country.coverage_refreshed", countries=len(out), version=COVERAGE_VERSION)
    return out


async def adoption_readings(session: AsyncSession, *, subject_key: str) -> list[AdoptionReading]:
    """What we know about one subject across every country we hold a row for.

    A country with no row at all is absent rather than zero: the caller decides
    which markets it wanted to compare, and `analyse_gap` reports the ones it
    could not measure by name.
    """
    rows = (
        await session.execute(
            sa.select(CountryAdoption, CountryProfile)
            .outerjoin(CountryProfile, CountryProfile.iso_code == CountryAdoption.country_code)
            .where(CountryAdoption.subject_key == subject_key)
        )
    ).all()
    readings: list[AdoptionReading] = []
    for adoption, profile in rows:
        readings.append(
            AdoptionReading(
                country=adoption.country_code,
                status=adoption.status,
                level=adoption.level,
                adoption_growth=adoption.adoption_growth,
                attention_growth=adoption.attention_growth,
                supplier_count=adoption.supplier_count,
                competitor_count=adoption.competitor_count,
                unit_price=adoption.unit_price,
                price_currency=adoption.price_currency,
                first_observed_days_ago=adoption.first_observed_days_ago,
                observation_count=adoption.observation_count,
                source_count=adoption.source_count,
                data_coverage=profile.data_coverage if profile else None,
            )
        )
    return readings


async def coverage_table(session: AsyncSession) -> list[dict[str, Any]]:
    """Every country and how much we hold about it, for the admin view."""
    rows = list(
        (
            await session.execute(sa.select(CountryProfile).order_by(CountryProfile.data_coverage.desc()))
        ).scalars()
    )
    return [
        {
            "iso_code": r.iso_code,
            "name": r.name,
            "region": r.region,
            "currency": r.currency,
            "data_coverage": r.data_coverage,
            "missing_facts": (r.coverage_parts or {}).get("missing_facts", []),
            "is_low": (r.coverage_parts or {}).get("is_low", True),
            "caveat": (r.coverage_parts or {}).get("caveat"),
            "last_coverage_at": r.last_coverage_at,
        }
        for r in rows
    ]


async def country_id_for(session: AsyncSession, iso_code: str) -> uuid.UUID | None:
    return (
        await session.execute(sa.select(CountryProfile.id).where(CountryProfile.iso_code == iso_code.upper()))
    ).scalar_one_or_none()
