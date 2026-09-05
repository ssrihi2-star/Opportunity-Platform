"""Everything that belongs to one person: profile, watchlists, alerts, feed.

One rule governs this whole module, and it is section 25 of the brief:

    User A must never access User B's watchlists, notes, decisions, capital,
    profile, alerts.

The enforcement is structural rather than careful. Every query in this file is
filtered on `user.id` taken from the verified token, never from the path or the
body; nothing accepts a user id as input; and the helpers that load a watchlist
or a rule by id refuse to return one that belongs to somebody else, answering
404 rather than 403 so the existence of another person's row is not confirmed.
"""

from __future__ import annotations

import uuid
from dataclasses import asdict
from datetime import UTC, datetime, timedelta

import sqlalchemy as sa
from fastapi import APIRouter, Depends, HTTPException, Query, status
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.deps import current_user, db_session
from app.api.v1 import telegram
from app.models.enums import DigestFrequency
from app.models.models import (
    AlertDelivery,
    AlertRule,
    Digest,
    Entity,
    NotificationChannelLink,
    Opportunity,
    Trend,
    User,
    UserOpportunityFeedback,
    UserProfile,
    Watchlist,
    WatchlistItem,
)
from app.schemas.common import Page
from app.schemas.opportunities import OpportunityOut
from app.schemas.personal import (
    AlertDeliveryOut,
    AlertRuleIn,
    AlertRuleOut,
    ChannelLinkIn,
    ChannelLinkOut,
    ChannelVerifyIn,
    DigestOut,
    FeedbackIn,
    FeedbackOut,
    MoneyOut,
    ProfileIn,
    ProfileOut,
    WatchlistIn,
    WatchlistItemIn,
    WatchlistItemOut,
    WatchlistOut,
)
from app.services.alerts import build_digest
from app.services.profiles import build_context, convert, ensure_profile
from app.services.user_relevance import compute_for_user

router = APIRouter(tags=["me"])

#: The profile fields that actually move the relevance score, for the
#: completeness meter. Weighted by how much difference each one makes.
PROFILE_WEIGHTS: dict[str, float] = {
    "residence_country": 1.5,
    "operating_countries": 1.5,
    "interest_ranking": 1.5,
    "max_capital": 1.5,
    "skills": 1.5,
    "experience_industries": 1.0,
    "assets": 1.0,
    "risk_tolerance": 0.5,
    "time_commitment": 0.5,
    "target_countries": 1.0,
    "industries": 0.5,
    "home_country": 0.5,
}

DISCOVERY_CAP = 500


# ------------------------------------------------------------------ profile
def _filled(profile: UserProfile, field: str) -> bool:
    value = getattr(profile, field, None)
    if isinstance(value, list):
        return bool(value)
    if field in {"risk_tolerance", "time_commitment"}:
        # These have defaults, so "filled" means the user has a profile at all.
        return bool(value)
    return value not in (None, "")


def _completeness(profile: UserProfile) -> tuple[float, list[str]]:
    total = sum(PROFILE_WEIGHTS.values())
    earned = sum(w for f, w in PROFILE_WEIGHTS.items() if _filled(profile, f))
    missing = sorted(f for f in PROFILE_WEIGHTS if not _filled(profile, f))
    return round(100.0 * earned / total, 1), missing


async def _profile_out(session: AsyncSession, profile: UserProfile) -> ProfileOut:
    out = ProfileOut.model_validate(profile)
    money = await convert(session, amount=profile.max_capital, currency=profile.capital_currency, to="USD")
    if money is not None:
        out.capital_in_usd = MoneyOut(**asdict(money))
    out.completeness, out.missing = _completeness(profile)
    return out


@router.get("/me/profile", response_model=ProfileOut)
async def read_profile(
    session: AsyncSession = Depends(db_session),
    user: User = Depends(current_user),
) -> ProfileOut:
    profile = await ensure_profile(session, user)
    await session.commit()
    return await _profile_out(session, profile)


