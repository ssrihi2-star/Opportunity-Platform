"""The opportunity engine: qualifying trends in, evidence-backed candidates out.

Pipeline, deliberately in this order so expensive work never runs on cheap data:

    trends  ->  qualifying trends only  ->  gate  ->  analyzer  ->  score
            ->  skeptic  ->  risk  ->  lifecycle  ->  stored candidate

Three properties this module is built around:

* **A trend is not an opportunity.** `generate` routinely produces nothing from a
  perfectly real trend, and records why in `rejections` so the reason is legible
  instead of mysterious.
* **The same candidate is updated**, keyed on (type, trend, geography), so a
  candidate accumulates a history rather than being rediscovered daily.
* **No language model is involved in any number here.** The AI layer narrates
  what this module has already decided, and only from stored facts.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import UTC, datetime
from typing import Any

import sqlalchemy as sa
from sqlalchemy.ext.asyncio import AsyncSession

from app.analytics.analyzers import run_analyzer
from app.analytics.dedup import duplication_ratio
from app.analytics.experimental import (
    GeoObservation,
    adoption_to_attention,
    geographic_gap,
)
from app.analytics.opportunity_config import OPPORTUNITY_VERSION
from app.analytics.opportunity_scoring import (
    EvidenceFact,
    OpportunityInput,
    accessibility_verdict,
    check_gate,
    next_state,
    score_confidence,
    score_opportunity,
)
from app.analytics.risk_engine import assess_risk
from app.analytics.signal_types import class_of, is_adoption_signal
from app.analytics.skeptic import review as skeptic_review
from app.core.logging import get_logger
from app.db.base import as_utc
from app.models.enums import (
    AnalysisMode,
    ConditionKind,
    ConditionState,
    OpportunityState,
    ValidationStatus,
)
from app.models.models import (
    Entity,
    Opportunity,
    OpportunityCondition,
    OpportunityEntity,
    OpportunityParticipation,
    OpportunityRisk,
    OpportunityScore,
    OpportunitySignal,
    OpportunityTrend,
    RawRecord,
    Signal,
    SkepticReview,
    Source,
    Trend,
    TrendSignal,
)

log = get_logger("opportunities")

#: A weak candidate is normally not stored at all. The exception is a *considered*
#: negative: a strong trend that we had enough evidence to judge and found wanting.
WEAK_BUT_INFORMATIVE_TREND = 40.0
WEAK_BUT_INFORMATIVE_COMPLETENESS = 0.5

# Presentation cutoffs for direction labels (rising/flat/declining).
# Not a scored quantity — only determines how we label observed growth.
DIRECTION_RISING_THRESHOLD = 5.0
DIRECTION_DECLINING_THRESHOLD = -5.0


@dataclass(slots=True)
class Rejection:
    """A trend that did not become an opportunity, and exactly why not."""

    trend_id: str
    trend_name: str
    opportunity_type: str
    reasons: list[str]
    # Research brief fields - assembled from data already computed
    trend_score: float = 0.0
    trend_confidence: float = 0.0
    trend_state: str = "candidate"
    trend_stage: str = "weak_signal"
    observation_count: int = 0
    history_days: int = 0
    distinct_signal_types: int = 0
    independent_source_count: int = 0
    direction: str = "unknown"  # rising, flat, declining, unknown
    growth_metrics: dict[str, float] = field(default_factory=dict)
    is_spike: bool = False
    evidence_summary: list[dict[str, Any]] = field(default_factory=list)


@dataclass(slots=True)
class GenerationResult:
    created: list[Opportunity] = field(default_factory=list)
    updated: list[Opportunity] = field(default_factory=list)
    rejections: list[Rejection] = field(default_factory=list)

    @property
    def total(self) -> int:
        return len(self.created) + len(self.updated)


# ------------------------------------------------------------------ candidacy
#: Which opportunity types are even worth *considering* for a given subject.
#: Considering is not proposing: each candidate still has to pass the gate.
def candidate_types(
    trend: Trend, entity_types: set[str], signal_classes: set[str] | None = None
) -> list[str]:
    signal_classes = signal_classes or set()
    if "crypto_asset" in entity_types or "token" in entity_types:
        # A crypto asset is only ever examined as a crypto opportunity. Dressing
        # one up as a "business opportunity" is how the risk floor gets dodged.
        return ["crypto"]

    types: list[str] = []
    if "public_company" in entity_types:
        types.append("public_investment")
    # Import/distribution needs actual trade evidence. Being measured in one
    # country is not the same as being importable — that inference produced
    # nonsense candidates like "a listed company, imported".
    if "trade" in signal_classes and entity_types & {"product", "commodity"}:
        types.append("import_distribution")
    if entity_types & {
        "technology",
        "product",
        "software_project",
        "problem",
        "skill",
        "industry",
    }:
        types.append("business")
    return types


async def _facts_for(session: AsyncSession, trend: Trend) -> list[EvidenceFact]:
    """Flatten the trend's supporting series into scorer inputs."""
    rows = (
        await session.execute(
            sa.select(TrendSignal, Signal, Source)
            .join(Signal, Signal.id == TrendSignal.signal_id)
            .join(Source, Source.id == TrendSignal.source_id)
            .where(TrendSignal.trend_id == trend.id)
        )
    ).all()

    now = datetime.now(UTC)
    facts: list[EvidenceFact] = []
    for ts, signal, source in rows:
        last = as_utc(source.last_success_at) or as_utc(trend.last_evaluated_at) or now
        facts.append(
            EvidenceFact(
                signal_type=ts.signal_type,
                signal_class=class_of(ts.signal_type),
                source_group=ts.source_group or source.slug,
                source_reliability=float(source.reliability or 0.5),
                is_proxy=bool(ts.is_proxy),
                is_adoption=is_adoption_signal(ts.signal_type) and not ts.is_proxy,
                growth_30d=ts.growth_30d,
                observation_count=ts.observation_count,
                days_since_latest=max(0, (now - last).days),
                geo_scope=signal.geo_scope,
            )
        )
    return facts


