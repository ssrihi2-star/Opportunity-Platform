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

Two further rules were added when this engine got a schedule behind it, and both
are about failing honestly:

* **Isolation.** One user's relevance blowing up, or one rule raising halfway
  through, must not cost everybody else in the batch their alerts. Each unit
  runs inside its own SAVEPOINT and a failure is counted, not propagated — with
  two exceptions that are re-raised, because they mean "stop the run" rather
  than "this unit failed": a broken connection or transaction, and a supervisor
  shutdown or time-limit signal. A savepoint contains database work only. It
  cannot un-send a message that `provider.send()` already handed to Telegram or
  SMTP, so a rule that fails *after* sending leaves the user with a message and
  the database with no record of it. That window is documented, not solved here.
  It is also a PostgreSQL guarantee in production: on SQLite — the development
  default — the pysqlite driver executes SAVEPOINT outside an explicit
  transaction, so a savepoint that was *released* and whose outer transaction is
  later rolled back can leave its rows behind. A savepoint that *fails* is still
  discarded correctly there, and that is the case this isolation depends on.
* **Period identity.** A digest names the canonical period it covers
  (`daily:2026-09-06`, `weekly:2026-W36`) and that key is unique per user and
  frequency, so a retry or an overlapping run cannot write the same period
  twice. The key and the period's boundaries travel together through every
  retry.
* **Delivery order.** Alert dispatch sends and then records, which is what leaves
  the window described above. Digest delivery does the reverse: the
  `alert_deliveries` row is committed *before* `provider.send()`, affordable
  because the digest it summarises is already committed. The consequences are the
  opposite ones — an ordinary repeat run cannot send the same period twice, but a
  send interrupted between the record and the message leaves a `pending` row that
  nothing resends, since resending risks a duplicate of a message that may have
  arrived. This is best-effort delivery and **not exactly-once**: a provider can
  hand a message to Telegram and lose the response, leaving a `failed` row for a
  message the user did receive.