@router.put("/me/profile", response_model=ProfileOut)
async def update_profile(
    body: ProfileIn,
    session: AsyncSession = Depends(db_session),
    user: User = Depends(current_user),
) -> ProfileOut:
    """Change your own profile, and only your own.

    Changing the profile changes relevance everywhere immediately, because
    relevance is recomputed from the row rather than baked into stored numbers.
    """
    profile = await ensure_profile(session, user)
    changes = body.model_dump(exclude_unset=True, exclude_none=True)
    for field, value in changes.items():
        if field.endswith("_countries") or field in {"home_country", "residence_country"}:
            value = [str(v).upper() for v in value] if isinstance(value, list) else str(value).upper()
        if field in {"capital_currency", "display_currency"}:
            value = str(value).upper()
        setattr(profile, field, value)
    await session.flush()

    # Relevance is stale the moment the profile changes, so it is rebuilt now
    # rather than left to disagree with what the user just told us.
    context = await build_context(session, profile)
    opportunities = list(
        (
            await session.execute(
                sa.select(Opportunity).order_by(Opportunity.opportunity_score.desc()).limit(DISCOVERY_CAP)
            )
        ).scalars()
    )
    await compute_for_user(session, user_id=user.id, user=context, opportunities=opportunities)
    await session.commit()
    return await _profile_out(session, profile)


# ---------------------------------------------------------------- discovery
async def _feed(
    session: AsyncSession,
    *,
    user: User,
    personalised: bool,
    include_outside: bool,
    limit: int,
    offset: int,
    min_global_score: float | None,
    min_relevance: float | None,
) -> Page[OpportunityOut]:
    profile = await ensure_profile(session, user)
    context = await build_context(session, profile)

    stmt = sa.select(Opportunity).order_by(Opportunity.opportunity_score.desc())
    if min_global_score is not None:
        stmt = stmt.where(Opportunity.opportunity_score >= min_global_score)
    candidates = list((await session.execute(stmt.limit(DISCOVERY_CAP))).scalars())

    results = await compute_for_user(session, user_id=user.id, user=context, opportunities=candidates)
    await session.commit()

    rows = candidates
    if personalised:
        if not include_outside and not profile.show_outside_profile:
            # Flagged, never silently dropped — this only happens when the user
            # has explicitly turned the escape hatch off.
            rows = [o for o in rows if not results[o.id].outside_profile]
        if min_relevance is not None:
            rows = [o for o in rows if results[o.id].relevance >= min_relevance]
        rows = sorted(rows, key=lambda o: results[o.id].relevance, reverse=True)

    page = rows[offset : offset + limit]
    trends = {
        t.id: t
        for t in (
            await session.execute(
                sa.select(Trend).where(Trend.id.in_([o.primary_trend_id for o in page if o.primary_trend_id]))
            )
        ).scalars()
    }

    items: list[OpportunityOut] = []
    for opp in page:
        out = OpportunityOut.model_validate(opp)
        trend = trends.get(opp.primary_trend_id)
        if trend is not None:
            out.trend_id = trend.id
            out.trend_score = trend.trend_score
            out.trend_confidence = trend.confidence
            out.trend_stage = trend.stage
            out.trend_name = trend.name
        result = results.get(opp.id)
        if result is not None:
            out.user_relevance = result.relevance
            out.best_path = result.best_path
            out.outside_profile = result.outside_profile
            out.relevance_version = result.version
        items.append(out)
    return Page(items=items, total=len(rows), limit=limit, offset=offset)


@router.get("/discover", response_model=Page[OpportunityOut])
async def discover(
    min_global_score: float | None = Query(default=None, ge=0, le=100),
    limit: int = Query(default=50, ge=1, le=200),
    offset: int = Query(default=0, ge=0),
    session: AsyncSession = Depends(db_session),
    user: User = Depends(current_user),
) -> Page[OpportunityOut]:
    """The global feed, ordered by the GLOBAL score, identical for everyone.

    Your relevance is attached to each row so you can see how it fits you, but it
    changes nothing about the order or the contents. Two people asking for this
    on the same day get the same opportunities in the same order.
    """
    return await _feed(
        session,
        user=user,
        personalised=False,
        include_outside=True,
        limit=limit,
        offset=offset,
        min_global_score=min_global_score,
        min_relevance=None,
    )


@router.get("/for-you", response_model=Page[OpportunityOut])
async def for_you(
    min_relevance: float | None = Query(default=None, ge=0, le=100),
    include_outside_profile: bool = Query(
        default=True,
        description=(
            "Keep opportunities that fall outside your stated filters, flagged as such. "
            "Turning this off narrows the feed to what you asked for and can hide things "
            "worth seeing."
        ),
    ),
    limit: int = Query(default=50, ge=1, le=200),
    offset: int = Query(default=0, ge=0),
    session: AsyncSession = Depends(db_session),
    user: User = Depends(current_user),
) -> Page[OpportunityOut]:
    """Your feed, ordered by YOUR relevance — with the global score still shown.

    Both numbers travel together on every row and neither is ever folded into the
    other. A global 91 that is a 12 for you stays visible and stays a 91.
    """
    return await _feed(
        session,
        user=user,
        personalised=True,
        include_outside=include_outside_profile,
        limit=limit,
        offset=offset,
        min_global_score=None,
        min_relevance=min_relevance,
    )


