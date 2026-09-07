"""The trend engine: turn stored observations into tracked, scored trends.

Reads observations, measures each series, judges corroboration, scores, and then
**updates the existing trend row** rather than creating a new one. That last part
is what makes the history worth anything: a trend the system has followed for
three months, with its peak score and its stage changes recorded, is a far more
useful object than a fresh "discovery" printed every morning.
"""

from __future__ import annotations

from collections import defaultdict
from dataclasses import asdict, dataclass, field
from datetime import UTC, datetime
from typing import Any

import sqlalchemy as sa
from sqlalchemy.ext.asyncio import AsyncSession

from app.analytics.confirmation import SignalRef, assess_confirmation
from app.analytics.dedup import duplication_ratio
from app.analytics.growth import GrowthProfile, Point, analyse_growth
from app.analytics.trend_scoring import (
    TREND_FORMULA_VERSION,
    TrendInput,
    TrendScore,
    next_state,
    score_trend,
)
from app.core.logging import get_logger
from app.db.base import as_utc
from app.models.enums import AnalysisMode, ObservationStatus, SubjectType, TrendState
from app.models.models import (
    Entity,
    RawRecord,
    Signal,
    SignalObservation,
    Source,
    Topic,
    TopicEntity,
    Trend,
    TrendSignal,
    TrendSnapshot,
)
from app.sources.provenance import live_eligibility

log = get_logger("trends")

#: A series with fewer points than this cannot say anything about a trend.
MIN_OBSERVATIONS = 4

#: A live-only trend needs at least this many eligible series before it is worth
#: evaluating at all. Below it there is nothing to aggregate, and the honest
#: answer is "not enough live evidence", not a score computed from one series and
#: quietly topped up from the demo scenarios.
MIN_LIVE_SERIES = 1


@dataclass(slots=True)
class ExcludedSource:
    """A source whose evidence live-only mode refused, and the reason."""

    slug: str
    adapter_key: str
    source_class: str
    reason: str
    signal_count: int = 0


@dataclass(slots=True)
class SeriesBundle:
    """One measured series, with everything needed to judge and to explain it."""

    signal: Signal
    source: Source
    entity: Entity
    profile: GrowthProfile
    points: list[Point] = field(default_factory=list)

    @property
    def weight(self) -> float:
        """Reliability, discounted for proxies. A proxy is evidence, but weaker."""
        return self.source.reliability * (0.6 if self.signal.is_proxy else 1.0)


async def _load_series(
    session: AsyncSession, *, analysis_mode: str = AnalysisMode.DEMO_INCLUSIVE
) -> tuple[dict[str, list[SeriesBundle]], list[ExcludedSource]]:
    """Every signal with enough observations, grouped by entity id.

    In `live_only` mode the demo, generator and manually-imported series are
    dropped **here**, before any measurement is taken, so nothing downstream —
    growth, corroboration counting, confidence, duplication, or the opportunity
    engine that reads the resulting trend_signals — can see them. Filtering only
    the displayed source list would leave every number computed from the mix.
    """
    rows = (
        await session.execute(
            sa.select(Signal, Source, Entity)
            .join(Source, Source.id == Signal.source_id)
            .join(Entity, Entity.id == Signal.entity_id)
        )
    ).all()

    excluded: dict[str, ExcludedSource] = {}
    if analysis_mode == AnalysisMode.LIVE_ONLY:
        kept = []
        for signal, source, entity in rows:
            eligible, reason = live_eligibility(source.adapter_key, source.source_class)
            if eligible:
                kept.append((signal, source, entity))
                continue
            record = excluded.setdefault(
                source.slug,
                ExcludedSource(
                    slug=source.slug,
                    adapter_key=source.adapter_key,
                    source_class=source.source_class,
                    reason=reason,
                ),
            )
            record.signal_count += 1
        rows = kept

    observations = (
        await session.execute(
            sa.select(
                SignalObservation.signal_id,
                SignalObservation.observed_at,
                SignalObservation.value,
                SignalObservation.status,
            ).order_by(SignalObservation.signal_id, SignalObservation.observed_at)
        )
    ).all()
    by_signal: dict[Any, list[Point]] = defaultdict(list)
    for signal_id, observed_at, value, status in observations:
        by_signal[signal_id].append(
            Point(at=as_utc(observed_at), value=value, status=status or ObservationStatus.OK)
        )

    bundles: dict[str, list[SeriesBundle]] = defaultdict(list)
    for signal, source, entity in rows:
        points = by_signal.get(signal.id, [])
        usable = [p for p in points if p.is_ok]
        if len(usable) < MIN_OBSERVATIONS:
            continue
        bundles[str(entity.id)].append(
            SeriesBundle(
                signal=signal,
                source=source,
                entity=entity,
                profile=analyse_growth(points),
                points=points,
            )
        )
    return bundles, sorted(excluded.values(), key=lambda e: e.slug)


