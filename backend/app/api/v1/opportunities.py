from __future__ import annotations

import uuid
from datetime import UTC, datetime

import sqlalchemy as sa
from fastapi import APIRouter, Depends, HTTPException, Query, status
from sqlalchemy.ext.asyncio import AsyncSession

from app.ai.opportunity_report import build_facts, build_report, narrate
from app.ai.provider import get_provider
from app.analytics.opportunity_config import OPPORTUNITY_VERSION
from app.analytics.relevance import RelevanceResult
from app.analytics.signal_types import class_of
from app.api.deps import current_user, db_session, require_admin, require_analyst
from app.models.enums import ValidationStatus
from app.models.models import (
    ModelRun,
    Opportunity,
    OpportunityCondition,
    OpportunityDecision,
    OpportunityParticipation,
    OpportunityRisk,
    OpportunityScore,
    Signal,
    SkepticReview,
    Source,
    SystemAuditLog,
    Trend,
    TrendSignal,
    User,
)
from app.schemas.common import Page
from app.schemas.opportunities import (
    ConditionOut,
    DecisionIn,
    DecisionOut,
    EvidenceRow,
    GenerateResult,
    OpportunityDetailOut,
    OpportunityOut,
    ParticipationOut,
    RejectionOut,
    ReportOut,
    ReportSectionOut,
    RiskOut,
    ScoreSnapshotOut,
    SkepticOut,
)
from app.services.opportunities import generate_opportunities
from app.services.profiles import build_context, ensure_profile
from app.services.user_relevance import compute_for_user
from app.sources.adapters.scenario_context import SCENARIO_CONTEXT

router = APIRouter(tags=["opportunities"])

SORT_PATTERN = "^(score|confidence|newest|updated|relevance|lowest_risk|fastest_trend)$"

SORTABLE = {
    "score": (Opportunity.opportunity_score.desc(), Opportunity.confidence.desc()),
    "confidence": (Opportunity.confidence.desc(), Opportunity.opportunity_score.desc()),
    "newest": (Opportunity.detected_at.desc(),),
    "updated": (Opportunity.last_evaluated_at.desc(),),
    #: Handled separately: relevance lives in a per-user table, never on the
    #: global row, so sorting by it means joining the caller's own rows.
    "relevance": (Opportunity.opportunity_score.desc(),),
    #: "Lowest risk" is a sort order, not a recommendation: it lets a reader who
    #: cannot take risk skip the rest, which is different from suggesting these.
    "lowest_risk": (
        sa.case(
            (Opportunity.risk_level == "low", 0),
            (Opportunity.risk_level == "moderate", 1),
            (Opportunity.risk_level == "high", 2),
            else_=3,
        ),
        Opportunity.opportunity_score.desc(),
    ),
}


#: How many candidates a single request will rescore for the caller. Relevance
#: is arithmetic over a stored profile, so this is cheap; the cap exists so one
#: request cannot walk an unbounded table.
RELEVANCE_CAP = 500


def _with_trend(row: Opportunity, trend: Trend | None) -> OpportunityOut:
    out = OpportunityOut.model_validate(row)
    if trend is not None:
        out.trend_id = trend.id
        out.trend_score = trend.trend_score
        out.trend_confidence = trend.confidence
        out.trend_stage = trend.stage
        out.trend_name = trend.name
    return out


async def _score_for_caller(
    session: AsyncSession, user: User, opportunities: list[Opportunity]
) -> dict[uuid.UUID, RelevanceResult]:
    """Relevance for the person holding this token, and nobody else.

    Scoped to `user.id` at every step. There is no code path here that can read
    or write another user's relevance, which is what section 25 requires.
    """
    profile = await ensure_profile(session, user)
    context = await build_context(session, profile)
    results = await compute_for_user(session, user_id=user.id, user=context, opportunities=opportunities)
    await session.commit()
    return results


def _apply_relevance(out: OpportunityOut, result: RelevanceResult | None) -> OpportunityOut:
    """Attach the user's number beside the global one — never merged into it."""
    if result is None:
        return out
    out.user_relevance = result.relevance
    out.best_path = result.best_path
    out.outside_profile = result.outside_profile
    out.relevance_version = result.version
    return out