# --------------------------------------------------------------- watchlists
async def _own_watchlist(session: AsyncSession, user: User, watchlist_id: uuid.UUID) -> Watchlist:
    """Load a watchlist, or 404. Another user's id is indistinguishable from a typo."""
    watchlist = (
        await session.execute(
            sa.select(Watchlist).where(Watchlist.id == watchlist_id, Watchlist.user_id == user.id)
        )
    ).scalar_one_or_none()
    if watchlist is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "No such watchlist.")
    return watchlist


async def _watchlist_out(session: AsyncSession, watchlist: Watchlist) -> WatchlistOut:
    items = list(
        (
            await session.execute(
                sa.select(WatchlistItem)
                .where(WatchlistItem.watchlist_id == watchlist.id)
                .order_by(WatchlistItem.created_at)
            )
        ).scalars()
    )
    # Built field by field rather than from the ORM object: `Watchlist.items` is a
    # lazy relationship, and letting the serialiser touch it triggers IO from
    # inside the response model, which async SQLAlchemy refuses.
    return WatchlistOut(
        id=watchlist.id,
        name=watchlist.name,
        description=watchlist.description,
        min_score=watchlist.min_score,
        max_risk_level=watchlist.max_risk_level,
        created_at=watchlist.created_at,
        items=[WatchlistItemOut.model_validate(i) for i in items],
    )


@router.get("/me/watchlists", response_model=list[WatchlistOut])
async def list_watchlists(
    session: AsyncSession = Depends(db_session),
    user: User = Depends(current_user),
) -> list[WatchlistOut]:
    rows = (
        await session.execute(
            sa.select(Watchlist).where(Watchlist.user_id == user.id).order_by(Watchlist.name)
        )
    ).scalars()
    return [await _watchlist_out(session, w) for w in rows]


@router.post("/me/watchlists", response_model=WatchlistOut, status_code=status.HTTP_201_CREATED)
async def create_watchlist(
    body: WatchlistIn,
    session: AsyncSession = Depends(db_session),
    user: User = Depends(current_user),
) -> WatchlistOut:
    watchlist = Watchlist(user_id=user.id, **body.model_dump())
    session.add(watchlist)
    await session.commit()
    await session.refresh(watchlist)
    return await _watchlist_out(session, watchlist)


@router.put("/me/watchlists/{watchlist_id}", response_model=WatchlistOut)
async def update_watchlist(
    watchlist_id: uuid.UUID,
    body: WatchlistIn,
    session: AsyncSession = Depends(db_session),
    user: User = Depends(current_user),
) -> WatchlistOut:
    watchlist = await _own_watchlist(session, user, watchlist_id)
    for field, value in body.model_dump(exclude_unset=True).items():
        setattr(watchlist, field, value)
    await session.commit()
    return await _watchlist_out(session, watchlist)


@router.delete("/me/watchlists/{watchlist_id}", status_code=status.HTTP_204_NO_CONTENT)
async def delete_watchlist(
    watchlist_id: uuid.UUID,
    session: AsyncSession = Depends(db_session),
    user: User = Depends(current_user),
) -> None:
    watchlist = await _own_watchlist(session, user, watchlist_id)
    await session.delete(watchlist)
    await session.commit()


async def _reject_dangling_references(session: AsyncSession, data: dict[str, object]) -> None:
    """Refuse ids that point at nothing. Only the ids actually supplied are checked.

    The three id columns are real foreign keys, but nothing above this point has
    ever looked at what they point to, and the two engines disagree about what
    that means: PostgreSQL raises a violation the app does not handle and answers
    500, while SQLite does not enforce foreign keys at all under the test suite
    and quietly stores a row that references a nonexistent thing. A dangling row
    is the worse outcome of the two, because it survives to be read back later.

    Validating here fixes both at once and gives the same answer on either engine.
    Catching IntegrityError instead would only address the PostgreSQL half, and
    could not be tested by a suite that never raises it.
    """
    for field, model, message in (
        ("opportunity_id", Opportunity, "No such opportunity."),
        ("trend_id", Trend, "No such trend."),
        ("entity_id", Entity, "No such entity."),
    ):
        value = data.get(field)
        if value is None:
            continue
        # One `select 1 ... limit 1` per supplied id, and none at all when the
        # item is a country, an industry or a keyword: those reference no row.
        exists = (
            await session.execute(sa.select(sa.literal(1)).where(model.id == value).limit(1))
        ).scalar_one_or_none()
        if exists is None:
            raise HTTPException(status.HTTP_422_UNPROCESSABLE_ENTITY, message)


