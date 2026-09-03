"""Watching stored opportunities change, and checking their conditions.

Two rules shape this module.

**A condition is never marked MET by judgement.** Section 16 of the brief:
*never mark a condition MET using an LLM guess.* A condition is met only when a
stored measurement satisfies a stored comparator. Anything else — no data, an
unparseable condition, a condition expressed only in prose — is `UNKNOWN`, which
is a different state from `FAILED` and is displayed differently.

**A change event is arithmetic on two stored numbers.** Nothing here decides that
something is "interesting"; it records that a number moved by a stated amount,
and leaves the decision about who should hear it to the alert layer.
"""

from __future__ import annotations

import uuid
from dataclasses import dataclass, field
from datetime import UTC, datetime, timedelta
from typing import Any

import sqlalchemy as sa
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.logging import get_logger
from app.db.base import as_utc
from app.models.enums import ChangeEventKind, ConditionCheckState, ConditionKind
from app.models.models import (
    ConditionCheck,
    Opportunity,
    OpportunityChangeEvent,
    OpportunityCondition,
    OpportunityScore,
    Signal,
    SignalObservation,
    Trend,
    TrendSignal,
)

logger = get_logger(__name__)

#: How much a score has to move before it is worth recording as an event. Below
#: this, the number is noise from re-running the pipeline, not news.
SCORE_EVENT_THRESHOLD = 5.0
CONFIDENCE_EVENT_THRESHOLD = 8.0

#: How far back a measurable condition looks for evidence, when it does not say.
DEFAULT_WINDOW_DAYS = 90

#: Ordering used only to say whether risk rose or fell. It never changes a label.
RISK_ORDER = {"low": 0, "moderate": 1, "high": 2, "very_high": 3}

COMPARATORS = {
    "gt": lambda a, b: a > b,
    "gte": lambda a, b: a >= b,
    "lt": lambda a, b: a < b,
    "lte": lambda a, b: a <= b,
    "eq": lambda a, b: a == b,
}


# --------------------------------------------------------------- conditions
@dataclass(slots=True)
class CheckOutcome:
    state: str
    reason: str
    evidence: dict[str, Any] = field(default_factory=dict)


def _describe(measurable: dict[str, Any]) -> str:
    signal = measurable.get("signal_type") or measurable.get("signal_class") or "the measure"
    comparator = measurable.get("comparator")
    value = measurable.get("value")
    if comparator and value is not None:
        words = {
            "gt": "above",
            "gte": "at or above",
            "lt": "below",
            "lte": "at or below",
            "eq": "exactly",
        }
        return f"{signal} {words.get(comparator, comparator)} {value}"
    return str(signal)


async def _observations_for(
    session: AsyncSession,
    *,
    trend_id: uuid.UUID | None,
    signal_type: str | None,
    since: datetime,
) -> list[SignalObservation]:
    """Every usable measurement of one signal type behind one trend."""
    if trend_id is None or not signal_type:
        return []
    stmt = (
        sa.select(SignalObservation)
        .join(Signal, Signal.id == SignalObservation.signal_id)
        .join(TrendSignal, TrendSignal.signal_id == Signal.id)
        .where(
            TrendSignal.trend_id == trend_id,
            Signal.signal_type == signal_type,
            SignalObservation.observed_at >= since,
            SignalObservation.status == "ok",
            SignalObservation.value.is_not(None),
        )
        .order_by(SignalObservation.observed_at.desc())
        .limit(200)
    )
    return list((await session.execute(stmt)).scalars())