An alert never says what to do. It says what changed and where to look.
"""

from __future__ import annotations

import hashlib
import uuid
from collections.abc import Mapping
from dataclasses import dataclass, field
from datetime import UTC, datetime, timedelta
from typing import Any
from zoneinfo import ZoneInfo

import sqlalchemy as sa
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.config import get_settings
from app.core.errors import failure_code, is_unrecoverable
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
    #: Units (one user's relevance preparation, or one rule) that raised and were
    #: skipped so the rest of the batch could continue. Not a suppression: a
    #: suppression is a decision, this is a failure that was contained.
    failed: int = 0
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
    # Each user gets a SAVEPOINT: one person's profile raising must not cost
    # everybody else their alerts, and on PostgreSQL a failed statement aborts
    # the enclosing transaction unless a savepoint contains it.
    opportunities = {opp.id: opp for _, opp in events}
    #: Absent key = no profile stored. `None` = preparation failed. A computed
    #: dict = the relevances. The three are not interchangeable below.
    relevance_by_user: dict[uuid.UUID, dict[uuid.UUID, float] | None] = {}
    for user_id in users:
        try:
            async with session.begin_nested():
                profile = (
                    await session.execute(sa.select(UserProfile).where(UserProfile.user_id == user_id))
                ).scalar_one_or_none()
                if profile is not None:
                    context = await build_context(session, profile)
                    scored = await compute_for_user(
                        session,
                        user_id=user_id,
                        user=context,
                        opportunities=list(opportunities.values()),
                    )
                    relevance_by_user[user_id] = {k: v.relevance for k, v in scored.items()}
        except Exception as exc:  # noqa: BLE001 - one user must not stop the batch
            if is_unrecoverable(exc):
                raise
            relevance_by_user[user_id] = None
            total.failed += 1
            logger.warning("alerts.relevance_failed", outcome="user_skipped", error_type=failure_code(exc))

    for rule in rules:
        # Read the name before opening a savepoint: a rollback expires ORM state,
        # and the failure path still has to be able to say which rule it was.
        rule_name = rule.name
        user = users.get(rule.user_id)
        if user is None:
            continue

        user_relevance = relevance_by_user.get(rule.user_id, {})
        if user_relevance is None:
            # Relevance could not be prepared for this rule's owner, so the rule
            # is not evaluated at all. Continuing with a missing relevance would
            # read its `min_user_relevance` floor as "unknown" and could deliver
            # exactly what the floor was set to prevent.
            total.suppressed += 1
            total.reasons.append(
                f"'{rule_name}' was not evaluated: relevance could not be prepared for its owner."
            )
            continue

        try:
            async with session.begin_nested():
                last_fired = as_utc(rule.last_fired_at)
                if last_fired is not None:
                    quiet_until = last_fired + timedelta(hours=max(0, rule.cooldown_hours))
                    if now < quiet_until:
                        total.suppressed += 1
                        total.reasons.append(
                            f"'{rule_name}' is in its cooldown window until {quiet_until.isoformat()}."
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
                    relevance = user_relevance.get(opportunity.id)
                    ok, why = _passes_conditions(
                        rule, event=event, opportunity=opportunity, relevance=relevance
                    )
                    if not ok:
                        total.suppressed += 1
                        total.reasons.append(f"'{rule_name}' skipped {opportunity.title}: {why}.")
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
        except Exception as exc:  # noqa: BLE001 - one rule must not stop the batch
            if is_unrecoverable(exc):
                raise
            # Counted, not swallowed silently. Note what a savepoint cannot do:
            # any message `_deliver` already handed to a provider stays sent
            # while its row is rolled back with the rest of this rule's work.
            total.failed += 1
            total.reasons.append(f"'{rule_name}' failed and was skipped for this run.")
            logger.warning("alerts.rule_failed", outcome="rule_skipped", error_type=failure_code(exc))

    await session.flush()
    logger.info("alerts.dispatched", sent=total.sent, suppressed=total.suppressed, failed=total.failed)
    return total


# ------------------------------------------------------------------- digests

#: How long each period is, in days. A frequency absent here produces nothing,
#: which is how `off` stays off without a special case.
DIGEST_WINDOW_DAYS = {DigestFrequency.DAILY: 1, DigestFrequency.WEEKLY: 7}


@dataclass(slots=True, frozen=True)
class DigestPeriod:
    """One canonical period a digest covers, and the durable identity of it.

    `key` names the *period*, not the instant the task ran, so the first attempt,
    a retry after midnight and a run redelivered by the broker all produce the
    same key — and the unique index on `(user_id, frequency, period_key)` turns
    the repeat into a counted duplicate instead of a second summary the user has
    to read twice.

    `start` and `end` are the content window as well, half-open `[start, end)`.
    They travel with the key, so a retry queries the period it was scheduled for
    rather than recalculating a window around whenever it happens to run. A
    stable key over a freshly computed window would be the worst of both: the
    same identity, different contents, depending on the attempt.
    """

    frequency: str
    key: str
    start: datetime
    end: datetime
    timezone: str

    def to_dict(self) -> dict[str, str]:
        """JSON-safe form. This crosses the broker on every retry."""
        return {
            "frequency": self.frequency,
            "key": self.key,
            "start": self.start.isoformat(),
            "end": self.end.isoformat(),
            "timezone": self.timezone,
        }

    @classmethod
    def from_dict(cls, raw: Mapping[str, str]) -> DigestPeriod:
        return cls(
            frequency=str(raw["frequency"]),
            key=str(raw["key"]),
            start=datetime.fromisoformat(str(raw["start"])),
            end=datetime.fromisoformat(str(raw["end"])),
            timezone=str(raw.get("timezone") or "UTC"),
        )


def digest_period(frequency: str, *, now: datetime, timezone_name: str = "UTC") -> DigestPeriod | None:
    """The most recently *completed* canonical period, in the configured timezone.

    Completed rather than current, for two reasons. A closed period has the same
    boundaries for every attempt that writes it, so the key and the content
    window agree across retries; a period still in progress would be
    accumulating while it was being summarised, and two attempts would
    legitimately disagree about its contents. And it is what a reader means by
    "yesterday's digest": a run at 03:00 on Tuesday summarises all of Monday,
    not the three hours since midnight.

    * **daily** — the local calendar day before the run's local day,
      `[D-1 00:00, D 00:00)`, keyed `daily:<D-1>`.
    * **weekly** — the ISO week that closed before the run, Monday 00:00 to the
      next Monday 00:00, keyed `weekly:<ISO year>-W<ISO week>`. The key names a
      week, not the day the task happened to run on, so every run inside the
      following week — Monday's scheduled attempt, a retry on Tuesday, a catch-up
      run on Friday — resolves to the same key and the same boundaries, and
      collides on the unique index exactly as it should. (A run *before* that
      Monday names the week before it: on a Sunday, the most recently completed
      week is the one that closed six days earlier. That is why the default
      weekly day is Monday.)

    Local midnights are constructed as wall-clock times in `timezone_name`, so a
    DST change shifts the instant a period starts without shifting the day it
    covers; a day where midnight does not exist resolves the way `zoneinfo`
    resolves it, and the period is still exactly one calendar day or one week.
    """
    days = DIGEST_WINDOW_DAYS.get(frequency)
    if days is None:
        return None
    tz = ZoneInfo(timezone_name)
    today = as_utc(now).astimezone(tz).date()
    if days == 1:
        first = today - timedelta(days=1)
        key = f"{DigestFrequency.DAILY.value}:{first.isoformat()}"
    else:
        first = today - timedelta(days=today.weekday()) - timedelta(days=7)
        iso = first.isocalendar()
        key = f"{DigestFrequency.WEEKLY.value}:{iso.year}-W{iso.week:02d}"
    last = first + timedelta(days=days)
    return DigestPeriod(
        frequency=frequency,
        key=key,
        start=datetime(first.year, first.month, first.day, tzinfo=tz),
        end=datetime(last.year, last.month, last.day, tzinfo=tz),
        timezone=timezone_name,
    )


@dataclass(slots=True)
class DigestRunOutcome:
    """What one period's run did, counted per user.

    `duplicates` is not a failure: it means that user's digest for that period
    already exists, which is the retry path working as designed.
    """

    frequency: str
    period_key: str | None = None
    written: int = 0
    duplicates: int = 0
    failed: int = 0


async def build_digest(
    session: AsyncSession,
    *,
    user: User,
    frequency: str,
    now: datetime | None = None,
    period: DigestPeriod | None = None,
) -> Digest | None:
    """One user's summary of the period. Empty periods produce an honest empty digest.

    A digest with nothing in it is still worth sending: "nothing changed" is
    information, and silence is indistinguishable from a broken pipeline.

    With a `period`, the window and the stored identity come from it and from
    nothing else. Without one — the on-demand path, `POST /me/digests` — the
    behaviour is exactly what it always was: a rolling window ending at `now`
    and no `period_key`, which is why an on-demand digest can still be generated
    twice. Deduplicating that endpoint would change API behaviour, and the
    unique index leaves NULLs distinct on both PostgreSQL and SQLite.
    """
    now = now or datetime.now(UTC)
    days = DIGEST_WINDOW_DAYS.get(frequency)
    if days is None and period is None:
        return None
    if period is not None:
        start, end, key = period.start, period.end, period.key
    else:
        start, end, key = now - timedelta(days=days), now, None

    delivery_filters = [
        AlertDelivery.user_id == user.id,
        AlertDelivery.created_at >= start,
        AlertDelivery.channel == FALLBACK_CHANNEL,
    ]
    event_filters = [OpportunityChangeEvent.occurred_at >= start]
    if period is not None:
        # Half-open, and closed: the period has ended, so its contents are the
        # same for every attempt that writes it.
        delivery_filters.append(AlertDelivery.created_at < end)
        event_filters.append(OpportunityChangeEvent.occurred_at < end)

    deliveries = list(
        (
            await session.execute(
                sa.select(AlertDelivery)
                .where(*delivery_filters)
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
                .where(*event_filters)
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
        "period": {
            "start": start.isoformat(),
            "end": end.isoformat(),
            "frequency": frequency,
            **({"key": period.key, "timezone": period.timezone} if period else {}),
        },
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
        period_end=end,
        period_key=key,
        sections=sections,
        item_count=count,
        generated_at=now,
    )
    session.add(digest)
    await session.flush()
    return digest


async def run_digests(
    session: AsyncSession,
    *,
    frequency: str,
    now: datetime | None = None,
    period: DigestPeriod | None = None,
) -> DigestRunOutcome:
    """Generate the digest for every user who asked for this frequency.

    Each user is a SAVEPOINT, so one person's failure costs that person their
    digest and nobody else theirs, and a period already written for somebody is
    counted as a duplicate rather than raised. The caller still owns the outer
    transaction and its commit.
    """
    users = list(
        (
            await session.execute(
                sa.select(User)
                .join(UserProfile, UserProfile.user_id == User.id)
                .where(UserProfile.digest_frequency == frequency, User.is_active.is_(True))
            )
        ).scalars()
    )
    outcome = DigestRunOutcome(frequency=frequency, period_key=period.key if period else None)
    for user in users:
        try:
            async with session.begin_nested():
                digest = await build_digest(session, user=user, frequency=frequency, now=now, period=period)
        except IntegrityError:
            # The period is already written for this user: a retry, a redelivered
            # message or an overlapping run got here second. That is the index
            # doing its job, not an error.
            outcome.duplicates += 1
            continue
        except Exception as exc:  # noqa: BLE001 - one user must not stop the rest
            if is_unrecoverable(exc):
                raise
            outcome.failed += 1
            logger.warning(
                "digest.user_failed",
                outcome="user_skipped",
                frequency=frequency,
                error_type=failure_code(exc),
            )
            continue
        # Counted only once the savepoint has been released: a unit that raised
        # is a failure, never a written digest.
        if digest is not None:
            outcome.written += 1
    logger.info(
        "digests.generated",
        frequency=frequency,
        period_key=outcome.period_key,
        written=outcome.written,
        duplicates=outcome.duplicates,
        failed=outcome.failed,
    )
    return outcome


# ------------------------------------------------------- digest delivery
#: Telegram's hard limit for one message. `TelegramProvider` renders
#: `title\n\nbody\n\nlink`, so the budget has to cover all three.
TELEGRAM_TEXT_LIMIT = 4096

#: How many items one digest message names before it points at the app instead.
DIGEST_MESSAGE_ITEMS = 6

#: The only channel a digest is delivered on today. Email digests are not
#: implemented, and the UI must not imply otherwise.
DIGEST_DELIVERY_CHANNEL = "telegram"

#: Only daily digests are delivered. Weekly digests are still *generated* and
#: readable in the app; delivering them is a separate decision nobody has made.
DIGEST_DELIVERY_FREQUENCIES = frozenset({DigestFrequency.DAILY.value})


def digest_delivery_key(*, period_key: str, channel: str) -> str:
    """Stable identity for one digest delivery: the period and the channel.

    Stored in `alert_deliveries.dedupe_key`, whose unique `(user_id, dedupe_key)`
    constraint is what stops an ordinary repeated run from sending the same
    digest twice. It names the *period*, not the digest row and not the attempt,
    so a retry, a redelivered task message and a manual re-run all collide on the
    same key.
    """
    return f"digest:{period_key}:{channel}"


@dataclass(slots=True)
class DigestDeliveryOutcome:
    """What one period's delivery pass did, counted per user.

    * `already_recorded` — a delivery row exists for this period and channel
      (sent, suppressed, failed, or still pending). Nothing is sent. This MVP
      never resends an existing record, so an interrupted send is a thing an
      operator investigates, not a thing the schedule silently retries.
    * `not_eligible` — no row written: the user's preference is no longer daily,
      they have no verified Telegram chat, or the frequency is not delivered.
    """

    frequency: str
    period_key: str | None = None
    sent: int = 0
    suppressed: int = 0
    failed: int = 0
    already_recorded: int = 0
    not_eligible: int = 0
    reason_code: str | None = None

    def as_dict(self) -> dict[str, object]:
        if self.failed:
            status = "failed"
        elif self.sent or self.suppressed or self.already_recorded:
            status = "ok"
        else:
            status = "nothing_to_do"
        return {
            "status": status,
            "sent": self.sent,
            "suppressed": self.suppressed,
            "failed": self.failed,
            "already_recorded": self.already_recorded,
            "not_eligible": self.not_eligible,
            "reason_code": self.reason_code,
        }


def _fit(text: str, budget: int) -> str:
    """Trim to `budget` characters, keeping the cut visible."""
    if budget <= 1 or len(text) <= budget:
        return text[: max(0, budget)]
    return text[: max(0, budget - 1)].rstrip() + "…"


def render_digest_message(digest: Digest, *, link: str = "/for-you") -> NotificationMessage:
    """One digest as one Telegram-sized message: a summary and a way in.

    The stored `sections` are routinely larger than Telegram accepts, and a
    message that quotes everything is one nobody reads. So this renders counts,
    the first few item titles, the standing disclaimer, and a link into the app.

    The budget is measured against what the provider actually sends —
    `title`, `body` and the absolute link it builds from `APP_BASE_URL` — so the
    result is guaranteed to fit rather than hoped to.
    """
    sections = digest.sections or {}
    period = sections.get("period") or {}
    key = str(digest.period_key or period.get("key") or "")
    label = key.split(":", 1)[1] if ":" in key else digest.period_start.date().isoformat()
    title = f"Your {digest.frequency} digest — {label}"

    watched = list(sections.get("watchlist_changes") or [])
    others = list(sections.get("other_changes") or [])
    alerts_section = list(sections.get("alerts") or [])

    lines: list[str] = []
    if digest.item_count == 0:
        lines.append(str(sections.get("empty_note") or "Nothing on your watchlists changed in this period."))
    else:
        parts = [f"{digest.item_count} item{'s' if digest.item_count == 1 else 's'}"]
        if watched:
            parts.append(f"{len(watched)} on your watchlists")
        if others:
            parts.append(f"{len(others)} elsewhere")
        if alerts_section:
            parts.append(f"{len(alerts_section)} alert{'s' if len(alerts_section) == 1 else ''}")
        lines.append(" · ".join(parts) + ".")
        for item in (watched + others)[:DIGEST_MESSAGE_ITEMS]:
            kind = str(item.get("kind") or "").replace("_", " ")
            lines.append(f"• {_fit(str(item.get('title') or 'Untitled'), 80)} — {kind}")
        remaining = len(watched) + len(others) - DIGEST_MESSAGE_ITEMS
        if remaining > 0:
            lines.append(f"… and {remaining} more in the app.")

    note = str(sections.get("note") or "")
    if note:
        lines.append(note)

    base = get_settings().APP_BASE_URL.rstrip("/")
    link_text = f"{base}{link}" if link else base
    # title + "\n\n" + body + "\n\n" + link_text, which is what the provider sends.
    overhead = len(title) + len(link_text) + 4
    body = _fit("\n".join(lines), max(0, TELEGRAM_TEXT_LIMIT - overhead))
    return NotificationMessage(
        title=_fit(title, 300),  # alert_deliveries.title is VARCHAR(300)
        body=body,
        link=link,
        detail={"digest_id": str(digest.id), "period_key": digest.period_key},
    )


async def deliver_digests(
    session: AsyncSession,
    *,
    frequency: str,
    period: DigestPeriod | None = None,
    now: datetime | None = None,
) -> DigestDeliveryOutcome:
    """Send the stored digests for one period to verified Telegram chats.

    Called **after** the period's digests are committed, so delivery is a
    separate step over rows that already exist — which is also what lets a digest
    generated by an earlier run (and skipped as a duplicate this time) still be
    delivered, as long as no delivery record exists for it.

    Two properties are deliberate and worth stating exactly:

    * **The delivery record is committed before the send.** A repeated run
      therefore finds the row and does not send again. That is the opposite order
      from `_deliver`, and it is affordable here because a digest send is not
      part of the transaction that produced the digest.
    * **An existing record is never resent** — not `pending`, not `failed`. So a
      worker interrupted between the commit and the send leaves a `pending` row
      and a user with no message. That is a real gap, it is visible in
      `GET /me/alerts` and in the phase report, and closing it needs a sweeper
      that this MVP does not have. What is *not* true is that a resend can
      happen by accident: the unique key makes the ordinary repeat a no-op.

    One digest, one message, per period: the delivery key carries the period and
    the channel but not the chat, so a user who verified two Telegram chats gets
    the digest on the older binding and the second is counted `already_recorded`.

    Delivery is best-effort and **not exactly-once**: the send can fail after the
    record exists (no message, no automatic retry), and a provider that hands the
    message to Telegram and then loses the response leaves a `failed` row for a
    message the user did receive.

    Each user is isolated. A send that raises costs that user their message and
    nobody else theirs; a broken transaction or a supervisor stop signal is
    re-raised, exactly as in `dispatch` and `run_digests`.
    """
    now = now or datetime.now(UTC)
    outcome = DigestDeliveryOutcome(frequency=frequency, period_key=period.key if period else None)

    if period is None:
        # On-demand digests (`POST /me/digests`) stay a read-in-the-app feature.
        outcome.reason_code = "no_period"
        return outcome
    if frequency not in DIGEST_DELIVERY_FREQUENCIES:
        outcome.reason_code = "frequency_not_delivered"
        return outcome

    channel = DIGEST_DELIVERY_CHANNEL
    key = digest_delivery_key(period_key=period.key, channel=channel)
    rows = (
        await session.execute(
            sa.select(Digest, User, NotificationChannelLink)
            .join(User, User.id == Digest.user_id)
            .join(UserProfile, UserProfile.user_id == User.id)
            .join(
                NotificationChannelLink,
                sa.and_(
                    NotificationChannelLink.user_id == User.id,
                    NotificationChannelLink.channel == channel,
                    NotificationChannelLink.verified.is_(True),
                ),
            )
            .where(
                Digest.frequency == frequency,
                Digest.period_key == period.key,
                # The preference is read at delivery time, not at generation time:
                # a user who switched off between the two gets nothing.
                UserProfile.digest_frequency == frequency,
                User.is_active.is_(True),
            )
            # Deterministic when a user verified more than one chat: the oldest
            # binding wins, and the others are counted `already_recorded` below
            # rather than sent a second copy of the same digest.
            .order_by(Digest.created_at, NotificationChannelLink.verified_at)
        )
    ).all()

    users_with_digest = set(
        (
            await session.execute(
                sa.select(Digest.user_id).where(
                    Digest.frequency == frequency, Digest.period_key == period.key
                )
            )
        ).scalars()
    )
    for digest, user, link in rows:
        address = link.external_id
        message = render_digest_message(digest)
        delivery_id: uuid.UUID | None = None
        try:
            async with session.begin_nested():
                existing = (
                    await session.execute(
                        sa.select(AlertDelivery.id).where(
                            AlertDelivery.user_id == user.id, AlertDelivery.dedupe_key == key
                        )
                    )
                ).scalar_one_or_none()
                if existing is not None:
                    outcome.already_recorded += 1
                    continue
                delivery = AlertDelivery(
                    user_id=user.id,
                    # A digest delivery references no rule, opportunity or event,
                    # and invents none: all three columns are nullable, and the
                    # period identity lives in `dedupe_key`.
                    rule_id=None,
                    opportunity_id=None,
                    event_id=None,
                    channel=channel,
                    title=message.title,
                    body=message.body,
                    dedupe_key=key,
                    status=DeliveryStatus.PENDING,
                )
                session.add(delivery)
                await session.flush()
                delivery_id = delivery.id
            # Durable before anything leaves the machine.
            await session.commit()
        except IntegrityError:
            outcome.already_recorded += 1
            continue
        except Exception as exc:  # noqa: BLE001 - one user must not stop the rest
            if is_unrecoverable(exc):
                raise
            await session.rollback()
            outcome.failed += 1
            logger.warning(
                "digest.delivery_record_failed",
                outcome="delivery_skipped",
                frequency=frequency,
                error_type=failure_code(exc),
            )
            continue

        try:
            provider = get_provider(channel)
            if provider is None or not provider.configured:
                reason = f"No {channel} provider is configured, so the digest was not sent."
                await _mark_delivery(session, delivery_id, DeliveryStatus.SUPPRESSED, reason, None)
                outcome.suppressed += 1
                continue
            result = await provider.send(address=address, message=message)
        except Exception as exc:  # noqa: BLE001 - a failed send is recorded, not raised
            if is_unrecoverable(exc):
                raise
            await _mark_delivery(
                session,
                delivery_id,
                DeliveryStatus.FAILED,
                f"Telegram delivery raised ({failure_code(exc)}).",
                None,
            )
            outcome.failed += 1
            logger.warning(
                "digest.delivery_failed",
                outcome="delivery_failed",
                frequency=frequency,
                error_type=failure_code(exc),
            )
            continue

        if result.delivered:
            await _mark_delivery(session, delivery_id, DeliveryStatus.SENT, None, now)
            outcome.sent += 1
        else:
            await _mark_delivery(
                session, delivery_id, DeliveryStatus.SUPPRESSED, _fit(result.detail, 200), None
            )
            outcome.suppressed += 1

    # Users who asked for a daily digest and got one, but cannot be reached on
    # Telegram: counted, never silently dropped, and never sent an unverified chat.
    reachable = {user.id for _, user, _ in rows}
    outcome.not_eligible = max(0, len(users_with_digest - reachable))

    logger.info(
        "digests.delivered",
        frequency=frequency,
        period_key=outcome.period_key,
        sent=outcome.sent,
        suppressed=outcome.suppressed,
        failed=outcome.failed,
        already_recorded=outcome.already_recorded,
        not_eligible=outcome.not_eligible,
        reason_code=outcome.reason_code,
    )
    return outcome


async def _mark_delivery(
    session: AsyncSession,
    delivery_id: uuid.UUID | None,
    status: DeliveryStatus,
    reason: str | None,
    sent_at: datetime | None,
) -> None:
    """Settle a committed delivery row.

    Reloaded by primary key rather than reused from before the commit: the row
    was committed in between, and a session factory configured with
    `expire_on_commit=True` would make every attribute read on the old instance
    an implicit lazy load — which raises inside async code.
    """
    if delivery_id is None:
        return
    row = await session.get(AlertDelivery, delivery_id)
    if row is None:
        return
    row.status = status
    row.suppressed_reason = _fit(reason, 200) if reason else None
    row.sent_at = sent_at
    await session.commit()