@router.post(
    "/me/watchlists/{watchlist_id}/items",
    response_model=WatchlistItemOut,
    status_code=status.HTTP_201_CREATED,
)
async def add_watchlist_item(
    watchlist_id: uuid.UUID,
    body: WatchlistItemIn,
    session: AsyncSession = Depends(db_session),
    user: User = Depends(current_user),
) -> WatchlistItemOut:
    """Follow anything: an opportunity, a trend, a company, a country, an industry.

    A country or an industry is as followable as a company, because "tell me when
    something starts happening in Kenya" is a real question.
    """
    # Ownership first, and deliberately so: someone probing another user's list
    # must get the same 404 whatever they put in the body. Validating the ids
    # first would answer 422 for a well-formed id and 404 otherwise, and that
    # difference tells an outsider the watchlist exists.
    watchlist = await _own_watchlist(session, user, watchlist_id)
    data = body.model_dump()
    await _reject_dangling_references(session, data)
    if data.get("country_code"):
        data["country_code"] = data["country_code"].upper()
    item = WatchlistItem(watchlist_id=watchlist.id, **data)
    session.add(item)
    await session.commit()
    await session.refresh(item)
    return WatchlistItemOut.model_validate(item)


@router.delete("/me/watchlists/{watchlist_id}/items/{item_id}", status_code=status.HTTP_204_NO_CONTENT)
async def remove_watchlist_item(
    watchlist_id: uuid.UUID,
    item_id: uuid.UUID,
    session: AsyncSession = Depends(db_session),
    user: User = Depends(current_user),
) -> None:
    await _own_watchlist(session, user, watchlist_id)
    item = (
        await session.execute(
            sa.select(WatchlistItem).where(
                WatchlistItem.id == item_id, WatchlistItem.watchlist_id == watchlist_id
            )
        )
    ).scalar_one_or_none()
    if item is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "No such item.")
    await session.delete(item)
    await session.commit()


# ------------------------------------------------------------------- alerts
@router.get("/me/alert-rules", response_model=list[AlertRuleOut])
async def list_alert_rules(
    session: AsyncSession = Depends(db_session),
    user: User = Depends(current_user),
) -> list[AlertRuleOut]:
    rows = (await session.execute(sa.select(AlertRule).where(AlertRule.user_id == user.id))).scalars()
    return [AlertRuleOut.model_validate(r) for r in rows]


@router.post("/me/alert-rules", response_model=AlertRuleOut, status_code=status.HTTP_201_CREATED)
async def create_alert_rule(
    body: AlertRuleIn,
    session: AsyncSession = Depends(db_session),
    user: User = Depends(current_user),
) -> AlertRuleOut:
    if body.watchlist_id is not None:
        await _own_watchlist(session, user, body.watchlist_id)
    rule = AlertRule(user_id=user.id, **body.model_dump())
    session.add(rule)
    await session.commit()
    await session.refresh(rule)
    return AlertRuleOut.model_validate(rule)


@router.delete("/me/alert-rules/{rule_id}", status_code=status.HTTP_204_NO_CONTENT)
async def delete_alert_rule(
    rule_id: uuid.UUID,
    session: AsyncSession = Depends(db_session),
    user: User = Depends(current_user),
) -> None:
    rule = (
        await session.execute(
            sa.select(AlertRule).where(AlertRule.id == rule_id, AlertRule.user_id == user.id)
        )
    ).scalar_one_or_none()
    if rule is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "No such alert rule.")
    await session.delete(rule)
    await session.commit()


@router.get("/me/alerts", response_model=list[AlertDeliveryOut])
async def list_alerts(
    limit: int = Query(default=50, ge=1, le=200),
    session: AsyncSession = Depends(db_session),
    user: User = Depends(current_user),
) -> list[AlertDeliveryOut]:
    rows = (
        await session.execute(
            sa.select(AlertDelivery)
            .where(AlertDelivery.user_id == user.id)
            .order_by(AlertDelivery.created_at.desc())
            .limit(limit)
        )
    ).scalars()
    return [AlertDeliveryOut.model_validate(r) for r in rows]


