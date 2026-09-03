from __future__ import annotations

from datetime import UTC, datetime

import sqlalchemy as sa
from fastapi import APIRouter, Depends
from sqlalchemy.ext.asyncio import AsyncSession

from app.analytics.statistics import analyse_series
from app.api.deps import current_user, db_session
from app.db.base import as_utc
from app.models.models import (
    Entity,
    RawRecord,
    Signal,
    SignalObservation,
    Source,
    SourceRun,
    User,
)
from app.schemas.signals import OverviewOut

router = APIRouter(tags=["overview"])

DISCLAIMER = (
    "This is a research tool, not financial advice. Opportunities may fail. Historical "
    "performance does not guarantee future results. AI-generated analysis may contain "
    "errors. Conduct your own independent research. High-risk assets can result in total loss."
)


@router.get("/overview", response_model=OverviewOut)
async def overview(
    _: User = Depends(current_user), session: AsyncSession = Depends(db_session)
) -> OverviewOut:
    async def count_of(model) -> int:
        return int((await session.execute(sa.select(sa.func.count(model.id)))).scalar_one())

    counts = {
        "sources": await count_of(Source),
        "sources_enabled": int(
            (await session.execute(sa.select(sa.func.count(Source.id)).where(Source.enabled))).scalar_one()
        ),
        "raw_records": await count_of(RawRecord),
        "entities": await count_of(Entity),
        "signals": await count_of(Signal),
        "observations": await count_of(SignalObservation),
        # Phase 4 objects do not exist yet; reported as zero rather than hidden.
        "opportunities": 0,
        "reports": 0,
    }

    # Rank signals by measured growth over the stored window. Deterministic, no model.
    signal_rows = (
        await session.execute(
            sa.select(Signal, Entity.canonical_name, Source.slug)
            .join(Entity, Entity.id == Signal.entity_id)
            .join(Source, Source.id == Signal.source_id)
        )
    ).all()

    all_values = (
        await session.execute(
            sa.select(SignalObservation.signal_id, SignalObservation.value).order_by(
                SignalObservation.signal_id, SignalObservation.observed_at
            )
        )
    ).all()
    by_signal: dict = {}
    for signal_id, value in all_values:
        by_signal.setdefault(signal_id, []).append(value)

    ranked: list[dict] = []
    for signal, entity_name, source_slug in signal_rows:
        stats = analyse_series(by_signal.get(signal.id, []))
        if stats.pct_change_window is None:
            continue
        ranked.append(
            {
                "signal_id": str(signal.id),
                "entity": entity_name,
                "source": source_slug,
                "signal_type": signal.signal_type,
                "signal_class": signal.signal_class,
                "is_proxy": signal.is_proxy,
                "pct_change_window": round(stats.pct_change_window, 2),
                "acceleration": round(stats.acceleration, 2) if stats.acceleration is not None else None,
                "zscore": round(stats.zscore_last, 2) if stats.zscore_last is not None else None,
                "is_anomaly": stats.is_anomaly,
                "strength": stats.strength,
                "observations": stats.n,
            }
        )
    ranked.sort(key=lambda r: r["pct_change_window"], reverse=True)

    sources = list((await session.execute(sa.select(Source).order_by(Source.slug))).scalars().all())
    now = datetime.now(UTC)
    health = [
        {
            "slug": s.slug,
            "name": s.name,
            "adapter_key": s.adapter_key,
            "enabled": s.enabled,
            "status": s.status,
            "reliability": s.reliability,
            "consecutive_failures": s.consecutive_failures,
            "freshness_hours": (
                round((now - as_utc(s.last_success_at)).total_seconds() / 3600, 1)
                if s.last_success_at
                else None
            ),
        }
        for s in sources
    ]

    runs = (
        await session.execute(
            sa.select(SourceRun, Source.slug)
            .join(Source, Source.id == SourceRun.source_id)
            .order_by(SourceRun.started_at.desc())
            .limit(10)
        )
    ).all()
    activity = [
        {
            "source": slug,
            "status": run.status,
            "started_at": run.started_at.isoformat(),
            "fetched": run.records_fetched,
            "stored": run.records_stored,
            "duplicates": run.records_duplicate,
            "http_requests": run.http_requests,
            "error": run.error,
        }
        for run, slug in runs
    ]

    return OverviewOut(
        counts=counts,
        fastest_growing=ranked[:12],
        source_health=health,
        recent_activity=activity,
        disclaimer=DISCLAIMER,
    )