async def _duplication(session: AsyncSession, trend: Trend) -> float | None:
    """How much of the supporting coverage is the same story republished."""
    source_ids = (
        (
            await session.execute(
                sa.select(TrendSignal.source_id).where(TrendSignal.trend_id == trend.id).distinct()
            )
        )
        .scalars()
        .all()
    )
    if not source_ids:
        return None
    titles = (
        (
            await session.execute(
                sa.select(RawRecord.title)
                .where(RawRecord.source_id.in_(source_ids), RawRecord.url.isnot(None))
                .limit(200)
            )
        )
        .scalars()
        .all()
    )
    clean = [t for t in titles if t]
    return duplication_ratio(clean) if len(clean) >= 3 else None


# ------------------------------------------------------------------ evaluation
async def evaluate_one(
    session: AsyncSession,
    trend: Trend,
    opportunity_type: str,
    *,
    context: dict[str, Any],
    validation_status: str,
    analysis_mode: str = AnalysisMode.DEMO_INCLUSIVE,
    now: datetime,
) -> tuple[Opportunity | None, Rejection | None]:
    """Assess one (trend, type) pair. Returns the candidate, or the reason there is none."""
    facts = await _facts_for(session, trend)

    entity_rows = (
        (
            await session.execute(
                sa.select(Entity).where(Entity.id == trend.entity_id)
                if trend.entity_id
                else sa.select(Entity).where(sa.false())
            )
        )
        .scalars()
        .all()
    )
    entity = entity_rows[0] if entity_rows else None

    inp = OpportunityInput(
        opportunity_type=opportunity_type,
        geo_scope=trend.geo_scope,
        country=None if trend.geo_scope in {"global", ""} else trend.geo_scope,
        industry=trend.category,
        trend_score=trend.trend_score,
        trend_confidence=trend.confidence,
        trend_stage=trend.stage,
        trend_state=trend.state,
        trend_is_spike=trend.is_spike,
        trend_is_seasonal=trend.is_seasonal,
        trend_history_days=trend.history_days,
        trend_observation_count=trend.observation_count,
        trend_missing_count=trend.missing_observation_count,
        facts=facts,
        market_size_band=context.get("market_size_band"),
        market_size_evidence=context.get("market_size_evidence"),
        local_penetration=context.get("local_penetration"),
        competitor_count=context.get("competitor_count"),
        awareness=context.get("awareness"),
        capital_required_usd=context.get("capital_required_usd"),
        capital_evidence=context.get("capital_evidence"),
        accessibility_barriers=list(context.get("accessibility_barriers") or []),
        defensibility_markers=list(context.get("defensibility_markers") or []),
        catalyst=context.get("catalyst"),
        catalyst_is_dated=bool(context.get("catalyst_is_dated")),
        catalyst_evidence_id=context.get("catalyst_evidence_id"),
        technical_difficulty=context.get("technical_difficulty"),
        has_commercial_data=bool(context.get("has_commercial_data")),
    )

    # 1. The gate. Most trends stop here, and that is the design.
    gate = check_gate(inp)
    if not gate.passed:
        brief = _build_rejection_brief(trend, facts, gate.reasons)
        return None, Rejection(
            trend_id=str(trend.id),
            trend_name=trend.name,
            opportunity_type=opportunity_type,
            reasons=gate.reasons,
            **brief,
        )

    # 2. The type-specific analyzer enriches and flags.
    analyzer = run_analyzer(inp, context)
    inp.flags |= analyzer.flags
    inp.evidence_kinds_present |= analyzer.evidence_kinds
    inp.assumption_count = len(analyzer.assumptions)

    # 3. Deterministic score and confidence.
    # The GLOBAL score. Nothing about any user reaches this call — see the
    # signature of score_opportunity, which no longer accepts a personal ceiling.
    score = score_opportunity(inp)
    confidence, confidence_parts = score_confidence(inp)

    # 4. Risk, with critical findings dominating and the crypto floor applied.
    duplication = await _duplication(session, trend)
    risk = assess_risk(
        opportunity_type=opportunity_type,
        hints=analyzer.risk_hints,
        flags=inp.flags,
        metrics={"trend_stage": trend.stage, "duplication_ratio": duplication},
        min_level=analyzer.min_risk_level,
    )

    # 5. The skeptic. It may only reduce confidence.
    skeptic = skeptic_review(
        inp,
        analysis=analyzer.analysis,
        missing_evidence=analyzer.missing_evidence,
        duplication_ratio=duplication,
        risk_level=risk.level,
    )
    confidence_before = confidence
    confidence = skeptic.apply(confidence)
    assert confidence <= confidence_before, "the skeptic may never raise confidence"

    # 6. Experimental measures, kept out of the score.
    ratio = adoption_to_attention(facts)
    geo = (
        geographic_gap(
            leaders=[
                GeoObservation(
                    geo=g["geo"],
                    adoption_growth=g.get("adoption_growth"),
                    attention_growth=g.get("attention_growth"),
                    supplier_count=g.get("supplier_count"),
                    competitor_count=g.get("competitor_count"),
                    unit_price=g.get("unit_price"),
                    price_currency=g.get("price_currency"),
                    first_observed_days_ago=g.get("first_observed_days_ago"),
                )
                for g in (context.get("leader_markets") or [])
            ],
            target=GeoObservation(
                geo=inp.country or "global",
                adoption_growth=context.get("local_adoption_growth"),
                attention_growth=context.get("local_attention_growth"),
                supplier_count=context.get("supplier_count"),
                competitor_count=context.get("local_competitor_count"),
                unit_price=context.get("local_unit_price"),
                price_currency=context.get("local_price_currency"),
                first_observed_days_ago=context.get("local_first_observed_days_ago"),
            ),
        )
        if context.get("leader_markets")
        else None
    )

    # 7a. No accessible way to take part. A real trend that nobody can act on is
    # not an opportunity — it is a fact about the world, and saying so plainly
    # is more useful than a low score whose reason reads as "not much here".
    #
    # This asks the scoring module for an explicit verdict rather than inferring
    # one from `points == 0`. Keying off the zero coupled this refusal to an
    # incidental early return, and when that return was removed in Phase 5 the
    # refusal silently stopped firing while every test but one still passed.
    inaccessible, why_closed = accessibility_verdict(inp)
    if inaccessible:
        reasons = [
            "The trend is real, but there is no accessible way to take part: "
            + why_closed
            + ". Recorded as a trend to watch rather than as an opportunity."
        ]
        brief = _build_rejection_brief(trend, facts, reasons)
        return None, Rejection(
            trend_id=str(trend.id),
            trend_name=trend.name,
            opportunity_type=opportunity_type,
            reasons=reasons,
            **brief,
        )

    # 7b. Floors. A candidate nobody can trust, or that is barely above nothing,
    # is not stored at all: a short list that is worth reading beats a long one.
    from app.analytics.opportunity_config import GATE

    completeness = _completeness(inp, analyzer)
    if score.opportunity_score < GATE["min_opportunity_score"]:
        # One exception, and it matters: when the underlying trend is strong AND
        # we actually had the evidence to judge the opportunity, a low score is a
        # finding in itself — "this is real, and there is still nothing good here"
        # — and hiding it would let a reader assume the trend implies the chance.
        informed_negative = (
            trend.trend_score >= WEAK_BUT_INFORMATIVE_TREND
            and completeness >= WEAK_BUT_INFORMATIVE_COMPLETENESS
        )
        if not informed_negative:
            reasons = [
                f"Opportunity score of {score.opportunity_score:.0f} is below the minimum "
                f"of {GATE['min_opportunity_score']:.0f}, and the evidence was too "
                f"incomplete ({completeness:.0%}) to call it a considered negative. "
                f"Weakest area: {_weakest(score)}."
            ]
            brief = _build_rejection_brief(trend, facts, reasons)
            return None, Rejection(
                trend_id=str(trend.id),
                trend_name=trend.name,
                opportunity_type=opportunity_type,
                reasons=reasons,
                **brief,
            )
        score.warnings.insert(
            0,
            f"The underlying trend scores {trend.trend_score:.0f}, but this particular way "
            f"of participating scores only {score.opportunity_score:.0f}. It is kept visible "
            "because a real trend with no attractive way in is worth knowing about.",
        )

    if confidence < GATE["min_confidence"]:
        reasons = [
            f"Confidence of {confidence:.0f} after the skeptic pass is below the "
            f"minimum of {GATE['min_confidence']:.0f}. "
            f"Strongest objection: {skeptic.strongest_counterargument}"
        ]
        brief = _build_rejection_brief(trend, facts, reasons)
        return None, Rejection(
            trend_id=str(trend.id),
            trend_name=trend.name,
            opportunity_type=opportunity_type,
            reasons=reasons,
            **brief,
        )

    opp = await _upsert(
        session,
        trend=trend,
        entity=entity,
        opportunity_type=opportunity_type,
        inp=inp,
        score=score,
        confidence=confidence,
        confidence_parts=confidence_parts,
        risk=risk,
        skeptic=skeptic,
        analyzer=analyzer,
        ratio=ratio,
        geo=geo,
        context=context,
        validation_status=validation_status,
        analysis_mode=analysis_mode,
        gate_metrics=gate.metrics,
        now=now,
    )
    return opp, None