async def _headlines(
    session: AsyncSession, source_ids: set[Any], limit: int = 200
) -> list[tuple[str, str | None]]:
    """Headlines of *documents* from the supporting sources, for syndication checks.

    Restricted to records carrying a URL. Measurement rows also have titles, but
    they are machine-generated one per day and would swamp the ratio with unique
    strings, hiding exactly the duplication this is meant to find.
    """
    if not source_ids:
        return []
    rows = (
        await session.execute(
            sa.select(RawRecord.source_id, RawRecord.title)
            .where(
                RawRecord.source_id.in_(list(source_ids)),
                RawRecord.title.is_not(None),
                RawRecord.url.is_not(None),
            )
            .order_by(RawRecord.fetched_at.desc())
            .limit(limit)
        )
    ).all()
    return [(str(source_id), title) for source_id, title in rows]


def _weighted(values: list[tuple[float, float]]) -> float | None:
    """Weighted mean of (value, weight) pairs, ignoring absent values."""
    pairs = [(v, w) for v, w in values if v is not None and w > 0]
    if not pairs:
        return None
    total = sum(w for _, w in pairs)
    if total == 0:
        return None
    return round(sum(v * w for v, w in pairs) / total, 4)


def aggregate(bundles: list[SeriesBundle]) -> tuple[TrendInput, SeriesBundle, list[str]]:
    """Combine several series into one judgement about the subject.

    Scale and novelty are read from a single **representative** series - the
    highest-weight non-proxy one - rather than mixed across sources, because
    GitHub stars and tonnes of ceramic have no common scale and averaging them
    would produce a number that means nothing.
    """
    notes: list[str] = []
    ranked = sorted(bundles, key=lambda b: (b.signal.is_proxy, -b.weight, b.signal.signal_type))
    representative = ranked[0]
    if representative.signal.is_proxy:
        notes.append(
            "Every supporting series is a proxy measurement, so the size figures below "
            "describe a stand-in rather than the activity itself."
        )

    growth_30 = _weighted([(b.profile.growth_30d, b.weight) for b in bundles])
    growth_90 = _weighted([(b.profile.growth_90d, b.weight) for b in bundles])
    acceleration = _weighted([(b.profile.acceleration_pp, b.weight) for b in bundles])
    persistence = _weighted([(b.profile.persistence, b.weight) for b in bundles])
    momentum = _weighted([(b.profile.momentum, b.weight) for b in bundles])

    directions = [b.profile.direction for b in bundles]
    if directions.count("rising") > len(directions) / 2:
        direction = "rising"
    elif directions.count("falling") > len(directions) / 2:
        direction = "falling"
    elif "rising" in directions and "falling" in directions:
        direction = "flat"
        notes.append("Supporting series disagree on direction.")
    else:
        direction = directions[0] if directions else "unknown"

    # A spike anywhere in a materially-weighted series taints the whole trend. The
    # threshold is low on purpose: if a quarter of the evidence is one loud day,
    # the honest reading is "one loud day", not "a trend with a caveat". One noisy
    # feed among five clean ones still falls below it.
    spiky_weight = sum(b.weight for b in bundles if b.profile.spike.is_one_day_spike)
    total_weight = sum(b.weight for b in bundles) or 1.0
    is_spike = spiky_weight / total_weight >= 0.25
    spike_note = next(
        (b.profile.spike.note for b in bundles if b.profile.spike.is_one_day_spike and b.profile.spike.note),
        "",
    )

    seasonal_weight = sum(b.weight for b in bundles if b.profile.seasonality.is_seasonal)
    is_seasonal = seasonal_weight / total_weight > 0.5
    seasonal_note = next(
        (b.profile.seasonality.note for b in bundles if b.profile.seasonality.is_seasonal), ""
    )

    rep = representative.profile
    ok_values = [p.value for p in representative.points if p.is_ok and p.value is not None]
    prior_max = max(ok_values[:-1]) if len(ok_values) > 1 else None

    agreeing = 0
    if (growth_30 or growth_90 or 0) > 0:
        agreeing += 1
    if (momentum or 0) > 0:
        agreeing += 1
    if (persistence or 0) >= 0.6:
        agreeing += 1

    last_at = max((b.profile.last_at for b in bundles if b.profile.last_at), default=None)
    days_since = (datetime.now(UTC) - last_at).days if last_at else 999

    refs = [
        SignalRef(
            signal_id=str(b.signal.id),
            signal_type=b.signal.signal_type,
            source_id=str(b.source.id),
            source_group=b.source.source_group or str(b.source.id),
            source_reliability=b.source.reliability,
            is_proxy=b.signal.is_proxy,
            observation_count=b.profile.observation_count,
        )
        for b in bundles
    ]
    confirmation = assess_confirmation(refs)

    inp = TrendInput(
        growth_30d=growth_30,
        growth_90d=growth_90,
        acceleration_pp=acceleration,
        persistence=persistence,
        momentum=momentum,
        direction=direction,
        baseline_value=rep.baseline_value,
        latest_value=rep.latest_value,
        absolute_growth_total=rep.absolute_growth_total,
        prior_maximum=prior_max,
        observation_count=sum(b.profile.observation_count for b in bundles),
        missing_count=sum(b.profile.missing_count for b in bundles),
        history_days=max((b.profile.history_days for b in bundles), default=0),
        days_since_last_observation=days_since,
        independent_sources=confirmation.independent_sources,
        distinct_signal_types=confirmation.distinct_signal_types,
        distinct_signal_classes=confirmation.distinct_signal_classes,
        non_proxy_classes=confirmation.non_proxy_classes,
        mean_source_reliability=confirmation.mean_reliability,
        dominant_source_share=confirmation.dominant_source_share,
        geo_scopes=sorted({b.signal.geo_scope for b in bundles}),
        is_one_day_spike=is_spike,
        is_seasonal=is_seasonal,
        seasonal_note=seasonal_note,
        spike_note=spike_note,
        agreeing_methods=agreeing,
    )
    notes.extend(confirmation.warnings)
    return inp, representative, notes


