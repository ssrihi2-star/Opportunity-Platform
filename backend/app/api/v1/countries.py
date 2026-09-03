"""Country intelligence, coverage, and cross-country gaps.

The recurring theme of this module is refusing to let thin data read as a
finding. Every response that touches a country carries its coverage and, when
that coverage is low, a sentence saying what low coverage does and does not mean.

Section 30 of the brief, which the tests enforce:

    Do not conclude "No opportunities exist in Country X" when the real
    situation is "We have poor data coverage for Country X."
"""

from __future__ import annotations

from datetime import datetime
from typing import Any

import sqlalchemy as sa
from fastapi import APIRouter, Depends, HTTPException, Query, status
from pydantic import BaseModel, Field
from sqlalchemy.ext.asyncio import AsyncSession

from app.analytics.country import LOW_COVERAGE, summarise
from app.analytics.geographic import (
    GEO_ENGINE_VERSION,
    TRANSFERABILITY_VERSION,
    analyse_gap,
    find_localization_candidates,
)
from app.api.deps import current_user, db_session, require_admin
from app.models.models import CountryFact, CountryProfile, User
from app.services.countries import (
    COVERAGE_DISCLAIMER,
    adoption_readings,
    coverage_table,
    market_facts,
    refresh_all_coverage,
)

router = APIRouter(tags=["countries"])


class CountryFactOut(BaseModel):
    key: str
    value_numeric: float | None
    value_text: str | None
    #: The value exactly as published, in its own unit and its own currency.
    unit: str | None
    currency: str | None
    source_name: str
    source_url: str | None
    as_of: datetime
    confidence: float
    status: str


class CountryOut(BaseModel):
    iso_code: str
    name: str
    region: str | None
    currency: str | None
    languages: list[str]
    #: 0-100. How much WE hold, not how good the country is.
    data_coverage: float
    coverage_is_low: bool
    coverage_disclaimer: str = COVERAGE_DISCLAIMER
    coverage_caveat: str | None = None
    missing_facts: list[str] = Field(default_factory=list)
    facts: list[CountryFactOut] = Field(default_factory=list)


class CoverageRow(BaseModel):
    iso_code: str
    name: str
    region: str | None
    currency: str | None
    data_coverage: float
    is_low: bool
    caveat: str | None
    missing_facts: list[str]
    last_coverage_at: datetime | None


class CoverageOut(BaseModel):
    countries: list[CoverageRow]
    disclaimer: str = COVERAGE_DISCLAIMER
    low_threshold: float = LOW_COVERAGE


class GapOut(BaseModel):
    version: str
    subject: str
    target: str
    #: None — never zero — when nothing comparable could be measured.
    gap: float | None
    dimensions: dict[str, Any]
    leaders: list[str]
    coverage_note: str
    confidence_penalty: float
    caveats: list[str]
    #: Countries we have never measured for this subject, named explicitly.
    unmeasured: list[str]
    #: Repeated on every response because it is the rule most easily lost.
    no_data_note: str = (
        "A market listed as unmeasured is a gap in our data, not evidence of low "
        "adoption. UNKNOWN is never treated as ZERO."
    )


class LocalizationOut(BaseModel):
    subject: str
    source_markets: list[str]
    target_market: str
    gap: float
    similarity: float
    transferability: float | None
    transferability_version: str = TRANSFERABILITY_VERSION
    transferability_note: str = (
        "Experimental. Shown separately and contributing nothing to the Global "
        "Opportunity Score until it has been backtested."
    )
    reason: str
    caveats: list[str]


@router.get("/countries", response_model=list[CoverageRow])
async def list_countries(
    session: AsyncSession = Depends(db_session),
    _: User = Depends(current_user),
) -> list[CoverageRow]:
    return [CoverageRow(**row) for row in await coverage_table(session)]


@router.get("/countries/coverage", response_model=CoverageOut)
async def coverage(
    session: AsyncSession = Depends(db_session),
    _: User = Depends(current_user),
) -> CoverageOut:
    """Where this system is blind, stated plainly.

    Reading this table as a ranking of countries is the mistake it exists to
    prevent, so the disclaimer travels with the data rather than sitting in the
    documentation.
    """
    return CoverageOut(countries=[CoverageRow(**row) for row in await coverage_table(session)])


@router.post("/countries/coverage/refresh", response_model=CoverageOut)
async def refresh_coverage_endpoint(
    session: AsyncSession = Depends(db_session),
    _: User = Depends(require_admin),
) -> CoverageOut:
    await refresh_all_coverage(session)
    await session.commit()
    return CoverageOut(countries=[CoverageRow(**row) for row in await coverage_table(session)])


