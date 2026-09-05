"""Schemas for everything that belongs to one person.

Every model here describes data scoped to the caller. None of them carries a
user id in a request body: the user is the token, never a parameter, so there is
no shape of request that can ask for somebody else's rows.
"""

from __future__ import annotations

import uuid
from datetime import datetime
from typing import Any

from pydantic import BaseModel, ConfigDict, Field

from app.models.enums import (
    AlertTrigger,
    CapitalFlexibility,
    DigestFrequency,
    InterestCategory,
    RiskLevel,
    RiskTolerance,
    TimeCommitment,
    UserFeedback,
    UserTimeHorizon,
    WatchTargetKind,
)

COUNTRY = Field(default=None, min_length=2, max_length=2)


class ProfileIn(BaseModel):
    """Everything a user can tell us about themselves.

    All optional: a half-filled profile is a valid profile, and the relevance
    engine treats unknowns as partial marks rather than zeros.
    """

    # --- location, as five separate questions -------------------------------
    home_country: str | None = COUNTRY
    residence_country: str | None = COUNTRY
    operating_countries: list[str] | None = None
    familiar_countries: list[str] | None = None
    target_countries: list[str] | None = None
    excluded_countries: list[str] | None = None

    # --- interests -----------------------------------------------------------
    #: The thirteen categories in priority order. Order is the preference.
    interest_ranking: list[InterestCategory] | None = None
    disabled_categories: list[InterestCategory] | None = None
    industries: list[str] | None = None
    excluded_industries: list[str] | None = None

    # --- capital, in the user's own currency ---------------------------------
    capital_currency: str | None = Field(default=None, min_length=3, max_length=3)
    preferred_investment_amount: float | None = Field(default=None, ge=0)
    max_capital: float | None = Field(default=None, ge=0)
    capital_flexibility: CapitalFlexibility | None = None

    # --- capability ----------------------------------------------------------
    #: Free text. There is no fixed vocabulary of human skill.
    skills: list[str] | None = None
    experience_industries: list[str] | None = None
    assets: list[str] | None = None

    # --- working style -------------------------------------------------------
    risk_tolerance: RiskTolerance | None = None
    time_horizon: UserTimeHorizon | None = None
    time_commitment: TimeCommitment | None = None

    # --- presentation --------------------------------------------------------
    display_currency: str | None = Field(default=None, min_length=3, max_length=3)
    timezone: str | None = Field(default=None, max_length=60)
    language: str | None = Field(default=None, max_length=5)

    # --- discovery -----------------------------------------------------------
    min_global_score: float | None = Field(default=None, ge=0, le=100)
    min_confidence: float | None = Field(default=None, ge=0, le=100)
    max_risk_level: RiskLevel | None = None
    max_capital_required: float | None = Field(default=None, ge=0)
    #: "Show me opportunities outside my normal profile." Section 14.
    show_outside_profile: bool | None = None
    digest_frequency: DigestFrequency | None = None


class MoneyOut(BaseModel):
    """An amount that never loses its original currency."""

    amount: float
    currency: str
    converted: float | None = None
    converted_to: str | None = None
    rate: float | None = None
    rate_source: str | None = None
    rate_as_of: datetime | None = None
    note: str | None = None


class ProfileOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    home_country: str | None
    residence_country: str | None
    operating_countries: list[str]
    familiar_countries: list[str]
    target_countries: list[str]
    excluded_countries: list[str]
    interest_ranking: list[str]
    disabled_categories: list[str]
    industries: list[str]
    excluded_industries: list[str]
    capital_currency: str
    preferred_investment_amount: float | None
    max_capital: float | None
    capital_flexibility: str
    skills: list[str]
    experience_industries: list[str]
    assets: list[str]
    risk_tolerance: str
    time_horizon: str
    time_commitment: str
    display_currency: str
    timezone: str
    language: str
    min_global_score: float
    min_confidence: float
    max_risk_level: str
    max_capital_required: float | None
    show_outside_profile: bool
    digest_frequency: str

    #: The user's capital in USD, when a stored rate exists. Absent otherwise.
    capital_in_usd: MoneyOut | None = None
    #: How complete the profile is, 0-100, and what is still missing.
    completeness: float = 0.0
    missing: list[str] = Field(default_factory=list)