async def check_condition(
    session: AsyncSession,
    condition: OpportunityCondition,
    *,
    trend_id: uuid.UUID | None,
    now: datetime | None = None,
) -> CheckOutcome:
    """Evaluate one condition against stored measurements only.

    There is no path through this function that reaches a language model, and no
    path that returns MET without a numeric comparison against a stored value.
    """
    now = now or datetime.now(UTC)
    measurable = condition.measurable or {}

    signal_type = measurable.get("signal_type")
    comparator = measurable.get("comparator")
    threshold = measurable.get("value")

    if not signal_type or comparator not in COMPARATORS or threshold is None:
        # The condition is real and worth showing; it simply is not something
        # this system can decide by arithmetic. Saying so is the honest answer.
        return CheckOutcome(
            state=ConditionCheckState.UNKNOWN,
            reason=(
                "This condition is not expressed as a measurement this system can "
                "check automatically, so it is left for a person to judge. It is "
                "not marked met or failed on a guess."
            ),
            evidence={"measurable": measurable},
        )

    window = int(measurable.get("window_days") or DEFAULT_WINDOW_DAYS)
    observations = await _observations_for(
        session,
        trend_id=trend_id,
        signal_type=signal_type,
        since=now - timedelta(days=window),
    )
    if not observations:
        return CheckOutcome(
            state=ConditionCheckState.UNKNOWN,
            reason=(
                f"No usable measurement of {signal_type} in the last {window} days, so "
                "this cannot be checked. Missing data is not a failed condition."
            ),
            evidence={"signal_type": signal_type, "window_days": window, "observations": 0},
        )

    test = COMPARATORS[comparator]
    values = [o.value for o in observations if o.value is not None]
    passing = [v for v in values if test(v, threshold)]
    latest = values[0]

    evidence = {
        "signal_type": signal_type,
        "comparator": comparator,
        "threshold": threshold,
        "latest": latest,
        "observations": len(values),
        "passing": len(passing),
        "window_days": window,
        "observed_at": as_utc(observations[0].observed_at).isoformat(),  # type: ignore[union-attr]
    }

    if test(latest, threshold):
        share = len(passing) / len(values)
        if share >= 0.6:
            return CheckOutcome(
                ConditionCheckState.MET,
                f"Met: {_describe(measurable)}. The latest reading is {latest:,.2f} and "
                f"{len(passing)} of {len(values)} readings in the window satisfy it.",
                evidence,
            )
        return CheckOutcome(
            ConditionCheckState.PARTIALLY_MET,
            f"Partly met: the latest reading of {latest:,.2f} satisfies "
            f"{_describe(measurable)}, but only {len(passing)} of {len(values)} readings "
            "in the window do, so it is not yet a settled pattern.",
            evidence,
        )
    if passing:
        return CheckOutcome(
            ConditionCheckState.PARTIALLY_MET,
            f"Partly met: {len(passing)} of {len(values)} readings satisfy "
            f"{_describe(measurable)}, but the latest, {latest:,.2f}, does not.",
            evidence,
        )
    return CheckOutcome(
        ConditionCheckState.FAILED,
        f"Not met: {_describe(measurable)} was checked against {len(values)} readings and "
        f"none satisfy it. The latest is {latest:,.2f}.",
        evidence,
    )


async def run_condition_checks(
    session: AsyncSession, *, opportunity: Opportunity, now: datetime | None = None
) -> tuple[list[ConditionCheck], list[OpportunityCondition]]:
    """Check every condition on one opportunity and record the history."""
    now = now or datetime.now(UTC)
    conditions = list(
        (
            await session.execute(
                sa.select(OpportunityCondition).where(OpportunityCondition.opportunity_id == opportunity.id)
            )
        ).scalars()
    )
    written: list[ConditionCheck] = []
    for condition in conditions:
        outcome = await check_condition(session, condition, trend_id=opportunity.primary_trend_id, now=now)
        previous = condition.state
        check = ConditionCheck(
            condition_id=condition.id,
            state=outcome.state,
            previous_state=previous,
            reason=outcome.reason,
            evidence=outcome.evidence,
            checked_at=now,
        )
        session.add(check)
        written.append(check)
        condition.state = outcome.state
        condition.checked_at = now
    await session.flush()
    return written, conditions


# ------------------------------------------------------------- change events
async def _last_snapshot(session: AsyncSession, opportunity_id: uuid.UUID) -> OpportunityScore | None:
    return (
        await session.execute(
            sa.select(OpportunityScore)
            .where(OpportunityScore.opportunity_id == opportunity_id)
            .order_by(OpportunityScore.computed_at.desc())
            .offset(1)
            .limit(1)
        )
    ).scalar_one_or_none()


def _event(
    opportunity: Opportunity,
    *,
    kind: str,
    summary: str,
    old: Any = None,
    new: Any = None,
    magnitude: float | None = None,
    detail: dict[str, Any] | None = None,
    now: datetime,
) -> OpportunityChangeEvent:
    return OpportunityChangeEvent(
        opportunity_id=opportunity.id,
        kind=kind,
        summary=summary,
        old_value=None if old is None else str(old),
        new_value=None if new is None else str(new),
        magnitude=magnitude,
        detail=detail or {},
        occurred_at=now,
    )


