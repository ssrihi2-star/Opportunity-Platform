"""Computing and storing the USER RELEVANCE SCORE.

The global data is shared. One opportunity is stored once, scored once globally,
and read by everyone. What is stored per user is only the *judgement* — one small
row per user per opportunity saying how relevant it is to them and why.

Nothing in this module writes to a global column. If it ever does, the Global
Opportunity Score stops being global, and the product's central claim — *this is
a strong opportunity and a poor fit for you* — becomes unsayable.
"""

from __future__ import annotations

import uuid
from datetime import UTC, datetime

import sqlalchemy as sa
from sqlalchemy.ext.asyncio import AsyncSession

from app.analytics.relevance import (
    RELEVANCE_VERSION,
    OpportunityContext,
    RelevanceResult,
    UserContext,
    score_relevance,
)
from app.core.logging import get_logger
from app.models.models import (
    CountryProfile,
    Opportunity,
    OpportunityParticipation,
    UserOpportunityRelevance,
)

logger = get_logger(__name__)


async def _coverage_by_country(session: AsyncSession, codes: set[str]) -> dict[str, float]:
    if not codes:
        return {}
    rows = (
        await session.execute(
            sa.select(CountryProfile.iso_code, CountryProfile.data_coverage).where(
                CountryProfile.iso_code.in_(sorted(codes))
            )
        )
    ).all()
    return {code: coverage for code, coverage in rows}


async def _paths_by_opportunity(session: AsyncSession, ids: list[uuid.UUID]) -> dict[uuid.UUID, list[str]]:
    if not ids:
        return {}
    rows = (
        await session.execute(
            sa.select(OpportunityParticipation.opportunity_id, OpportunityParticipation.kind).where(
                OpportunityParticipation.opportunity_id.in_(ids)
            )
        )
    ).all()
    out: dict[uuid.UUID, list[str]] = {}
    for opportunity_id, kind in rows:
        out.setdefault(opportunity_id, []).append(kind)
    return out


def to_context(opp: Opportunity, *, paths: list[str], country_coverage: float | None) -> OpportunityContext:
    """A read-only copy of the global facts relevance is allowed to look at.

    Deliberately a copy: the relevance engine receives a value object it cannot
    write back through, so no per-user calculation can reach a global column
    even by accident.
    """
    return OpportunityContext(
        opportunity_id=str(opp.id),
        opportunity_type=opp.opportunity_type,
        category=opp.category,
        industry=opp.industry,
        geo_scope=opp.geo_scope,
        country=opp.country,
        evidence_countries=dict(opp.evidence_countries or {}),
        global_score=opp.opportunity_score,
        confidence=opp.confidence,
        risk_level=opp.risk_level,
        capital_required_usd=opp.capital_required_usd,
        participation_paths=paths or ["watch"],
        country_data_coverage=country_coverage,
    )


async def compute_for_user(
    session: AsyncSession,
    *,
    user_id: uuid.UUID,
    user: UserContext,
    opportunities: list[Opportunity],
) -> dict[uuid.UUID, RelevanceResult]:
    """Score every given opportunity for one user and store the rows.

    Returns the results keyed by opportunity id so a caller that already has the
    opportunities in hand does not have to read back what it just wrote.
    """
    if not opportunities:
        return {}

    ids = [opp.id for opp in opportunities]
    paths_by_id = await _paths_by_opportunity(session, ids)
    coverage = await _coverage_by_country(
        session, {opp.country.upper() for opp in opportunities if opp.country}
    )

    existing = {
        row.opportunity_id: row
        for row in (
            await session.execute(
                sa.select(UserOpportunityRelevance).where(
                    UserOpportunityRelevance.user_id == user_id,
                    UserOpportunityRelevance.opportunity_id.in_(ids),
                )
            )
        ).scalars()
    }

    now = datetime.now(UTC)
    results: dict[uuid.UUID, RelevanceResult] = {}
    for opp in opportunities:
        context = to_context(
            opp,
            paths=paths_by_id.get(opp.id, []),
            country_coverage=coverage.get((opp.country or "").upper()),
        )
        result = score_relevance(user, context)
        results[opp.id] = result

        row = existing.get(opp.id)
        if row is None:
            row = UserOpportunityRelevance(user_id=user_id, opportunity_id=opp.id)
            session.add(row)
        row.relevance = result.relevance
        row.parts = {"factors": result.parts, "notes": result.notes}
        row.path_relevance = result.path_relevance
        row.best_path = result.best_path
        row.outside_profile = result.outside_profile
        row.formula_version = RELEVANCE_VERSION
        row.computed_at = now

    await session.flush()
    logger.info(
        "relevance.computed",
        user_id=str(user_id),
        count=len(results),
        version=RELEVANCE_VERSION,
    )
    return results


async def refresh_user(
    session: AsyncSession, *, user_id: uuid.UUID, user: UserContext, limit: int = 500
) -> int:
    """Recompute a user's whole relevance set, e.g. after they edit their profile."""
    opportunities = list(
        (
            await session.execute(
                sa.select(Opportunity).order_by(Opportunity.opportunity_score.desc()).limit(limit)
            )
        ).scalars()
    )
    results = await compute_for_user(session, user_id=user_id, user=user, opportunities=opportunities)
    return len(results)


async def stored_for_user(
    session: AsyncSession, *, user_id: uuid.UUID, opportunity_ids: list[uuid.UUID]
) -> dict[uuid.UUID, UserOpportunityRelevance]:
    """Read back stored relevance. Scoped to one user, always."""
    if not opportunity_ids:
        return {}
    rows = (
        await session.execute(
            sa.select(UserOpportunityRelevance).where(
                UserOpportunityRelevance.user_id == user_id,
                UserOpportunityRelevance.opportunity_id.in_(opportunity_ids),
            )
        )
    ).scalars()
    return {row.opportunity_id: row for row in rows}
