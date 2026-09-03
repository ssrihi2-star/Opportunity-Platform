from __future__ import annotations

import dataclasses
import uuid

import sqlalchemy as sa
from fastapi import APIRouter, Depends, HTTPException, Query, status
from sqlalchemy.ext.asyncio import AsyncSession

from app.analytics.statistics import analyse_series
from app.api.deps import current_user, db_session
from app.models.models import Entity, Signal, SignalObservation, Source, User
from app.schemas.common import Page
from app.schemas.signals import (
    EntityOut,
    ObservationOut,
    SeriesStatsOut,
    SignalDetailOut,
    SignalOut,
)

router = APIRouter(tags=["signals"])


@router.get("/signals", response_model=Page[SignalOut])
async def list_signals(
    entity_id: uuid.UUID | None = None,
    signal_type: str | None = None,
    signal_class: str | None = None,
    geo_scope: str | None = None,
    source_id: uuid.UUID | None = None,
    limit: int = Query(default=50, ge=1, le=200),
    offset: int = Query(default=0, ge=0),
    _: User = Depends(current_user),
    session: AsyncSession = Depends(db_session),
) -> Page[SignalOut]:
    where = []
    if entity_id:
        where.append(Signal.entity_id == entity_id)
    if signal_type:
        where.append(Signal.signal_type == signal_type)
    if signal_class:
        where.append(Signal.signal_class == signal_class)
    if geo_scope:
        where.append(Signal.geo_scope == geo_scope)
    if source_id:
        where.append(Signal.source_id == source_id)

    total = (await session.execute(sa.select(sa.func.count(Signal.id)).where(*where))).scalar_one()
    rows = (
        await session.execute(
            sa.select(Signal, Entity.canonical_name, Source.slug)
            .join(Entity, Entity.id == Signal.entity_id)
            .join(Source, Source.id == Signal.source_id)
            .where(*where)
            .order_by(Entity.canonical_name, Signal.signal_type)
            .limit(limit)
            .offset(offset)
        )
    ).all()
    items = []
    for signal, entity_name, source_slug in rows:
        out = SignalOut.model_validate(signal)
        out.entity_name = entity_name
        out.source_slug = source_slug
        items.append(out)
    return Page[SignalOut](items=items, total=int(total), limit=limit, offset=offset)


@router.get("/signals/{signal_id}", response_model=SignalDetailOut)
async def signal_detail(
    signal_id: uuid.UUID,
    _: User = Depends(current_user),
    session: AsyncSession = Depends(db_session),
) -> SignalDetailOut:
    signal = await session.get(Signal, signal_id)
    if signal is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Signal not found.")
    entity = await session.get(Entity, signal.entity_id)
    source = await session.get(Source, signal.source_id)
    observations = (
        (
            await session.execute(
                sa.select(SignalObservation)
                .where(SignalObservation.signal_id == signal_id)
                .order_by(SignalObservation.observed_at)
            )
        )
        .scalars()
        .all()
    )
    stats = analyse_series([o.value for o in observations])
    detail = SignalDetailOut(
        **SignalOut.model_validate(signal).model_dump(),
        observations=[ObservationOut.model_validate(o) for o in observations],
        stats=SeriesStatsOut(**dataclasses.asdict(stats)),
    )
    detail.entity_name = entity.canonical_name if entity else None
    detail.source_slug = source.slug if source else None
    return detail


@router.get("/entities", response_model=Page[EntityOut])
async def list_entities(
    q: str | None = None,
    entity_type: str | None = None,
    limit: int = Query(default=50, ge=1, le=200),
    offset: int = Query(default=0, ge=0),
    _: User = Depends(current_user),
    session: AsyncSession = Depends(db_session),
) -> Page[EntityOut]:
    where = []
    if q:
        where.append(Entity.normalized.contains(q.lower()))
    if entity_type:
        where.append(Entity.entity_type == entity_type)
    total = (await session.execute(sa.select(sa.func.count(Entity.id)).where(*where))).scalar_one()
    rows = (
        (
            await session.execute(
                sa.select(Entity).where(*where).order_by(Entity.canonical_name).limit(limit).offset(offset)
            )
        )
        .scalars()
        .all()
    )
    return Page[EntityOut](
        items=[EntityOut.model_validate(r) for r in rows],
        total=int(total),
        limit=limit,
        offset=offset,
    )
