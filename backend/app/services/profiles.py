"""User profiles, and the currency conversion that must never be guessed.

Two responsibilities:

* Turn a stored `UserProfile` row into the flat `UserContext` the relevance
  engine reads. Nothing about any particular person is written in code — change
  the row and the same opportunity scores differently.
* Convert the user's stated capital into USD **only** when a stored FX rate
  exists. When it does not, `max_capital_usd` stays `None` and the relevance
  engine scores capital as unknown rather than inventing a rate. The original
  amount and its original currency are never overwritten.
"""

from __future__ import annotations

import uuid
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta

import sqlalchemy as sa
from sqlalchemy.ext.asyncio import AsyncSession

from app.analytics.relevance import UserContext
from app.db.base import as_utc
from app.models.models import FxRate, User, UserProfile

#: A rate older than this is still used, but the caller is told how old it is.
#: Refusing a three-month-old rate outright would silently disable capital
#: scoring for every currency that is not updated daily.
FX_STALE_DAYS = 90


@dataclass(slots=True)
class Money:
    """An amount, its own currency, and — only if evidenced — a conversion.

    The original is the fact. The conversion is a convenience that carries its
    own provenance and can always be absent.
    """

    amount: float
    currency: str
    converted: float | None = None
    converted_to: str | None = None
    rate: float | None = None
    rate_source: str | None = None
    rate_as_of: datetime | None = None
    note: str | None = None

    @property
    def is_converted(self) -> bool:
        return self.converted is not None


async def get_rate(session: AsyncSession, *, base: str, quote: str) -> tuple[float, str, datetime] | None:
    """The most recent stored rate, or None. Never computed, never guessed."""
    base, quote = base.upper(), quote.upper()
    if base == quote:
        return 1.0, "identity", datetime.now(UTC)

    row = (
        await session.execute(
            sa.select(FxRate)
            .where(FxRate.base_currency == base, FxRate.quote_currency == quote)
            .order_by(FxRate.as_of.desc())
            .limit(1)
        )
    ).scalar_one_or_none()
    if row is not None:
        return row.rate, row.source_name, row.as_of

    # An inverse rate is the same evidence read the other way round, which is
    # arithmetic rather than invention.
    inverse = (
        await session.execute(
            sa.select(FxRate)
            .where(FxRate.base_currency == quote, FxRate.quote_currency == base)
            .order_by(FxRate.as_of.desc())
            .limit(1)
        )
    ).scalar_one_or_none()
    if inverse is not None and inverse.rate:
        return 1.0 / inverse.rate, f"{inverse.source_name} (inverted)", inverse.as_of
    return None


async def convert(session: AsyncSession, *, amount: float | None, currency: str, to: str) -> Money | None:
    """Convert, or return the original untouched with a note saying why not."""
    if amount is None:
        return None
    currency, to = currency.upper(), to.upper()
    found = await get_rate(session, base=currency, quote=to)
    if found is None:
        return Money(
            amount=amount,
            currency=currency,
            note=(
                f"No stored exchange rate from {currency} to {to}, so this is shown "
                "in its original currency rather than converted on a guess."
            ),
        )
    rate, source, as_of = found
    age = (datetime.now(UTC) - (as_utc(as_of) or datetime.now(UTC))).days
    note = None
    if age > FX_STALE_DAYS:
        note = f"Converted at a rate that is {age} days old ({source})."
    return Money(
        amount=amount,
        currency=currency,
        converted=round(amount * rate, 2),
        converted_to=to,
        rate=rate,
        rate_source=source,
        rate_as_of=as_of,
        note=note,
    )


async def get_profile(session: AsyncSession, user_id: uuid.UUID) -> UserProfile | None:
    return (
        await session.execute(sa.select(UserProfile).where(UserProfile.user_id == user_id))
    ).scalar_one_or_none()


async def ensure_profile(session: AsyncSession, user: User) -> UserProfile:
    """Every user has a profile row. An empty one is a valid, neutral profile.

    A new user with no answers must still see the product work: the relevance
    engine scores unknowns as partial marks, so an empty profile produces
    middling relevance everywhere rather than zero everywhere.
    """
    existing = await get_profile(session, user.id)
    if existing is not None:
        return existing
    profile = UserProfile(user_id=user.id, language=user.locale or "en")
    session.add(profile)
    await session.flush()
    return profile


async def build_context(session: AsyncSession, profile: UserProfile) -> UserContext:
    """The stored row, flattened for the engine, with capital converted if we can."""
    money = await convert(session, amount=profile.max_capital, currency=profile.capital_currency, to="USD")
    return UserContext(
        home_country=profile.home_country,
        residence_country=profile.residence_country,
        operating_countries=list(profile.operating_countries or []),
        familiar_countries=list(profile.familiar_countries or []),
        target_countries=list(profile.target_countries or []),
        excluded_countries=list(profile.excluded_countries or []),
        interest_ranking=list(profile.interest_ranking or []),
        disabled_categories=list(profile.disabled_categories or []),
        industries=list(profile.industries or []),
        excluded_industries=list(profile.excluded_industries or []),
        capital_currency=profile.capital_currency,
        max_capital=profile.max_capital,
        max_capital_usd=money.converted if money else None,
        capital_flexibility=profile.capital_flexibility,
        skills=list(profile.skills or []),
        experience_industries=list(profile.experience_industries or []),
        assets=list(profile.assets or []),
        risk_tolerance=profile.risk_tolerance,
        time_commitment=profile.time_commitment,
        time_horizon=profile.time_horizon,
        min_global_score=profile.min_global_score,
        min_confidence=profile.min_confidence,
        max_risk_level=profile.max_risk_level,
        max_capital_required=profile.max_capital_required,
        show_outside_profile=profile.show_outside_profile,
    )


def stale_before(days: int) -> datetime:
    return datetime.now(UTC) - timedelta(days=days)
