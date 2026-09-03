"""Deciding who hears about a change, and making sure they hear it once.

A change event is a global fact. An alert is a per-user decision about that fact,
and three things stand between the two:

* **Deduplication.** `(user_id, dedupe_key)` is unique in the database, so the
  same fact cannot reach the same person twice even if the pipeline runs ten
  times. The constraint is the guarantee; the code merely avoids the collision.
* **Cooldown.** A rule that has just fired stays quiet for its cooldown window.
  Twenty true alerts in an hour is indistinguishable from spam, and a user who
  mutes the product hears nothing at all afterwards.
* **Relevance.** A rule tied to a watchlist only fires for things on it, and a
  rule with a relevance floor only fires for things that clear it.

An alert never says what to do. It says what changed and where to look.
"""

from __future__ import annotations

import hashlib
import uuid
from dataclasses import dataclass, field
from datetime import UTC, datetime, timedelta
from typing import Any

import sqlalchemy as sa
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.logging import get_logger
from app.db.base import as_utc
from app.models.enums import AlertTrigger, ChangeEventKind, DeliveryStatus, DigestFrequency
from app.models.models import (
    AlertDelivery,
    AlertRule,
    Digest,
    NotificationChannelLink,
    Opportunity,
    OpportunityChangeEvent,
    User,
    UserProfile,
    Watchlist,
    WatchlistItem,
)
from app.notifications import NotificationMessage, get_provider
from app.notifications import providers as _providers  # noqa: F401 - registers the defaults
from app.services.profiles import build_context
from app.services.user_relevance import compute_for_user

logger = get_logger(__name__)

#: Which change events can satisfy which trigger. A trigger with no mapping here
#: fires on nothing, which is the safe direction to fail.
TRIGGER_EVENTS: dict[str, set[str]] = {
    AlertTrigger.SCORE_THRESHOLD: {ChangeEventKind.SCORE_ROSE, ChangeEventKind.SCORE_FELL},
    AlertTrigger.CONFIDENCE_THRESHOLD: {
        ChangeEventKind.CONFIDENCE_ROSE,
        ChangeEventKind.CONFIDENCE_FELL,
    },
    AlertTrigger.RISK_CHANGE: {ChangeEventKind.RISK_ROSE, ChangeEventKind.RISK_FELL},
    AlertTrigger.CONFIRMATION_MET: {ChangeEventKind.CONFIRMATION_MET},
    AlertTrigger.INVALIDATION_TRIGGERED: {ChangeEventKind.INVALIDATION_TRIGGERED},
    AlertTrigger.NEW_OPPORTUNITY: {ChangeEventKind.CREATED},
    AlertTrigger.TREND_ACCELERATION: {ChangeEventKind.TREND_STAGE_CHANGED},
    AlertTrigger.NEW_GEOGRAPHY: {ChangeEventKind.NEW_GEOGRAPHY},
    AlertTrigger.WATCHLIST_CHANGE: set(ChangeEventKind),
    AlertTrigger.COUNTRY_GAP: {ChangeEventKind.NEW_GEOGRAPHY},
}

#: The channel every user always has. Nothing can be configured away to the
#: point where an alert vanishes silently.
FALLBACK_CHANNEL = "in_app"


def dedupe_key(*, event: OpportunityChangeEvent, rule_id: uuid.UUID | None) -> str:
    """One key per (rule, event). Stable across restarts and re-runs."""
    raw = f"{rule_id or 'no-rule'}|{event.opportunity_id}|{event.kind}|{event.id}"
    return hashlib.sha256(raw.encode()).hexdigest()[:48]


@dataclass(slots=True)
class AlertOutcome:
    sent: int = 0
    suppressed: int = 0
    reasons: list[str] = field(default_factory=list)


async def _watchlist_opportunity_ids(session: AsyncSession, watchlist_id: uuid.UUID) -> set[uuid.UUID]:
    rows = (
        await session.execute(
            sa.select(WatchlistItem.opportunity_id).where(
                WatchlistItem.watchlist_id == watchlist_id,
                WatchlistItem.opportunity_id.is_not(None),
            )
        )
    ).scalars()
    return {row for row in rows if row is not None}


def _passes_conditions(
    rule: AlertRule,
    *,
    event: OpportunityChangeEvent,
    opportunity: Opportunity,
    relevance: float | None,
) -> tuple[bool, str]:
    conditions = rule.conditions or {}

    minimum = conditions.get("min_global_score")
    if minimum is not None and opportunity.opportunity_score < minimum:
        return False, f"global score {opportunity.opportunity_score:.0f} below {minimum}"

    min_relevance = conditions.get("min_user_relevance")
    if min_relevance is not None:
        if relevance is None:
            return False, "relevance has not been computed for this user yet"
        if relevance < min_relevance:
            return False, f"your relevance {relevance:.0f} below {min_relevance}"

    magnitude = conditions.get("min_magnitude")
    if magnitude is not None and abs(event.magnitude or 0.0) < magnitude:
        return False, f"the change of {event.magnitude or 0:.1f} is smaller than {magnitude}"

    wanted_type = conditions.get("opportunity_type")
    if wanted_type and opportunity.opportunity_type != wanted_type:
        return False, "a different opportunity type"

    country = conditions.get("country")
    if country and (opportunity.country or "").upper() != str(country).upper():
        return False, "a different country"

    return True, ""