@router.get("/me/digests", response_model=list[DigestOut])
async def list_digests(
    limit: int = Query(default=10, ge=1, le=50),
    session: AsyncSession = Depends(db_session),
    user: User = Depends(current_user),
) -> list[DigestOut]:
    rows = (
        await session.execute(
            sa.select(Digest)
            .where(Digest.user_id == user.id)
            .order_by(Digest.generated_at.desc())
            .limit(limit)
        )
    ).scalars()
    return [DigestOut.model_validate(r) for r in rows]


@router.post("/me/digests", response_model=DigestOut, status_code=status.HTTP_201_CREATED)
async def generate_digest(
    frequency: DigestFrequency = Query(default=DigestFrequency.DAILY),
    session: AsyncSession = Depends(db_session),
    user: User = Depends(current_user),
) -> DigestOut:
    digest = await build_digest(session, user=user, frequency=frequency)
    if digest is None:
        raise HTTPException(status.HTTP_400_BAD_REQUEST, "Digests are only daily or weekly.")
    await session.commit()
    return DigestOut.model_validate(digest)


# ------------------------------------------------------------------ channels
LINK_INSTRUCTIONS = {
    "telegram": (
        "Open the bot in Telegram and send: /link {code}. Until you do, nothing is "
        "sent to that chat, and the chat cannot read anything about your account. "
        "The code expires shortly and works once. Send it in a direct message with "
        "the bot, not in a group."
    ),
}


@router.get("/me/channels", response_model=list[ChannelLinkOut])
async def list_channels(
    session: AsyncSession = Depends(db_session),
    user: User = Depends(current_user),
) -> list[ChannelLinkOut]:
    rows = (
        await session.execute(
            sa.select(NotificationChannelLink).where(NotificationChannelLink.user_id == user.id)
        )
    ).scalars()
    return [
        ChannelLinkOut(
            id=r.id,
            channel=r.channel,
            verified=r.verified,
            verified_at=r.verified_at,
            link_code=None,  # never echoed back after creation
            link_code_expires_at=r.link_code_expires_at,
        )
        for r in rows
    ]


@router.post("/me/channels", response_model=ChannelLinkOut, status_code=status.HTTP_201_CREATED)
async def start_channel_link(
    body: ChannelLinkIn,
    session: AsyncSession = Depends(db_session),
    user: User = Depends(current_user),
) -> ChannelLinkOut:
    """Begin linking an external channel by issuing a one-time code.

    The code — not the chat id — is what proves the chat belongs to this account.
    Without it, anyone who knew or guessed a chat id could attach it to their own
    account and start receiving another person's alerts.

    Issuing the code is only half the exchange. It is redeemed by sending it
    *into the chat*, where Telegram observes which chat it came from and tells
    the webhook. Nothing this endpoint returns can complete a link on its own.
    """
    code = telegram.new_link_code()
    link = NotificationChannelLink(
        user_id=user.id,
        channel=body.channel,
        external_id=telegram.pending_placeholder(code),
        link_code=code,
        link_code_expires_at=telegram.code_expiry(),
        verified=False,
    )
    session.add(link)
    await session.commit()
    await session.refresh(link)
    return ChannelLinkOut(
        id=link.id,
        channel=link.channel,
        verified=False,
        verified_at=None,
        link_code=code,
        instructions=LINK_INSTRUCTIONS.get(body.channel, "").format(code=code) or None,
        link_code_expires_at=link.link_code_expires_at,
    )