@router.get("/countries/{iso_code}", response_model=CountryOut)
async def country_detail(
    iso_code: str,
    session: AsyncSession = Depends(db_session),
    _: User = Depends(current_user),
) -> CountryOut:
    profile = (
        await session.execute(sa.select(CountryProfile).where(CountryProfile.iso_code == iso_code.upper()))
    ).scalar_one_or_none()
    if profile is None:
        raise HTTPException(
            status.HTTP_404_NOT_FOUND,
            f"No country profile is stored for {iso_code.upper()} yet. That means we "
            "have not collected anything about it, not that there is nothing there.",
        )
    facts = list(
        (
            await session.execute(
                sa.select(CountryFact)
                .where(CountryFact.country_id == profile.id)
                .order_by(CountryFact.key, CountryFact.as_of.desc())
            )
        ).scalars()
    )
    parts = profile.coverage_parts or {}
    return CountryOut(
        iso_code=profile.iso_code,
        name=profile.name,
        region=profile.region,
        currency=profile.currency,
        languages=list(profile.languages or []),
        data_coverage=profile.data_coverage,
        coverage_is_low=profile.data_coverage < LOW_COVERAGE,
        coverage_caveat=parts.get("caveat"),
        missing_facts=parts.get("missing_facts", []),
        facts=[
            CountryFactOut(
                key=f.key,
                value_numeric=f.value_numeric,
                value_text=f.value_text,
                unit=f.unit,
                currency=f.currency,
                source_name=f.source_name,
                source_url=f.source_url,
                as_of=f.as_of,
                confidence=f.confidence,
                status=f.status,
            )
            for f in facts
        ],
    )


@router.get("/geographic/gap", response_model=GapOut)
async def geographic_gap(
    subject: str = Query(description="The subject key, e.g. a trend or product slug."),
    target: str = Query(min_length=2, max_length=2, description="The market to assess."),
    session: AsyncSession = Depends(db_session),
    _: User = Depends(current_user),
) -> GapOut:
    """How far behind is one market, on the evidence we actually hold?

    Returns a null gap rather than a zero when the target has never been
    measured. "We cannot tell" and "there is no gap" are different answers.
    """
    readings = await adoption_readings(session, subject_key=subject)
    if not readings:
        raise HTTPException(
            status.HTTP_404_NOT_FOUND,
            f"No adoption readings are stored for '{subject}' in any market. Nothing "
            "can be concluded about any country from an empty measurement set.",
        )
    facts = await market_facts(session, target)
    gap = analyse_gap(
        subject=subject,
        readings=readings,
        target_country=target.upper(),
        target_facts=facts,
    )
    return GapOut(
        version=gap.version,
        subject=subject,
        target=gap.target,
        gap=gap.gap,
        dimensions={
            name: {"points": d.points, "max": d.max, "why": d.why, "measured": d.measured}
            for name, d in gap.dimensions.items()
        },
        leaders=gap.leaders,
        coverage_note=gap.coverage_note,
        confidence_penalty=gap.confidence_penalty,
        caveats=gap.caveats,
        unmeasured=gap.unmeasured,
    )


@router.get("/geographic/localization", response_model=list[LocalizationOut])
async def localization_candidates(
    subject: str = Query(description="The subject key to look for a lagging market in."),
    min_similarity: float = Query(default=0.6, ge=0, le=1),
    min_gap: float = Query(default=25.0, ge=0, le=100),
    session: AsyncSession = Depends(db_session),
    _: User = Depends(current_user),
) -> list[LocalizationOut]:
    """Working in A, similar conditions in B, low penetration in B.

    A market we have never measured is never proposed here. Recommending a
    country because nobody has looked at it would be the single most damaging
    thing this feature could do.
    """
    readings = await adoption_readings(session, subject_key=subject)
    if not readings:
        return []
    facts = {r.country: await market_facts(session, r.country) for r in readings}
    candidates = find_localization_candidates(
        subject=subject,
        readings=readings,
        facts_by_country=facts,
        min_similarity=min_similarity,
        min_gap=min_gap,
    )
    return [
        LocalizationOut(
            subject=c.subject,
            source_markets=c.source_markets,
            target_market=c.target_market,
            gap=c.gap,
            similarity=c.similarity,
            transferability=c.transferability,
            reason=c.reason,
            caveats=c.caveats,
        )
        for c in candidates
    ]


class SubjectCoverageOut(BaseModel):
    subject: str
    measured: list[str]
    no_signal: list[str]
    no_data: list[str]
    low_coverage: list[str]
    sentence: str
    engine_version: str = GEO_ENGINE_VERSION


@router.get("/geographic/coverage", response_model=SubjectCoverageOut)
async def subject_coverage(
    subject: str,
    session: AsyncSession = Depends(db_session),
    _: User = Depends(current_user),
) -> SubjectCoverageOut:
    """Which markets we measured for one subject, and which we merely did not."""
    readings = await adoption_readings(session, subject_key=subject)
    summary = summarise(readings)
    return SubjectCoverageOut(
        subject=subject,
        measured=sorted(summary.measured),
        no_signal=sorted(summary.no_signal),
        no_data=sorted(summary.no_data),
        low_coverage=sorted(summary.low_coverage),
        sentence=summary.sentence(),
    )