async def _channels_for(session: AsyncSession, *, user: User, rule: AlertRule) -> list[tuple[str, str]]:
    """(channel, address) pairs this user can actually be reached on.

    Only *verified* external links are returned. An unverified Telegram chat is
    never written to, which is what stops one person's opportunities reaching
    another person's chat.
    """
    out: list[tuple[str, str]] = [(FALLBACK_CHANNEL, str(user.id))]
    wanted = {c for c in (rule.channels or []) if c != FALLBACK_CHANNEL}
    if not wanted:
        return out

    if "email" in wanted and user.email:
        out.append(("email", user.email))

    links = (
        await session.execute(
            sa.select(NotificationChannelLink).where(
                NotificationChannelLink.user_id == user.id,
                NotificationChannelLink.verified.is_(True),
            )
        )
    ).scalars()
    for link in links:
        if link.channel in wanted:
            out.append((link.channel, link.external_id))
    return out


async def _deliver(
    session: AsyncSession,
    *,
    user: User,
    rule: AlertRule | None,
    event: OpportunityChangeEvent,
    opportunity: Opportunity,
    key: str,
    now: datetime,
) -> AlertOutcome:
    outcome = AlertOutcome()
    message = NotificationMessage(
        title=f"{opportunity.title}: {event.kind.replace('_', ' ')}",
        body=(
            f"{event.summary}\n\n"
            f"Global opportunity score {opportunity.opportunity_score:.0f}, confidence "
            f"{opportunity.confidence:.0f}, risk {opportunity.risk_level.replace('_', ' ')}.\n"
            "This is a research note, not advice to buy, import or start anything."
        ),
        link=f"/opportunities/{opportunity.id}",
        detail={"event_kind": event.kind, "opportunity_id": str(opportunity.id)},
    )

    channels = (
        await _channels_for(session, user=user, rule=rule) if rule else [(FALLBACK_CHANNEL, str(user.id))]
    )
    for channel, address in channels:
        delivery = AlertDelivery(
            user_id=user.id,
            rule_id=rule.id if rule else None,
            opportunity_id=opportunity.id,
            event_id=event.id,
            channel=channel,
            title=message.title,
            body=message.body,
            dedupe_key=f"{key}:{channel}",
            status=DeliveryStatus.PENDING,
        )
        # A savepoint, so a duplicate rolls back only this insert. Rolling back
        # the whole transaction would discard every alert already delivered in
        # this batch, which is a far worse failure than one duplicate.
        try:
            async with session.begin_nested():
                session.add(delivery)
                await session.flush()
        except IntegrityError:
            # The unique constraint did its job: this person has already been
            # told this exact fact.
            outcome.suppressed += 1
            outcome.reasons.append("already delivered")
            continue

        provider = get_provider(channel)
        if provider is None:
            delivery.status = DeliveryStatus.SUPPRESSED
            delivery.suppressed_reason = f"No provider is registered for {channel}."
            outcome.suppressed += 1
            continue

        result = await provider.send(address=address, message=message)
        if result.delivered:
            delivery.status = DeliveryStatus.SENT
            delivery.sent_at = now
            outcome.sent += 1
        else:
            delivery.status = DeliveryStatus.SUPPRESSED
            delivery.suppressed_reason = result.detail
            outcome.suppressed += 1
            outcome.reasons.append(result.detail)
    await session.flush()
    return outcome