@router.get("/opportunities", response_model=Page[OpportunityOut])
async def list_opportunities(
    opportunity_type: str | None = None,
    country: str | None = None,
    industry: str | None = None,
    state: str | None = None,
    risk_level: str | None = None,
    validation_status: str | None = None,
    min_score: float | None = Query(default=None, ge=0, le=100),
    min_confidence: float | None = Query(default=None, ge=0, le=100),
    min_relevance: float | None = Query(default=None, ge=0, le=100),
    detected_after: datetime | None = None,
    sort: str = Query(default="score", pattern=SORT_PATTERN),
    limit: int = Query(default=50, ge=1, le=200),
    offset: int = Query(default=0, ge=0),
    session: AsyncSession = Depends(db_session),
    user: User = Depends(current_user),
) -> Page[OpportunityOut]:
    """The global feed. Every row carries both scores, side by side and separate.

    Filtering or sorting by relevance rescores the caller's own profile first,
    because relevance is not stored on the global row and must never be.
    """
    stmt = sa.select(Opportunity).outerjoin(Trend, Trend.id == Opportunity.primary_trend_id)
    filters = []
    if opportunity_type:
        filters.append(Opportunity.opportunity_type == opportunity_type)
    if country:
        filters.append(Opportunity.country == country)
    if industry:
        filters.append(Opportunity.industry == industry)
    if state:
        filters.append(Opportunity.state == state)
    if risk_level:
        filters.append(Opportunity.risk_level == risk_level)
    if validation_status:
        filters.append(Opportunity.validation_status == validation_status)
    if min_score is not None:
        filters.append(Opportunity.opportunity_score >= min_score)
    if min_confidence is not None:
        filters.append(Opportunity.confidence >= min_confidence)
    if detected_after is not None:
        filters.append(Opportunity.detected_at >= detected_after)
    if filters:
        stmt = stmt.where(*filters)

    needs_relevance = sort == "relevance" or min_relevance is not None
    if needs_relevance:
        # Relevance is per-user, so it cannot be filtered or ordered in SQL until
        # this caller's rows exist. Score the filtered set, then page in memory.
        candidates = list(
            (
                await session.execute(
                    sa.select(Opportunity)
                    .where(*filters)
                    .order_by(Opportunity.opportunity_score.desc())
                    .limit(RELEVANCE_CAP)
                )
            ).scalars()
        )
        results = await _score_for_caller(session, user, candidates)
        kept = [
            opp
            for opp in candidates
            if min_relevance is None
            or (results[opp.id].relevance if opp.id in results else 0.0) >= min_relevance
        ]
        if sort == "relevance":
            kept.sort(key=lambda o: results[o.id].relevance if o.id in results else 0.0, reverse=True)
        page = kept[offset : offset + limit]
        trends = {
            t.id: t
            for t in (
                await session.execute(
                    sa.select(Trend).where(
                        Trend.id.in_([o.primary_trend_id for o in page if o.primary_trend_id])
                    )
                )
            ).scalars()
        }
        return Page(
            items=[
                _apply_relevance(_with_trend(opp, trends.get(opp.primary_trend_id)), results.get(opp.id))
                for opp in page
            ],
            total=len(kept),
            limit=limit,
            offset=offset,
        )

    total = (
        await session.execute(
            sa.select(sa.func.count()).select_from(Opportunity).where(*filters)
            if filters
            else sa.select(sa.func.count()).select_from(Opportunity)
        )
    ).scalar_one()

    order = (Trend.trend_score.desc(),) if sort == "fastest_trend" else SORTABLE[sort]
    rows = (await session.execute(stmt.add_columns(Trend).order_by(*order).limit(limit).offset(offset))).all()
    results = await _score_for_caller(session, user, [opp for opp, _ in rows])

    return Page(
        items=[_apply_relevance(_with_trend(opp, trend), results.get(opp.id)) for opp, trend in rows],
        total=total,
        limit=limit,
        offset=offset,
    )


async def _evidence_rows(session: AsyncSession, trend_id: uuid.UUID | None) -> list[EvidenceRow]:
    """The measurements behind a candidate, grouped by the kind of evidence they are."""
    if trend_id is None:
        return []
    rows = (
        await session.execute(
            sa.select(TrendSignal, Source, Signal)
            .join(Source, Source.id == TrendSignal.source_id)
            .join(Signal, Signal.id == TrendSignal.signal_id)
            .where(TrendSignal.trend_id == trend_id)
        )
    ).all()
    return [
        EvidenceRow(
            kind=class_of(ts.signal_type),
            signal_type=ts.signal_type,
            source_slug=source.slug,
            source_group=ts.source_group,
            reliability=float(source.reliability or 0.5),
            is_proxy=bool(ts.is_proxy),
            growth_30d=ts.growth_30d,
            observation_count=ts.observation_count,
            signal_id=signal.id,
        )
        for ts, source, signal in rows
    ]