def _weakest(score: Any) -> str:
    """The component furthest from its maximum, for a legible refusal message."""
    worst = min(
        score.components.items(),
        key=lambda kv: kv[1]["points"] / max(1, kv[1]["max"]),
    )
    return f"{worst[0].replace('_', ' ')} scored {worst[1]['points']:.1f}/{worst[1]['max']}"


def _build_rejection_brief(trend: Trend, facts: list[EvidenceFact], reasons: list[str]) -> dict[str, Any]:
    """Assemble research brief fields from data already computed.
    
    Returns a dict with keys matching Rejection dataclass fields.
    """
    # Derive direction from growth metrics.
    # Unknown when metrics are absent, growth_30d is missing, or its value is None.
    metrics = trend.metrics or {}
    growth_30d = metrics.get("growth_30d")
    
    # Extract all available growth metrics for display
    growth_metrics = {}
    for key in ["growth_7d", "growth_14d", "growth_30d", "growth_90d"]:
        value = metrics.get(key)
        if value is not None:
            growth_metrics[key] = value
    
    if growth_30d is None:
        direction = "unknown"
    elif growth_30d > DIRECTION_RISING_THRESHOLD:
        direction = "rising"
    elif growth_30d < DIRECTION_DECLINING_THRESHOLD:
        direction = "declining"
    else:
        direction = "flat"
    
    # Build evidence summary from facts
    evidence_summary = []
    for fact in facts:
        evidence_summary.append({
            "signal_type": fact.signal_type,
            "signal_class": fact.signal_class,
            "source_group": fact.source_group,
            "observation_count": fact.observation_count,
            "growth_30d": fact.growth_30d,
        })
    
    return {
        "trend_score": trend.trend_score,
        "trend_confidence": trend.confidence,
        "trend_state": trend.state,
        "trend_stage": trend.stage,
        "observation_count": trend.observation_count,
        "history_days": trend.history_days,
        "distinct_signal_types": trend.distinct_signal_types,
        "independent_source_count": trend.independent_source_count,
        "direction": direction,
        "growth_metrics": growth_metrics,
        "is_spike": trend.is_spike,
        "evidence_summary": evidence_summary,
    }