async def dispatch(
    session: AsyncSession,
    *,
    events: list[tuple[OpportunityChangeEvent, Opportunity]],
    now: datetime | None = None,
) -> AlertOutcome:
    """Match every event against every enabled rule, once per user per fact."""
    now = now or datetime.now(UTC)
    total = AlertOutcome()
    if not events:
        return total

    rules = list((await session.execute(sa.select(AlertRule).where(AlertRule.enabled.is_(True)))).scalars())
    if not rules:
        return total

    users = {
        user.id: user
        for user in (
            await session.execute(sa.select(User).where(User.id.in_({r.user_id for r in rules})))
        ).scalars()
    }

    # Relevance is per user, so it is computed once per user for the whole batch.
    opportunities = {opp.id: opp for _, opp in events}
    relevance_by_user: dict[uuid.UUID, dict[uuid.UUID, float]] = {}
    for user_id in users:
        profile = (
            await session.execute(sa.select(UserProfile).where(UserProfile.user_id == user_id))
        ).scalar_one_or_none()
        if profile is None:
            continue
        context = await build_context(session, profile)
        scored = await compute_for_user(
            session,
            user_id=user_id,
            user=context,
            opportunities=list(opportunities.values()),
        )
        relevance_by_user[user_id] = {k: v.relevance for k, v in scored.items()}

    for rule in rules:
        user = users.get(rule.user_id)
        if user is None:
            continue
        last_fired = as_utc(rule.last_fired_at)
        if last_fired is not None:
            quiet_until = last_fired + timedelta(hours=max(0, rule.cooldown_hours))
            if now < quiet_until:
                total.suppressed += 1
                total.reasons.append(
                    f"'{rule.name}' is in its cooldown window until {quiet_until.isoformat()}."
                )
                continue

        allowed_kinds = TRIGGER_EVENTS.get(rule.trigger, set())
        watched: set[uuid.UUID] | None = None
        if rule.watchlist_id is not None:
            watched = await _watchlist_opportunity_ids(session, rule.watchlist_id)

        fired = False
        for event, opportunity in events:
            if event.kind not in allowed_kinds:
                continue
            if watched is not None and opportunity.id not in watched:
                continue
            relevance = relevance_by_user.get(rule.user_id, {}).get(opportunity.id)
            ok, why = _passes_conditions(rule, event=event, opportunity=opportunity, relevance=relevance)
            if not ok:
                total.suppressed += 1
                total.reasons.append(f"'{rule.name}' skipped {opportunity.title}: {why}.")
                continue

            outcome = await _deliver(
                session,
                user=user,
                rule=rule,
                event=event,
                opportunity=opportunity,
                key=dedupe_key(event=event, rule_id=rule.id),
                now=now,
            )
            total.sent += outcome.sent
            total.suppressed += outcome.suppressed
            total.reasons.extend(outcome.reasons)
            fired = fired or outcome.sent > 0

        if fired:
            rule.last_fired_at = now

    await session.flush()
    logger.info("alerts.dispatched", sent=total.sent, suppressed=total.suppressed)
    return total


# ------------------------------------------------------------------- digests
DIGEST_WINDOW_DAYS = {DigestFrequency.DAILY: 1, DigestFrequency.WEEKLY: 7}


async def build_digest(
    session: AsyncSession,
    *,
    user: User,
    frequency: str,
    now: datetime | None = None,
) -> Digest | None:
    """One user's summary of the period. Empty periods produce an honest empty digest.

    A digest with nothing in it is still worth sending: "nothing changed" is
    information, and silence is indistinguishable from a broken pipeline.
    """
    now = now or datetime.now(UTC)
    days = DIGEST_WINDOW_DAYS.get(frequency)
    if days is None:
        return None
    start = now - timedelta(days=days)

    deliveries = list(
        (
            await session.execute(
                sa.select(AlertDelivery)
                .where(
                    AlertDelivery.user_id == user.id,
                    AlertDelivery.created_at >= start,
                    AlertDelivery.channel == FALLBACK_CHANNEL,
                )
                .order_by(AlertDelivery.created_at.desc())
                .limit(100)
            )
        ).scalars()
    )

    events = list(
        (
            await session.execute(
                sa.select(OpportunityChangeEvent, Opportunity)
                .join(Opportunity, Opportunity.id == OpportunityChangeEvent.opportunity_id)
                .where(OpportunityChangeEvent.occurred_at >= start)
                .order_by(OpportunityChangeEvent.occurred_at.desc())
                .limit(50)
            )
        ).all()
    )

    watchlists = list(
        (await session.execute(sa.select(Watchlist).where(Watchlist.user_id == user.id))).scalars()
    )
    watched_ids: set[uuid.UUID] = set()
    for watchlist in watchlists:
        watched_ids |= await _watchlist_opportunity_ids(session, watchlist.id)

    sections: dict[str, Any] = {
        "period": {"start": start.isoformat(), "end": now.isoformat(), "frequency": frequency},
        "alerts": [{"title": d.title, "body": d.body, "at": d.created_at.isoformat()} for d in deliveries],
        "watchlist_changes": [
            {
                "opportunity_id": str(opp.id),
                "title": opp.title,
                "kind": event.kind,
                "summary": event.summary,
            }
            for event, opp in events
            if opp.id in watched_ids
        ],
        "other_changes": [
            {
                "opportunity_id": str(opp.id),
                "title": opp.title,
                "kind": event.kind,
                "summary": event.summary,
            }
            for event, opp in events
            if opp.id not in watched_ids
        ],
        "note": (
            "This is a summary of what changed, not a recommendation. Nothing here "
            "tells you to buy, import or start anything."
        ),
    }
    count = len(sections["alerts"]) + len(events)
    if count == 0:
        sections["empty_note"] = (
            "Nothing on your watchlists changed in this period. That is a real "
            "finding, not a gap: quiet weeks are the normal case."
        )

    digest = Digest(
        user_id=user.id,
        frequency=frequency,
        period_start=start,
        period_end=now,
        sections=sections,
        item_count=count,
        generated_at=now,
    )
    session.add(digest)
    await session.flush()
    return digest


async def run_digests(session: AsyncSession, *, frequency: str, now: datetime | None = None) -> int:
    """Generate the digest for every user who asked for this frequency."""
    rows = (
        await session.execute(
            sa.select(User)
            .join(UserProfile, UserProfile.user_id == User.id)
            .where(UserProfile.digest_frequency == frequency, User.is_active.is_(True))
        )
    ).scalars()
    written = 0
    for user in rows:
        if await build_digest(session, user=user, frequency=frequency, now=now):
            written += 1
    return written