async def _load_detail(session: AsyncSession, opportunity_id: uuid.UUID) -> tuple:
    opp = await session.get(Opportunity, opportunity_id)
    if opp is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "No such opportunity.")
    trend = await session.get(Trend, opp.primary_trend_id) if opp.primary_trend_id else None
    risks = (
        (await session.execute(sa.select(OpportunityRisk).where(OpportunityRisk.opportunity_id == opp.id)))
        .scalars()
        .all()
    )
    conditions = (
        (
            await session.execute(
                sa.select(OpportunityCondition)
                .where(OpportunityCondition.opportunity_id == opp.id)
                .order_by(OpportunityCondition.kind, OpportunityCondition.created_at)
            )
        )
        .scalars()
        .all()
    )
    participation = (
        (
            await session.execute(
                sa.select(OpportunityParticipation).where(OpportunityParticipation.opportunity_id == opp.id)
            )
        )
        .scalars()
        .all()
    )
    skeptic = (
        await session.execute(
            sa.select(SkepticReview)
            .where(SkepticReview.opportunity_id == opp.id)
            .order_by(SkepticReview.created_at.desc())
            .limit(1)
        )
    ).scalar_one_or_none()
    return opp, trend, risks, conditions, participation, skeptic


@router.get("/opportunities/{opportunity_id}", response_model=OpportunityDetailOut)
async def opportunity_detail(
    opportunity_id: uuid.UUID,
    session: AsyncSession = Depends(db_session),
    user: User = Depends(current_user),
) -> OpportunityDetailOut:
    opp, trend, risks, conditions, participation, skeptic = await _load_detail(session, opportunity_id)
    decisions = (
        (
            await session.execute(
                sa.select(OpportunityDecision)
                .where(OpportunityDecision.opportunity_id == opp.id)
                .order_by(OpportunityDecision.decided_at.desc())
            )
        )
        .scalars()
        .all()
    )
    history = (
        (
            await session.execute(
                sa.select(OpportunityScore)
                .where(OpportunityScore.opportunity_id == opp.id)
                .order_by(OpportunityScore.computed_at)
            )
        )
        .scalars()
        .all()
    )

    out = OpportunityDetailOut.model_validate(opp)
    if trend is not None:
        out.trend_id = trend.id
        out.trend_score = trend.trend_score
        out.trend_confidence = trend.confidence
        out.trend_stage = trend.stage
        out.trend_name = trend.name
    out.risks = [RiskOut.model_validate(r) for r in risks]
    out.conditions = [ConditionOut.model_validate(c) for c in conditions]
    out.participation = [ParticipationOut.model_validate(p) for p in participation]
    out.evidence = await _evidence_rows(session, opp.primary_trend_id)
    out.skeptic = SkepticOut.model_validate(skeptic) if skeptic else None
    out.decisions = [DecisionOut.model_validate(d) for d in decisions]
    out.history = [ScoreSnapshotOut.model_validate(h) for h in history]

    result = (await _score_for_caller(session, user, [opp])).get(opp.id)
    _apply_relevance(out, result)
    if result is not None:
        out.user_relevance_parts = result.parts
        out.path_relevance = result.path_relevance
        out.relevance_notes = result.notes
    return out


@router.get("/opportunities/{opportunity_id}/report", response_model=ReportOut)
async def opportunity_report(
    opportunity_id: uuid.UUID,
    narrate_sections: bool = Query(
        default=False,
        description=(
            "Ask a language model to phrase three sections. Requires a configured "
            "provider; a draft that fails the grounding check is discarded, not repaired."
        ),
    ),
    session: AsyncSession = Depends(db_session),
    user: User = Depends(current_user),
) -> ReportOut:
    opp, trend, risks, conditions, participation, skeptic = await _load_detail(session, opportunity_id)
    evidence = await _evidence_rows(session, opp.primary_trend_id)

    narrated: dict[str, str] = {}
    note: str | None = None
    if narrate_sections:
        facts = build_facts(opp, risks=risks, skeptic=skeptic, conditions=conditions)
        provider = get_provider()
        narrated, response = await narrate(provider, opp, facts)
        session.add(
            ModelRun(
                provider=provider.name,
                model=getattr(response, "model", "unknown"),
                prompt_name="opportunity_report",
                prompt_version=OPPORTUNITY_VERSION,
                input_tokens=getattr(response, "input_tokens", 0),
                output_tokens=getattr(response, "output_tokens", 0),
                cost_usd=getattr(response, "cost_usd", 0.0),
                latency_ms=getattr(response, "latency_ms", 0),
                status="succeeded" if narrated else "rejected",
                started_at=datetime.now(UTC),
            )
        )
        await session.commit()
        if not narrated:
            note = (
                "A narrated version was requested but was not stored: either no language "
                "model is configured, or the draft failed the grounding check. The "
                "deterministic report below is complete and unaffected."
            )

    sections = build_report(
        opp,
        trend=trend,
        risks=risks,
        skeptic=skeptic,
        conditions=conditions,
        participation=participation,
        evidence=[e.model_dump(mode="json") for e in evidence],
        narrated=narrated,
    )
    return ReportOut(
        opportunity_id=opp.id,
        sections=[
            ReportSectionOut(key=s.key, title=s.title, body=s.body, items=s.items, generated=s.generated)
            for s in sections
        ],
        narrated=bool(narrated),
        narration_note=note,
    )