def _completeness(inp: OpportunityInput, analyzer: Any) -> float:
    """Share of the required evidence that is actually present."""
    present = len(inp.evidence_kinds_present)
    absent = len(analyzer.missing_evidence)
    return round(present / max(1, present + absent), 3)


async def _upsert(  # noqa: PLR0913 - this is the assembly point; splitting it hides the shape
    session: AsyncSession,
    *,
    trend: Trend,
    entity: Entity | None,
    opportunity_type: str,
    inp: OpportunityInput,
    score: Any,
    confidence: float,
    confidence_parts: dict[str, float],
    risk: Any,
    skeptic: Any,
    analyzer: Any,
    ratio: Any,
    geo: Any,
    context: dict[str, Any],
    validation_status: str,
    analysis_mode: str,
    gate_metrics: dict[str, Any],
    now: datetime,
) -> Opportunity:
    """Create or update the candidate for (type, trend, geography)."""
    existing = (
        await session.execute(
            sa.select(Opportunity).where(
                Opportunity.opportunity_type == opportunity_type,
                Opportunity.primary_trend_id == trend.id,
                Opportunity.geo_scope == trend.geo_scope,
            )
        )
    ).scalar_one_or_none()

    title = context.get("title") or f"{trend.name} — {opportunity_type.replace('_', ' ')}"
    slug = context.get("slug") or f"{opportunity_type}-{trend.id}"

    peak = max(existing.peak_score if existing else 0.0, score.opportunity_score)
    days_since = min((f.days_since_latest for f in inp.facts), default=0)
    state, state_reason = next_state(
        current_state=existing.state if existing else None,
        opportunity_score=score.opportunity_score,
        confidence=confidence,
        peak_score=peak,
        risk_level=risk.level,
        skeptic_status=skeptic.status,
        days_since_evidence=days_since,
    )

    warnings = list(score.warnings)
    if risk.floor_applied:
        warnings.append(f"Risk level is floored at {risk.floor_applied} for this asset class.")
    warnings.append(state_reason)

    fields: dict[str, Any] = {
        "title": title,
        "summary": context.get("summary"),
        "category": trend.category or "technology",
        "opportunity_type": opportunity_type,
        "geo_scope": trend.geo_scope,
        "country": inp.country,
        "industry": trend.category,
        "state": state,
        "validation_status": validation_status,
        "analysis_mode": analysis_mode,
        "maturity_stage": trend.stage,
        "risk_level": risk.level,
        "opportunity_score": score.opportunity_score,
        "adjusted_score": score.opportunity_score,
        "raw_score": score.raw_score,
        "penalty_total": score.penalty_total,
        "confidence": confidence,
        "components": score.components,
        "penalties": score.penalties,
        "confidence_parts": confidence_parts,
        "metrics": {**score.metrics, **gate_metrics, "duplication_checked": True},
        "warnings": warnings,
        "thesis": context.get("thesis"),
        "counter_thesis": context.get("counter_thesis") or skeptic.strongest_counterargument,
        "mechanism": context.get("mechanism"),
        "why_early": analyzer.why_early,
        "missing_evidence": analyzer.missing_evidence,
        "next_research_steps": context.get("next_research_steps") or [],
        "analysis": analyzer.analysis,
        "independent_source_count": gate_metrics.get("independent_sources", 0),
        "distinct_signal_types": gate_metrics.get("signal_types", 0),
        "evidence_count": len(inp.facts),
        "algorithm_version": OPPORTUNITY_VERSION,
        "last_evaluated_at": now,
        "peak_score": peak,
        "capital_required_usd": inp.capital_required_usd,
        "geographic_gap": geo.gap if geo else None,
        "geographic_gap_parts": (
            {"parts": geo.parts, "notes": geo.notes, "caveats": geo.caveats, "version": geo.version}
            if geo
            else {}
        ),
        "adoption_attention_ratio": ratio.ratio,
        "adoption_attention_parts": {
            "adoption_growth": ratio.adoption_growth,
            "attention_growth": ratio.attention_growth,
            "reading": ratio.reading,
            "version": ratio.version,
            **ratio.parts,
        },
    }

    if existing is None:
        opp = Opportunity(slug=slug, primary_trend_id=trend.id, detected_at=now, **fields)
        session.add(opp)
        await session.flush()
    else:
        opp = existing
        for key, value in fields.items():
            setattr(opp, key, value)
        await session.flush()

    # ---- immutable score snapshot ------------------------------------------
    session.add(
        OpportunityScore(
            opportunity_id=opp.id,
            formula_version=OPPORTUNITY_VERSION,
            components=score.components,
            penalties=score.penalties,
            raw_score=score.raw_score,
            penalty_total=score.penalty_total,
            adjusted_score=score.opportunity_score,
            confidence=confidence,
            evidence_completeness=_completeness(inp, analyzer),
            risk_level=risk.level,
            computed_at=now,
        )
    )

    # ---- skeptic review (immutable; a new row per pass) --------------------
    session.add(
        SkepticReview(
            opportunity_id=opp.id,
            counterarguments=skeptic.counterarguments,
            risk_flags=skeptic.red_flags,
            missing_evidence=skeptic.missing_evidence,
            alternative_explanations=skeptic.alternative_explanations,
            invalidation_conditions=[c["description"] for c in analyzer.invalidation],
            manipulation_probability=skeptic.manipulation_probability,
            confidence_reduction=skeptic.confidence_reduction,
            status=skeptic.status,
            strongest_counterargument=skeptic.strongest_counterargument,
            too_late_reasons=skeptic.too_late_reasons,
            inaccessible_reasons=skeptic.inaccessible_reasons,
            questions_asked=skeptic.questions_asked,
            prompt_version=skeptic.version,
            reviewed_at=now,
        )
    )

    # ---- risks, conditions, participation: replaced wholesale each run -----
    await session.execute(sa.delete(OpportunityRisk).where(OpportunityRisk.opportunity_id == opp.id))
    for r in risk.risks:
        session.add(
            OpportunityRisk(
                opportunity_id=opp.id,
                code=r.code,
                category=r.category,
                severity=r.severity,
                rationale=r.rationale,
                evidence_item_ids=r.evidence_ids,
                confidence=r.confidence,
                mitigation=r.mitigation,
                is_blocking=r.is_blocking,
            )
        )

    await session.execute(
        sa.delete(OpportunityCondition).where(OpportunityCondition.opportunity_id == opp.id)
    )
    for cond in analyzer.confirmation:
        session.add(
            OpportunityCondition(
                opportunity_id=opp.id,
                kind=ConditionKind.CONFIRMATION,
                description=cond["description"],
                measurable=cond.get("measurable") or {},
                state=ConditionState.PENDING,
            )
        )
    for cond in analyzer.invalidation:
        session.add(
            OpportunityCondition(
                opportunity_id=opp.id,
                kind=ConditionKind.INVALIDATION,
                description=cond["description"],
                measurable=cond.get("measurable") or {},
                state=ConditionState.PENDING,
            )
        )

    await session.execute(
        sa.delete(OpportunityParticipation).where(OpportunityParticipation.opportunity_id == opp.id)
    )
    for path in analyzer.participation:
        session.add(
            OpportunityParticipation(
                opportunity_id=opp.id,
                kind=path["kind"],
                description=path["description"],
                difficulty=path.get("difficulty", "unknown"),
                capital_hint=path.get("capital_hint"),
            )
        )

    # ---- links --------------------------------------------------------------
    link = (
        await session.execute(
            sa.select(OpportunityTrend).where(
                OpportunityTrend.opportunity_id == opp.id,
                OpportunityTrend.trend_id == trend.id,
            )
        )
    ).scalar_one_or_none()
    if link is None:
        session.add(OpportunityTrend(opportunity_id=opp.id, trend_id=trend.id, role="primary"))

    if entity is not None:
        has = (
            await session.execute(
                sa.select(OpportunityEntity).where(
                    OpportunityEntity.opportunity_id == opp.id,
                    OpportunityEntity.entity_id == entity.id,
                )
            )
        ).scalar_one_or_none()
        if has is None:
            session.add(OpportunityEntity(opportunity_id=opp.id, entity_id=entity.id))

    signal_ids = (
        (await session.execute(sa.select(TrendSignal.signal_id).where(TrendSignal.trend_id == trend.id)))
        .scalars()
        .all()
    )
    existing_sigs = set(
        (
            await session.execute(
                sa.select(OpportunitySignal.signal_id).where(OpportunitySignal.opportunity_id == opp.id)
            )
        )
        .scalars()
        .all()
    )
    for sid in signal_ids:
        if sid not in existing_sigs:
            session.add(OpportunitySignal(opportunity_id=opp.id, signal_id=sid, contribution=1.0))

    await session.flush()
    return opp