async def detect_changes(
    session: AsyncSession,
    *,
    opportunity: Opportunity,
    previous: dict[str, Any] | None = None,
    condition_checks: list[ConditionCheck] | None = None,
    conditions: list[OpportunityCondition] | None = None,
    now: datetime | None = None,
) -> list[OpportunityChangeEvent]:
    """Record what actually changed about one opportunity. Global, not per-user.

    A change event is a fact about the world. Whether any particular person
    should be told about it is a separate question, answered in `alerts`.
    """
    now = now or datetime.now(UTC)
    events: list[OpportunityChangeEvent] = []

    if previous is None:
        snapshot = await _last_snapshot(session, opportunity.id)
        previous = (
            {
                "opportunity_score": snapshot.adjusted_score,
                "confidence": snapshot.confidence,
                "risk_level": snapshot.risk_level,
            }
            if snapshot
            else None
        )

    if previous:
        old_score = previous.get("opportunity_score")
        if old_score is not None:
            delta = opportunity.opportunity_score - old_score
            if abs(delta) >= SCORE_EVENT_THRESHOLD:
                events.append(
                    _event(
                        opportunity,
                        kind=(ChangeEventKind.SCORE_ROSE if delta > 0 else ChangeEventKind.SCORE_FELL),
                        summary=(
                            f"The global opportunity score moved from {old_score:.0f} to "
                            f"{opportunity.opportunity_score:.0f}."
                        ),
                        old=round(old_score, 1),
                        new=round(opportunity.opportunity_score, 1),
                        magnitude=round(delta, 1),
                        now=now,
                    )
                )

        old_confidence = previous.get("confidence")
        if old_confidence is not None:
            delta = opportunity.confidence - old_confidence
            if abs(delta) >= CONFIDENCE_EVENT_THRESHOLD:
                events.append(
                    _event(
                        opportunity,
                        kind=(
                            ChangeEventKind.CONFIDENCE_ROSE if delta > 0 else ChangeEventKind.CONFIDENCE_FELL
                        ),
                        summary=(
                            f"Confidence moved from {old_confidence:.0f} to {opportunity.confidence:.0f}."
                        ),
                        old=round(old_confidence, 1),
                        new=round(opportunity.confidence, 1),
                        magnitude=round(delta, 1),
                        now=now,
                    )
                )

        old_risk = previous.get("risk_level")
        if old_risk and old_risk != opportunity.risk_level:
            rose = RISK_ORDER.get(opportunity.risk_level, 2) > RISK_ORDER.get(old_risk, 2)
            events.append(
                _event(
                    opportunity,
                    kind=ChangeEventKind.RISK_ROSE if rose else ChangeEventKind.RISK_FELL,
                    summary=(
                        f"The risk level changed from {old_risk.replace('_', ' ')} to "
                        f"{opportunity.risk_level.replace('_', ' ')}."
                    ),
                    old=old_risk,
                    new=opportunity.risk_level,
                    now=now,
                )
            )

        old_state = previous.get("state")
        if old_state and old_state != opportunity.state:
            events.append(
                _event(
                    opportunity,
                    kind=ChangeEventKind.STATE_CHANGED,
                    summary=(f"The lifecycle state changed from {old_state} to {opportunity.state}."),
                    old=old_state,
                    new=opportunity.state,
                    now=now,
                )
            )

    kinds = {c.id: c.kind for c in (conditions or [])}
    for check in condition_checks or []:
        if check.previous_state == check.state:
            continue
        # A confirmation being met and an invalidation being met are opposite
        # news, so the event kind follows the condition, not the check.
        is_invalidation = kinds.get(check.condition_id) == ConditionKind.INVALIDATION
        if check.state != ConditionCheckState.MET:
            continue
        kind = ChangeEventKind.INVALIDATION_TRIGGERED if is_invalidation else ChangeEventKind.CONFIRMATION_MET
        events.append(
            _event(
                opportunity,
                kind=kind,
                summary=check.reason,
                old=check.previous_state,
                new=check.state,
                detail={"condition_id": str(check.condition_id)},
                now=now,
            )
        )

    for event in events:
        session.add(event)
    if events:
        await session.flush()
        logger.info(
            "change_events.recorded",
            opportunity_id=str(opportunity.id),
            count=len(events),
        )
    return events


async def monitor_all(
    session: AsyncSession, *, limit: int = 500, now: datetime | None = None
) -> dict[str, int]:
    """Check conditions and detect changes across every stored opportunity."""
    now = now or datetime.now(UTC)
    opportunities = list(
        (
            await session.execute(
                sa.select(Opportunity).order_by(Opportunity.opportunity_score.desc()).limit(limit)
            )
        ).scalars()
    )
    checks = 0
    events = 0
    for opportunity in opportunities:
        written, conditions = await run_condition_checks(session, opportunity=opportunity, now=now)
        checks += len(written)
        events += len(
            await detect_changes(
                session,
                opportunity=opportunity,
                condition_checks=written,
                conditions=conditions,
                now=now,
            )
        )
    return {"opportunities": len(opportunities), "checks": checks, "events": events}


async def recent_events(
    session: AsyncSession, *, since: datetime, limit: int = 200
) -> list[tuple[OpportunityChangeEvent, Opportunity]]:
    rows = (
        await session.execute(
            sa.select(OpportunityChangeEvent, Opportunity)
            .join(Opportunity, Opportunity.id == OpportunityChangeEvent.opportunity_id)
            .where(OpportunityChangeEvent.occurred_at >= since)
            .order_by(OpportunityChangeEvent.occurred_at.desc())
            .limit(limit)
        )
    ).all()
    return [(event, opportunity) for event, opportunity in rows]


async def trend_for(session: AsyncSession, opportunity: Opportunity) -> Trend | None:
    if opportunity.primary_trend_id is None:
        return None
    return await session.get(Trend, opportunity.primary_trend_id)