@router.post("/me/channels/verify", response_model=ChannelLinkOut)
async def verify_channel_link(
    body: ChannelVerifyIn,
    session: AsyncSession = Depends(db_session),
    user: User = Depends(current_user),
) -> ChannelLinkOut:
    """Legacy endpoint. Reports on a *pending* link; it can no longer grant one.

    This endpoint used to take a `link_code` the caller had just been issued
    together with an `external_id` the caller simply asserted, and set
    ``verified = True`` from the pair. Both halves came from the same
    authenticated request, so the exchange proved only that the user could type:
    anyone could bind any chat id, including someone else's, and start receiving
    that chat's alerts.

    Verification now happens in the only place that can actually witness the
    chat — the webhook, from an update Telegram itself delivered. The route is
    kept so existing clients still parse a response, but the `external_id` they
    send is ignored on purpose: nothing a caller asserts about which chat they
    own may influence the binding.

    **Use `GET /me/channels` to poll for verification.** That is the supported
    way to watch `verified` flip, and the only one that keeps working after the
    link succeeds.

    Behaviour of *this* route, stated exactly, because it is easy to misread:

    * While the code is outstanding, it returns ``200`` with
      ``verified: false`` and the linking instructions.
    * Once the webhook redeems the code, the code is consumed and cleared — it
      is deliberately not retained, since a code that survives its use is a
      credential that never expires. Nothing then matches the lookup, so this
      route returns ``404``.

    That ``404`` therefore means "no link is pending under this code", which
    covers *both* a code that never existed and one that has already been
    redeemed successfully. It is not an error signal and must not be read as
    failure: a client that treats it as one will report a successful link as
    broken. Check `GET /me/channels` to find out which happened.

    Because a row only carries a `link_code` while it is unredeemed, the
    `verified` field in this response is always ``false``.
    """
    link = (
        await session.execute(
            sa.select(NotificationChannelLink).where(
                NotificationChannelLink.user_id == user.id,
                NotificationChannelLink.channel == body.channel,
                NotificationChannelLink.link_code == body.link_code,
            )
        )
    ).scalar_one_or_none()
    if link is None:
        # Either never issued, or already redeemed through the webhook. The two
        # are indistinguishable here by design, and `GET /me/channels` is where
        # a client learns which.
        raise HTTPException(
            status.HTTP_404_NOT_FOUND,
            "No pending link with that code. If you have already sent /link to the bot, "
            "check GET /me/channels — the link may have completed successfully.",
        )

    # Reached only while the code is still outstanding, so `verified` is false
    # and the instructions are still what the user needs. Both are read from the
    # row rather than hardcoded, so this stays truthful if that ever changes.
    return ChannelLinkOut(
        id=link.id,
        channel=link.channel,
        verified=link.verified,
        verified_at=link.verified_at,
        link_code=None,
        instructions=LINK_INSTRUCTIONS.get(link.channel, "").format(code=body.link_code) or None,
    )


@router.delete("/me/channels/{link_id}", status_code=status.HTTP_204_NO_CONTENT)
async def unlink_channel(
    link_id: uuid.UUID,
    session: AsyncSession = Depends(db_session),
    user: User = Depends(current_user),
) -> None:
    link = (
        await session.execute(
            sa.select(NotificationChannelLink).where(
                NotificationChannelLink.id == link_id,
                NotificationChannelLink.user_id == user.id,
            )
        )
    ).scalar_one_or_none()
    if link is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "No such channel link.")
    await session.delete(link)
    await session.commit()


# ------------------------------------------------------------------ feedback
@router.post(
    "/me/opportunities/{opportunity_id}/feedback",
    response_model=FeedbackOut,
    status_code=status.HTTP_201_CREATED,
)
async def record_feedback(
    opportunity_id: uuid.UUID,
    body: FeedbackIn,
    session: AsyncSession = Depends(db_session),
    user: User = Depends(current_user),
) -> FeedbackOut:
    """Tell us what you thought. It never retrains the global score.

    Section 27 of the brief, and worth being explicit about: if one person saying
    "not relevant to me" moved the global number, the global number would stop
    being about the world and start being about whoever clicks most.
    """
    opportunity = await session.get(Opportunity, opportunity_id)
    if opportunity is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "No such opportunity.")
    row = UserOpportunityFeedback(
        user_id=user.id,
        opportunity_id=opportunity_id,
        feedback=body.feedback,
        note=body.note,
    )
    session.add(row)
    await session.commit()
    await session.refresh(row)
    return FeedbackOut.model_validate(row)


@router.get("/me/feedback", response_model=list[FeedbackOut])
async def list_feedback(
    session: AsyncSession = Depends(db_session),
    user: User = Depends(current_user),
) -> list[FeedbackOut]:
    rows = (
        await session.execute(
            sa.select(UserOpportunityFeedback)
            .where(UserOpportunityFeedback.user_id == user.id)
            .order_by(UserOpportunityFeedback.created_at.desc())
        )
    ).scalars()
    return [FeedbackOut.model_validate(r) for r in rows]


def since(days: int) -> datetime:
    return datetime.now(UTC) - timedelta(days=days)