# ------------------------------------------------------------------ entrypoint
async def generate_opportunities(
    session: AsyncSession,
    *,
    contexts: dict[str, dict[str, Any]] | None = None,
    validation_status: str = ValidationStatus.DEMO,
    analysis_mode: str = AnalysisMode.DEMO_INCLUSIVE,
    now: datetime | None = None,
) -> GenerationResult:
    """Run the whole pipeline over every qualifying trend. Safe to run repeatedly.

    `contexts` supplies the analyzer facts that cannot be derived from a time
    series — supplier counts, filings, token distribution — keyed by
    "<trend name>|<opportunity type>" or just "<trend name>". Anything absent
    stays UNKNOWN, and UNKNOWN never earns points.

    `analysis_mode` picks which trends are read. It is a *hard* filter, not a
    preference: in `live_only` the generator only ever sees trends that were
    themselves computed from live evidence, so every fact, corroboration count
    and duplication ratio underneath a live-only candidate is live. In that mode
    the scenario `contexts` are also refused — they are hand-written demo facts,
    and letting them in through the analyzer would put demo evidence back into a
    live-only result by the back door.
    """
    now = now or datetime.now(UTC)
    contexts = contexts or {}
    live_only = analysis_mode == AnalysisMode.LIVE_ONLY
    if live_only:
        contexts = {}
    result = GenerationResult()

    trends = (
        (
            await session.execute(
                sa.select(Trend)
                .where(Trend.analysis_mode == analysis_mode)
                .order_by(Trend.trend_score.desc())
            )
        )
        .scalars()
        .all()
    )

    for trend in trends:
        entity_types: set[str] = set()
        if trend.entity_id:
            ent = (
                await session.execute(sa.select(Entity).where(Entity.id == trend.entity_id))
            ).scalar_one_or_none()
            if ent:
                entity_types.add(ent.entity_type)

        classes = {
            class_of(st)
            for st in (
                await session.execute(
                    sa.select(TrendSignal.signal_type).where(TrendSignal.trend_id == trend.id)
                )
            )
            .scalars()
            .all()
        }
        for opportunity_type in candidate_types(trend, entity_types, classes):
            key = f"{trend.name}|{opportunity_type}"
            context = contexts.get(key) or contexts.get(trend.name) or {}
            existed = (
                await session.execute(
                    sa.select(Opportunity.id).where(
                        Opportunity.opportunity_type == opportunity_type,
                        Opportunity.primary_trend_id == trend.id,
                        Opportunity.geo_scope == trend.geo_scope,
                    )
                )
            ).first() is not None

            opp, rejection = await evaluate_one(
                session,
                trend,
                opportunity_type,
                context=context,
                validation_status=validation_status,
                analysis_mode=analysis_mode,
                now=now,
            )
            if opp is not None:
                (result.updated if existed else result.created).append(opp)
            elif rejection is not None:
                result.rejections.append(rejection)

    log.info(
        "opportunities_generated",
        created=len(result.created),
        updated=len(result.updated),
        rejected=len(result.rejections),
        version=OPPORTUNITY_VERSION,
        analysis_mode=analysis_mode,
    )
    return result


async def archive_stale(session: AsyncSession, *, now: datetime | None = None) -> int:
    """Move candidates whose evidence has gone quiet out of the active list."""
    now = now or datetime.now(UTC)
    opps = (
        (
            await session.execute(
                sa.select(Opportunity).where(
                    Opportunity.state.notin_([OpportunityState.ARCHIVED, OpportunityState.INVALIDATED])
                )
            )
        )
        .scalars()
        .all()
    )
    moved = 0
    for opp in opps:
        last = as_utc(opp.last_evaluated_at)
        if last and (now - last).days > 120:
            opp.state = OpportunityState.ARCHIVED
            moved += 1
    return moved