# ------------------------------------------------------------------ watchlists
class WatchlistItemIn(BaseModel):
    item_type: WatchTargetKind
    opportunity_id: uuid.UUID | None = None
    trend_id: uuid.UUID | None = None
    entity_id: uuid.UUID | None = None
    country_code: str | None = COUNTRY
    industry: str | None = Field(default=None, max_length=80)
    keyword: str | None = Field(default=None, max_length=200)
    label: str | None = Field(default=None, max_length=240)


class WatchlistItemOut(WatchlistItemIn):
    model_config = ConfigDict(from_attributes=True)

    id: uuid.UUID
    created_at: datetime


class WatchlistIn(BaseModel):
    name: str = Field(min_length=1, max_length=200)
    description: str | None = Field(default=None, max_length=2000)
    min_score: float | None = Field(default=None, ge=0, le=100)
    max_risk_level: RiskLevel | None = None


class WatchlistOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: uuid.UUID
    name: str
    description: str | None
    min_score: float | None
    max_risk_level: str | None
    created_at: datetime
    items: list[WatchlistItemOut] = Field(default_factory=list)


# ---------------------------------------------------------------------- alerts
class AlertRuleIn(BaseModel):
    name: str = Field(min_length=1, max_length=140)
    trigger: AlertTrigger
    enabled: bool = True
    conditions: dict[str, Any] = Field(default_factory=dict)
    channels: list[str] = Field(default_factory=lambda: ["in_app"])
    #: Silence after firing. Zero is allowed but strongly discouraged.
    cooldown_hours: int = Field(default=24, ge=0, le=720)
    watchlist_id: uuid.UUID | None = None


class AlertRuleOut(AlertRuleIn):
    model_config = ConfigDict(from_attributes=True)

    id: uuid.UUID
    last_fired_at: datetime | None


class AlertDeliveryOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: uuid.UUID
    opportunity_id: uuid.UUID | None
    channel: str
    title: str
    body: str
    status: str
    suppressed_reason: str | None
    sent_at: datetime | None
    created_at: datetime


class DigestOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: uuid.UUID
    frequency: str
    period_start: datetime
    period_end: datetime
    item_count: int
    sections: dict[str, Any]
    generated_at: datetime


class ChannelLinkOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: uuid.UUID
    channel: str
    verified: bool
    verified_at: datetime | None
    #: Only ever present immediately after creation, for the user to paste.
    link_code: str | None = None
    instructions: str | None = None
    #: When the outstanding code stops working, straight from the row the webhook
    #: checks. NULL once a link is verified (the code and its expiry are cleared
    #: together), so a client can show a real deadline or show nothing — it never
    #: has to invent one. The code itself is still never echoed back.
    link_code_expires_at: datetime | None = None


class ChannelLinkIn(BaseModel):
    channel: str = Field(max_length=20)


class ChannelVerifyIn(BaseModel):
    channel: str = Field(max_length=20)
    link_code: str = Field(min_length=6, max_length=64)
    external_id: str = Field(min_length=1, max_length=200)


# -------------------------------------------------------------------- feedback
class FeedbackIn(BaseModel):
    feedback: UserFeedback
    note: str | None = Field(default=None, max_length=4000)


class FeedbackOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: uuid.UUID
    opportunity_id: uuid.UUID
    feedback: str
    note: str | None
    created_at: datetime
    #: Repeated on every response so the contract is impossible to forget.
    note_on_use: str = (
        "Stored for research and for your own view. It never changes the global "
        "opportunity score: one person's judgement is not evidence about the world."
    )


# ------------------------------------------------------------------ monitoring
class ConditionCheckOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: uuid.UUID
    condition_id: uuid.UUID
    state: str
    previous_state: str | None
    reason: str
    evidence: dict[str, Any]
    checked_at: datetime


class ChangeEventOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: uuid.UUID
    opportunity_id: uuid.UUID
    kind: str
    summary: str
    old_value: str | None
    new_value: str | None
    magnitude: float | None
    occurred_at: datetime


class MonitorResult(BaseModel):
    opportunities: int
    checks: int
    events: int
    alerts_sent: int
    alerts_suppressed: int
    notes: list[str] = Field(default_factory=list)
