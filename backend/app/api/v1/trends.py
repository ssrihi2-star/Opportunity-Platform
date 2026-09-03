from __future__ import annotations

import uuid

import sqlalchemy as sa
from fastapi import APIRouter, Depends, HTTPException, Query, status
from sqlalchemy.ext.asyncio import AsyncSession

from app.ai.provider import get_provider
from app.ai.trend_explainer import explain_trend
from app.api.deps import current_user, db_session, require_admin
from app.models.enums import MatchDecision
from app.models.models import (
    Entity,
    EntityMatchCandidate,
    ModelRun,
    RawRecord,
    Signal,
    SignalObservation,
    Source,
    SystemAuditLog,
    Topic,
    TopicEntity,
    Trend,
    TrendSignal,
    TrendSnapshot,
    User,
)
from app.schemas.common import Page
from app.schemas.trends import (
    DecisionIn,
    EvaluateResult,
    EvidenceEvent,
    MatchCandidateOut,
    SnapshotOut,
    TopicOut,
    TrendDetailOut,
    TrendOut,
    TrendPoint,
    TrendSignalOut,
)
from app.services.entities import apply_decision
from app.services.topics import rebuild_topics
from app.services.trends import evaluate_trends

router = APIRouter(tags=["trends"])

SORTABLE = {
    "score": (Trend.trend_score.desc(), Trend.confidence.desc()),
    "confidence": (Trend.confidence.desc(), Trend.trend_score.desc()),
    "newest": (Trend.first_detected_at.desc(),),
    "updated": (Trend.last_evaluated_at.desc(),),
}


@router.get("/trends", response_model=Page[TrendOut])
async def list_trends(
    category: str | None = None,
    stage: str | None = None,
    state: str | None = None,
    geo_scope: str | None = None,
    subject_type: str | None = None,
    min_score: float | None = Query(default=None, ge=0, le=100),
    min_confidence: float | None = Query(default=None, ge=0, le=100),
    include_spikes: bool = Query(default=True, description="Include one-day spikes."),
    include_seasonal: bool = Query(default=True, description="Include seasonal patterns."),
    sort: str = Query(default="score", pattern="^(score|confidence|newest|updated|acceleration)$"),
    limit: int = Query(default=50, ge=1, le=200),
    offset: int = Query(default=0, ge=0),
    _: User = Depends(current_user),
    session: AsyncSession = Depends(db_session),
) -> Page[TrendOut]:
    where = []
    if category:
        where.append(Trend.category == category)
    if stage:
        where.append(Trend.stage == stage)
    if state:
        where.append(Trend.state == state)
    if geo_scope:
        where.append(Trend.geo_scope == geo_scope)
    if subject_type:
        where.append(Trend.subject_type == subject_type)
    if min_score is not None:
        where.append(Trend.trend_score >= min_score)
    if min_confidence is not None:
        where.append(Trend.confidence >= min_confidence)
    if not include_spikes:
        where.append(Trend.is_spike.is_(False))
    if not include_seasonal:
        where.append(Trend.is_seasonal.is_(False))

    total = (await session.execute(sa.select(sa.func.count(Trend.id)).where(*where))).scalar_one()

    if sort == "acceleration":
        # Acceleration lives inside the metrics blob, so it is sorted in Python.
        rows = (await session.execute(sa.select(Trend).where(*where))).scalars().all()
        rows.sort(key=lambda t: (t.metrics or {}).get("acceleration_pp") or -1e9, reverse=True)
        page = rows[offset : offset + limit]
    else:
        page = (
            (
                await session.execute(
                    sa.select(Trend).where(*where).order_by(*SORTABLE[sort]).limit(limit).offset(offset)
                )
            )
            .scalars()
            .all()
        )

    return Page[TrendOut](
        items=[TrendOut.model_validate(t) for t in page],
        total=int(total),
        limit=limit,
        offset=offset,
    )


@router.post("/trends/evaluate", response_model=EvaluateResult)
async def run_evaluation(
    user: User = Depends(require_admin), session: AsyncSession = Depends(db_session)
) -> EvaluateResult:
    """Recompute topics and every trend. Idempotent; updates rather than duplicates."""
    before = (await session.execute(sa.select(sa.func.count(Trend.id)))).scalar_one()
    topics = await rebuild_topics(session)
    trends = await evaluate_trends(session)
    after = (await session.execute(sa.select(sa.func.count(Trend.id)))).scalar_one()
    session.add(
        SystemAuditLog(
            actor_user_id=user.id,
            actor_label=user.email,
            action="trends.evaluate",
            after={"evaluated": len(trends), "topics": len(topics)},
        )
    )
    return EvaluateResult(
        evaluated=len(trends),
        created=int(after) - int(before),
        topics=len(topics),
        detail=(
            f"Evaluated {len(trends)} trend(s) across {len(topics)} topic(s); "
            f"{int(after) - int(before)} newly detected, the rest updated in place."
        ),
    )


