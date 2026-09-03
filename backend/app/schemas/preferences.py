from __future__ import annotations

import uuid

from pydantic import BaseModel, ConfigDict, Field

from app.models.enums import RiskLevel


class PreferenceOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: uuid.UUID
    countries: list[str]
    industries: list[str]
    excluded_industries: list[str]
    excluded_asset_types: list[str]
    ethical_exclusions: list[str]
    preferred_categories: list[str]
    max_risk_level: str
    min_confidence: float
    capital_min_usd: float
    capital_max_usd: float
    time_horizon: str
    alert_frequency: str
    prefers_business_over_investment: bool
    include_china_sourcing: bool


class PreferenceUpdate(BaseModel):
    countries: list[str] | None = None
    industries: list[str] | None = None
    excluded_industries: list[str] | None = None
    excluded_asset_types: list[str] | None = None
    ethical_exclusions: list[str] | None = None
    preferred_categories: list[str] | None = None
    #: Validated against the enum rather than a hand-written pattern. The
    #: pattern here had drifted to accept "medium" and reject "moderate", which
    #: is the value the risk engine actually emits — so this field could only be
    #: set to something no opportunity would ever match.
    max_risk_level: RiskLevel | None = None
    min_confidence: float | None = Field(default=None, ge=0, le=1)
    capital_min_usd: float | None = Field(default=None, ge=0)
    capital_max_usd: float | None = Field(default=None, ge=0)
    time_horizon: str | None = Field(default=None, pattern="^(short|medium|long)$")
    alert_frequency: str | None = Field(default=None, pattern="^(realtime|daily|weekly|off)$")
    prefers_business_over_investment: bool | None = None
    include_china_sourcing: bool | None = None