async def _upsert_trend(
    session: AsyncSession,
    *,
    subject_type: str,
    entity: Entity | None,
    topic: Topic | None,
    name: str,
    category: str | None,
    geo_scope: str,
    inp: TrendInput,
    result: TrendScore,
    bundles: list[SeriesBundle],
    extra_warnings: list[str],
    analysis_mode: str,
    now: datetime,
) -> Trend:
    where = [
        Trend.subject_type == subject_type,
        Trend.geo_scope == geo_scope,
        Trend.analysis_mode == analysis_mode,
    ]
    where.append(Trend.entity_id == (entity.id if entity else None))
    where.append(Trend.topic_id == (topic.id if topic else None))
    trend = (await session.execute(sa.select(Trend).where(*where))).scalar_one_or_none()

    warnings = list(dict.fromkeys(result.warnings + extra_warnings))
    metrics = {
        **asdict(inp),
        "representative_signal": bundles[0].signal.signal_type if bundles else None,
        "analysis_mode": analysis_mode,
    }

    if trend is None:
        trend = Trend(
            subject_type=subject_type,
            entity_id=entity.id if entity else None,
            topic_id=topic.id if topic else None,
            name=name,
            category=category,
            geo_scope=geo_scope,
            analysis_mode=analysis_mode,
            first_detected_at=now,
            last_evaluated_at=now,
            peak_score=result.trend_score,
            peak_score_at=now,
            state=TrendState.CANDIDATE,
        )
        session.add(trend)
        await session.flush()

    state, reason = next_state(
        current_state=trend.state,
        trend_score=result.trend_score,
        confidence=result.confidence,
        peak_score=max(trend.peak_score, result.trend_score),
        independent_sources=inp.independent_sources,
        days_since_last_observation=inp.days_since_last_observation,
        is_one_day_spike=inp.is_one_day_spike,
        is_seasonal=inp.is_seasonal,
        stage=result.stage,
    )

    trend.name = name
    trend.category = category
    trend.analysis_mode = analysis_mode
    trend.state = state
    trend.stage = result.stage
    trend.trend_score = result.trend_score
    trend.confidence = result.confidence
    if result.trend_score > trend.peak_score:
        trend.peak_score = result.trend_score
        trend.peak_score_at = now
    trend.last_evaluated_at = now
    if state in (TrendState.ACTIVE, TrendState.CONFIRMED):
        trend.last_confirmation_at = now
    trend.components = {
        c.name: {"points": c.points, "max": c.max_points, "why": c.rationale} for c in result.components
    }
    trend.penalties = result.penalties
    trend.metrics = {
        **metrics,
        "lifecycle_reason": reason,
        "stage_evidence": result.stage_evidence,
        "confidence_parts": result.confidence_parts,
        "raw_score": result.raw_score,
        "penalty_total": result.penalty_total,
    }
    trend.warnings = warnings
    trend.independent_source_count = inp.independent_sources
    trend.distinct_signal_types = inp.distinct_signal_types
    trend.observation_count = inp.observation_count
    trend.missing_observation_count = inp.missing_count
    trend.history_days = inp.history_days
    trend.is_spike = inp.is_one_day_spike
    trend.is_seasonal = inp.is_seasonal

    session.add(
        TrendSnapshot(
            trend_id=trend.id,
            evaluated_at=now,
            formula_version=TREND_FORMULA_VERSION,
            trend_score=result.trend_score,
            confidence=result.confidence,
            stage=result.stage,
            state=state,
            components={c.name: c.points for c in result.components},
            penalties=result.penalties,
            metrics={
                "growth_30d": inp.growth_30d,
                "acceleration_pp": inp.acceleration_pp,
                "independent_sources": inp.independent_sources,
                "observation_count": inp.observation_count,
            },
            warnings=warnings,
        )
    )

    seen: set[Any] = set()
    for bundle in bundles:
        seen.add(bundle.signal.id)
        link = (
            await session.execute(
                sa.select(TrendSignal).where(
                    TrendSignal.trend_id == trend.id, TrendSignal.signal_id == bundle.signal.id
                )
            )
        ).scalar_one_or_none()
        if link is None:
            link = TrendSignal(trend_id=trend.id, signal_id=bundle.signal.id, source_id=bundle.source.id)
            session.add(link)
        link.source_group = bundle.source.source_group or str(bundle.source.id)
        link.signal_type = bundle.signal.signal_type
        link.growth_30d = bundle.profile.growth_30d
        link.acceleration = bundle.profile.acceleration_pp
        link.observation_count = bundle.profile.observation_count
        link.is_proxy = bundle.signal.is_proxy
        link.contribution = round(bundle.weight, 4)

    stale = (
        (
            await session.execute(
                sa.select(TrendSignal).where(
                    TrendSignal.trend_id == trend.id, TrendSignal.signal_id.not_in(list(seen))
                )
            )
        )
        .scalars()
        .all()
    )
    for row in stale:
        await session.delete(row)

    await session.flush()
    return trend