@router.get("/trends/{trend_id}", response_model=TrendDetailOut)
async def trend_detail(
    trend_id: uuid.UUID,
    _: User = Depends(current_user),
    session: AsyncSession = Depends(db_session),
) -> TrendDetailOut:
    trend = await session.get(Trend, trend_id)
    if trend is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Trend not found.")

    links = (
        await session.execute(
            sa.select(TrendSignal, Source.slug, Signal)
            .join(Source, Source.id == TrendSignal.source_id)
            .join(Signal, Signal.id == TrendSignal.signal_id)
            .where(TrendSignal.trend_id == trend.id)
            .order_by(TrendSignal.contribution.desc())
        )
    ).all()
    signals_out: list[TrendSignalOut] = []
    for link, slug, _signal in links:
        row = TrendSignalOut.model_validate(link)
        row.source_slug = slug
        signals_out.append(row)

    series: list[TrendPoint] = []
    for link, slug, signal in links:
        observations = (
            (
                await session.execute(
                    sa.select(SignalObservation)
                    .where(SignalObservation.signal_id == link.signal_id)
                    .order_by(SignalObservation.observed_at)
                )
            )
            .scalars()
            .all()
        )
        for obs in observations:
            series.append(
                TrendPoint(
                    at=obs.observed_at,
                    value=obs.value,
                    status=obs.status,
                    signal_type=link.signal_type,
                    source_slug=slug,
                    unit=signal.unit,
                    currency=obs.currency,
                )
            )

    history = (
        (
            await session.execute(
                sa.select(TrendSnapshot)
                .where(TrendSnapshot.trend_id == trend.id)
                .order_by(TrendSnapshot.evaluated_at)
            )
        )
        .scalars()
        .all()
    )

    stage_words = trend.stage.replace("_", " ")
    article = "an" if stage_words[:1].lower() in "aeiou" else "a"
    evidence: list[EvidenceEvent] = [
        EvidenceEvent(
            at=trend.first_detected_at,
            kind="detected",
            detail=f"First detected as {article} {stage_words} trend.",
        )
    ]
    for snapshot in history:
        evidence.append(
            EvidenceEvent(
                at=snapshot.evaluated_at,
                kind="evaluated",
                detail=(
                    f"Scored {snapshot.trend_score:.0f} at confidence {snapshot.confidence:.0f} "
                    f"({snapshot.stage.replace('_', ' ')}, {snapshot.state})."
                ),
            )
        )
    source_ids = {link.source_id for link, _slug, _sig in links}
    if source_ids:
        records = (
            await session.execute(
                sa.select(RawRecord, Source.slug)
                .join(Source, Source.id == RawRecord.source_id)
                .where(RawRecord.source_id.in_(list(source_ids)), RawRecord.url.is_not(None))
                .order_by(RawRecord.published_at.desc().nullslast())
                .limit(15)
            )
        ).all()
        for record, slug in records:
            evidence.append(
                EvidenceEvent(
                    at=record.published_at or record.fetched_at,
                    kind="source_record",
                    detail=record.title or "(untitled record)",
                    url=record.url,
                    source_slug=slug,
                )
            )
    evidence.sort(key=lambda e: e.at)

    related: list[dict] = []
    if trend.topic_id:
        rows = (
            (
                await session.execute(
                    sa.select(Entity)
                    .join(TopicEntity, TopicEntity.entity_id == Entity.id)
                    .where(TopicEntity.topic_id == trend.topic_id)
                )
            )
            .scalars()
            .all()
        )
        related = [
            {
                "id": str(e.id),
                "name": e.canonical_name,
                "type": e.entity_type,
                "external_ids": e.external_ids or {},
            }
            for e in rows
        ]
    elif trend.entity_id:
        entity = await session.get(Entity, trend.entity_id)
        if entity is not None:
            related = [
                {
                    "id": str(entity.id),
                    "name": entity.canonical_name,
                    "type": entity.entity_type,
                    "external_ids": entity.external_ids or {},
                }
            ]

    detail = TrendDetailOut(
        **TrendOut.model_validate(trend).model_dump(),
        components=trend.components or {},
        penalties=trend.penalties or {},
        metrics=trend.metrics or {},
        explanation=trend.explanation,
        signals=signals_out,
        series=series,
        history=[SnapshotOut.model_validate(s) for s in history],
        evidence=evidence,
        related_entities=related,
    )
    return detail