@router.post("/opportunities/generate", response_model=GenerateResult)
async def run_generation(
    session: AsyncSession = Depends(db_session),
    user: User = Depends(require_admin),
) -> GenerateResult:
    """Re-run the whole pipeline. Updates existing candidates; never duplicates them."""
    result = await generate_opportunities(
        session, contexts=SCENARIO_CONTEXT, validation_status=ValidationStatus.DEMO
    )
    session.add(
        SystemAuditLog(
            actor_user_id=user.id,
            action="opportunities.generate",
            object_type="opportunity",
            object_id=None,
            after={
                "created": len(result.created),
                "updated": len(result.updated),
                "rejected": len(result.rejections),
                "version": OPPORTUNITY_VERSION,
            },
        )
    )
    await session.commit()
    return GenerateResult(
        created=len(result.created),
        updated=len(result.updated),
        rejected=len(result.rejections),
        algorithm_version=OPPORTUNITY_VERSION,
        validation_status=ValidationStatus.DEMO,
        rejections=[
            RejectionOut(
                trend_id=r.trend_id,
                trend_name=r.trend_name,
                opportunity_type=r.opportunity_type,
                reasons=r.reasons,
            )
            for r in result.rejections
        ],
    )


@router.get("/opportunities/{opportunity_id}/decisions", response_model=list[DecisionOut])
async def list_decisions(
    opportunity_id: uuid.UUID,
    session: AsyncSession = Depends(db_session),
    _: User = Depends(current_user),
) -> list[DecisionOut]:
    rows = (
        (
            await session.execute(
                sa.select(OpportunityDecision)
                .where(OpportunityDecision.opportunity_id == opportunity_id)
                .order_by(OpportunityDecision.decided_at.desc())
            )
        )
        .scalars()
        .all()
    )
    return [DecisionOut.model_validate(r) for r in rows]


@router.post(
    "/opportunities/{opportunity_id}/decisions",
    response_model=DecisionOut,
    status_code=status.HTTP_201_CREATED,
)
async def record_decision(
    opportunity_id: uuid.UUID,
    body: DecisionIn,
    session: AsyncSession = Depends(db_session),
    user: User = Depends(require_analyst),
) -> DecisionOut:
    """Record what the person decided, with the numbers frozen as they stood.

    Decisions are append-only. Changing your mind adds a row; it never edits one,
    because Phase 6 needs to ask what the system was saying at the moment of each
    decision, not what it says now.
    """
    opp = await session.get(Opportunity, opportunity_id)
    if opp is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "No such opportunity.")

    # Freeze *this user's* relevance as it stood, not a global number: the
    # question Phase 6 will ask is what this person was shown.
    mine = (await _score_for_caller(session, user, [opp])).get(opp.id)

    decision = OpportunityDecision(
        opportunity_id=opp.id,
        user_id=user.id,
        interest=body.interest,
        note=body.note,
        decided_at=datetime.now(UTC),
        score_at_decision=opp.opportunity_score,
        confidence_at_decision=opp.confidence,
        risk_at_decision=opp.risk_level,
        relevance_at_decision=mine.relevance if mine else None,
        state_at_decision=opp.state,
        algorithm_version=opp.algorithm_version,
    )
    session.add(decision)
    session.add(
        SystemAuditLog(
            actor_user_id=user.id,
            action="opportunity.decision",
            object_type="opportunity",
            object_id=str(opp.id),
            after={"interest": str(body.interest), "score": opp.opportunity_score},
        )
    )
    await session.commit()
    await session.refresh(decision)
    return DecisionOut.model_validate(decision)