async def evaluate_trends(
    session: AsyncSession,
    *,
    analysis_mode: str = AnalysisMode.DEMO_INCLUSIVE,
    now: datetime | None = None,
) -> list[Trend]:
    """Score every entity and topic that has enough data. Safe to run repeatedly.

    `analysis_mode` selects which evidence is readable. `live_only` evaluates the
    same subjects from live series alone and stores the result as its own trend
    row, leaving the demo-inclusive row untouched.
    """
    now = now or datetime.now(UTC)
    by_entity, excluded = await _load_series(session, analysis_mode=analysis_mode)
    trends: list[Trend] = []

    live_only = analysis_mode == AnalysisMode.LIVE_ONLY
    exclusion_note = _exclusion_note(excluded) if live_only else None

    for _entity_id, bundles in sorted(by_entity.items()):
        entity = bundles[0].entity
        if live_only and len(bundles) < MIN_LIVE_SERIES:
            continue
        inp, representative, notes = aggregate(bundles)
        if exclusion_note:
            notes.append(exclusion_note)
        headlines = await _headlines(session, {b.source.id for b in bundles})
        if headlines:
            inp.duplication_ratio = duplication_ratio(headlines)
        result = score_trend(inp)
        geo = representative.signal.geo_scope
        trends.append(
            await _upsert_trend(
                session,
                subject_type=SubjectType.ENTITY,
                entity=entity,
                topic=None,
                name=entity.canonical_name,
                category=_category_for(entity.entity_type),
                geo_scope=geo,
                inp=inp,
                result=result,
                bundles=bundles,
                extra_warnings=notes,
                analysis_mode=analysis_mode,
                now=now,
            )
        )

    # Topics aggregate the series of all their member entities.
    topics = (await session.execute(sa.select(Topic))).scalars().all()
    for topic in topics:
        member_ids = [
            str(row.entity_id)
            for row in (await session.execute(sa.select(TopicEntity).where(TopicEntity.topic_id == topic.id)))
            .scalars()
            .all()
        ]
        bundles = [b for eid in member_ids for b in by_entity.get(eid, [])]
        if len(bundles) < 2:
            continue
        inp, representative, notes = aggregate(bundles)
        if exclusion_note:
            notes.append(exclusion_note)
        headlines = await _headlines(session, {b.source.id for b in bundles})
        if headlines:
            inp.duplication_ratio = duplication_ratio(headlines)
        result = score_trend(inp)
        trends.append(
            await _upsert_trend(
                session,
                subject_type=SubjectType.TOPIC,
                entity=None,
                topic=topic,
                name=topic.label,
                category=topic.category,
                geo_scope=representative.signal.geo_scope,
                inp=inp,
                result=result,
                bundles=bundles,
                extra_warnings=notes,
                analysis_mode=analysis_mode,
                now=now,
            )
        )

    log.info(
        "trends_evaluated",
        count=len(trends),
        analysis_mode=analysis_mode,
        excluded_sources=len(excluded),
    )
    return trends


def _exclusion_note(excluded: list[ExcludedSource]) -> str | None:
    """One sentence naming what live-only mode refused, for the trend's warnings.

    Written out rather than counted, because "3 sources excluded" tells a reader
    nothing about whether the remaining evidence is worth anything.
    """
    if not excluded:
        return None
    shown = ", ".join(e.slug for e in excluded[:6])
    more = f" and {len(excluded) - 6} more" if len(excluded) > 6 else ""
    return (
        f"Live-only analysis: evidence from {len(excluded)} demo, generator or manually "
        f"imported source(s) was excluded before any figure on this page was computed "
        f"({shown}{more}). The demo-inclusive evaluation of the same subject is kept "
        "separately and reports different numbers."
    )


def _category_for(entity_type: str) -> str:
    from app.services.topics import CATEGORY_BY_ENTITY_TYPE

    return CATEGORY_BY_ENTITY_TYPE.get(entity_type, "technology")