@router.post("/trends/{trend_id}/explain", response_model=TrendDetailOut)
async def explain(
    trend_id: uuid.UUID,
    user: User = Depends(require_admin),
    session: AsyncSession = Depends(db_session),
) -> TrendDetailOut:
    """Ask the configured model to narrate the numbers. It cannot add to them."""
    trend = await session.get(Trend, trend_id)
    if trend is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Trend not found.")

    rows = (
        await session.execute(
            sa.select(TrendSignal, Source.slug)
            .join(Source, Source.id == TrendSignal.source_id)
            .where(TrendSignal.trend_id == trend.id)
        )
    ).all()
    signal_rows = [
        {"signal_type": link.signal_type, "source_slug": slug, "observation_count": link.observation_count}
        for link, slug in rows
    ]

    provider = get_provider()
    text, response = await explain_trend(provider, trend, signal_rows)
    run = ModelRun(
        provider=response.provider,
        model=response.model,
        purpose="trend_explanation",
        input_tokens=response.input_tokens,
        output_tokens=response.output_tokens,
        cost_usd=response.cost_usd,
        latency_ms=response.latency_ms,
        succeeded=text is not None,
        error=None if text else "Explanation failed grounding checks or provider returned none.",
    )
    session.add(run)
    await session.flush()
    trend.explanation = text
    trend.explanation_model_run_id = run.id
    session.add(
        SystemAuditLog(
            actor_user_id=user.id,
            actor_label=user.email,
            action="trend.explain",
            object_type="trend",
            object_id=str(trend.id),
            after={"grounded": text is not None, "provider": response.provider},
        )
    )
    await session.flush()
    return await trend_detail(trend_id, user, session)


# --------------------------------------------------------------------- topics
@router.get("/topics", response_model=list[TopicOut])
async def list_topics(
    _: User = Depends(current_user), session: AsyncSession = Depends(db_session)
) -> list[TopicOut]:
    topics = (await session.execute(sa.select(Topic).order_by(Topic.label))).scalars().all()
    out: list[TopicOut] = []
    for topic in topics:
        names = (
            (
                await session.execute(
                    sa.select(Entity.canonical_name)
                    .join(TopicEntity, TopicEntity.entity_id == Entity.id)
                    .where(TopicEntity.topic_id == topic.id)
                    .order_by(Entity.canonical_name)
                )
            )
            .scalars()
            .all()
        )
        row = TopicOut.model_validate(topic)
        row.entity_names = list(names)
        out.append(row)
    return out


# ------------------------------------------------------------- entity review
@router.get("/entity-review", response_model=list[MatchCandidateOut])
async def list_match_candidates(
    decision: str = Query(default="pending", pattern="^(pending|confirmed|rejected|keep_separate|all)$"),
    _: User = Depends(current_user),
    session: AsyncSession = Depends(db_session),
) -> list[MatchCandidateOut]:
    """Uncertain name matches the system refused to guess at."""
    where = [] if decision == "all" else [EntityMatchCandidate.decision == decision]
    rows = (
        await session.execute(
            sa.select(EntityMatchCandidate, Entity)
            .join(Entity, Entity.id == EntityMatchCandidate.candidate_entity_id)
            .where(*where)
            .order_by(EntityMatchCandidate.confidence.desc())
        )
    ).all()
    out: list[MatchCandidateOut] = []
    for candidate, entity in rows:
        row = MatchCandidateOut.model_validate(candidate)
        row.candidate_entity_name = entity.canonical_name
        row.candidate_external_ids = entity.external_ids or {}
        out.append(row)
    return out


@router.post("/entity-review/{candidate_id}", response_model=MatchCandidateOut)
async def decide_match(
    candidate_id: uuid.UUID,
    payload: DecisionIn,
    user: User = Depends(require_admin),
    session: AsyncSession = Depends(db_session),
) -> MatchCandidateOut:
    """Record a human ruling. Confirmations merge; the decision is remembered forever."""
    candidate = await session.get(EntityMatchCandidate, candidate_id)
    if candidate is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Match candidate not found.")
    if candidate.decision != MatchDecision.PENDING:
        raise HTTPException(
            status.HTTP_409_CONFLICT,
            f"This match was already decided as '{candidate.decision}' on "
            f"{candidate.decided_at:%Y-%m-%d}. Decisions are kept, not overwritten.",
        )
    await apply_decision(session, candidate, payload.decision, user_id=user.id, note=payload.note)
    session.add(
        SystemAuditLog(
            actor_user_id=user.id,
            actor_label=user.email,
            action="entity.match_decision",
            object_type="entity_match_candidate",
            object_id=str(candidate.id),
            after={"decision": payload.decision, "observed_name": candidate.observed_name},
        )
    )
    entity = await session.get(Entity, candidate.candidate_entity_id)
    row = MatchCandidateOut.model_validate(candidate)
    row.candidate_entity_name = entity.canonical_name if entity else None
    row.candidate_external_ids = (entity.external_ids or {}) if entity else {}
    return row
