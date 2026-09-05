"""All ORM models. See docs/database-schema.md for the narrative version."""

from __future__ import annotations

import uuid
from datetime import datetime
from typing import Any

import sqlalchemy as sa
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.db.base import (
    Base,
    CreatedAtMixin,
    EmbeddingType,
    ImmutableMixin,
    TimestampMixin,
    UUIDMixin,
)
from app.models.enums import (
    CapitalFlexibility,
    ConditionCheckState,
    ConditionKind,
    ConditionState,
    DecisionKind,
    DeliveryStatus,
    DigestFrequency,
    EvidenceStatus,
    LiveValidationState,
    MatchDecision,
    MaturityStage,
    NotificationChannel,
    ObservationStatus,
    OpportunityState,
    OpportunityStatus,
    OpportunityType,
    OutcomeKind,
    ParticipationKind,
    RiskCategory,
    RiskLevel,
    RiskSeverity,
    RiskTolerance,
    Role,
    RunStatus,
    SkepticStatus,
    SourceStatus,
    SubjectType,
    TimeCommitment,
    TimeHorizon,
    TrendState,
    UserInterest,
    UserTimeHorizon,
    ValidationStatus,
    WatchTargetKind,
)

JSONB = sa.JSON


# --------------------------------------------------------------------------- users
class User(UUIDMixin, TimestampMixin, Base):
    __tablename__ = "users"

    email: Mapped[str] = mapped_column(sa.String(320), unique=True, index=True)
    full_name: Mapped[str | None] = mapped_column(sa.String(200))
    password_hash: Mapped[str] = mapped_column(sa.String(255))
    role: Mapped[str] = mapped_column(sa.String(20), default=Role.VIEWER)
    is_active: Mapped[bool] = mapped_column(sa.Boolean, default=True)
    locale: Mapped[str] = mapped_column(sa.String(5), default="en")
    last_login_at: Mapped[datetime | None] = mapped_column(sa.DateTime(timezone=True))

    preferences: Mapped[UserPreference | None] = relationship(
        back_populates="user", uselist=False, cascade="all, delete-orphan"
    )


class UserPreference(UUIDMixin, TimestampMixin, Base):
    __tablename__ = "user_preferences"

    user_id: Mapped[uuid.UUID] = mapped_column(sa.ForeignKey("users.id", ondelete="CASCADE"), unique=True)
    countries: Mapped[list[str]] = mapped_column(JSONB, default=list)
    industries: Mapped[list[str]] = mapped_column(JSONB, default=list)
    excluded_industries: Mapped[list[str]] = mapped_column(JSONB, default=list)
    excluded_asset_types: Mapped[list[str]] = mapped_column(JSONB, default=list)
    ethical_exclusions: Mapped[list[str]] = mapped_column(JSONB, default=list)
    preferred_categories: Mapped[list[str]] = mapped_column(JSONB, default=list)
    max_risk_level: Mapped[str] = mapped_column(sa.String(20), default=RiskLevel.HIGH)
    min_confidence: Mapped[float] = mapped_column(sa.Float, default=0.65)
    capital_min_usd: Mapped[float] = mapped_column(sa.Float, default=0.0)
    capital_max_usd: Mapped[float] = mapped_column(sa.Float, default=10000.0)
    time_horizon: Mapped[str] = mapped_column(sa.String(20), default=TimeHorizon.MEDIUM)
    alert_frequency: Mapped[str] = mapped_column(sa.String(20), default="daily")
    prefers_business_over_investment: Mapped[bool] = mapped_column(sa.Boolean, default=True)
    include_china_sourcing: Mapped[bool] = mapped_column(sa.Boolean, default=True)

    # ---- phase 4: inputs to Personal Relevance -----------------------------
    # These describe *a* user, never this user. Nothing about Libya, Tunisia or
    # any particular person belongs in the domain model; it belongs in a row.
    #: Opportunity types in priority order, e.g. ["import_distribution", "business"].
    type_priority: Mapped[list[str]] = mapped_column(JSONB, default=list)
    #: Countries the person can actually operate in, most important first.
    priority_geographies: Mapped[list[str]] = mapped_column(JSONB, default=list)
    #: Industries the person has worked in, which lowers execution risk for them.
    experience_industries: Mapped[list[str]] = mapped_column(JSONB, default=list)
    has_supplier_access: Mapped[bool] = mapped_column(sa.Boolean, default=False)
    has_distribution_access: Mapped[bool] = mapped_column(sa.Boolean, default=False)
    technical_ability: Mapped[str] = mapped_column(sa.String(20), default="medium")
    weekly_hours_available: Mapped[int | None] = mapped_column(sa.Integer)
    #: Regulatory reach: places where the person can legally register and trade.
    regulatory_access: Mapped[list[str]] = mapped_column(JSONB, default=list)

    extra: Mapped[dict[str, Any]] = mapped_column(JSONB, default=dict)

    user: Mapped[User] = relationship(back_populates="preferences")


# ------------------------------------------------------------------------- sources
class Source(UUIDMixin, TimestampMixin, Base):
    __tablename__ = "sources"

    slug: Mapped[str] = mapped_column(sa.String(80), unique=True, index=True)
    name: Mapped[str] = mapped_column(sa.String(200))
    adapter_key: Mapped[str] = mapped_column(sa.String(80))
    source_group: Mapped[str] = mapped_column(sa.String(80), default="independent")
    source_class: Mapped[str] = mapped_column(sa.String(40), default="primary_api")
    status: Mapped[str] = mapped_column(sa.String(20), default=SourceStatus.ACTIVE)
    enabled: Mapped[bool] = mapped_column(sa.Boolean, default=True)
    schedule_cron: Mapped[str] = mapped_column(sa.String(80), default="0 3 * * *")
    rate_limit_per_minute: Mapped[int] = mapped_column(sa.Integer, default=30)
    daily_quota: Mapped[int | None] = mapped_column(sa.Integer)
    reliability: Mapped[float] = mapped_column(sa.Float, default=0.5)
    consecutive_failures: Mapped[int] = mapped_column(sa.Integer, default=0)
    last_run_at: Mapped[datetime | None] = mapped_column(sa.DateTime(timezone=True))
    last_success_at: Mapped[datetime | None] = mapped_column(sa.DateTime(timezone=True))
    config: Mapped[dict[str, Any]] = mapped_column(JSONB, default=dict)
    notes: Mapped[str | None] = mapped_column(sa.Text)

    runs: Mapped[list[SourceRun]] = relationship(back_populates="source")


class SourceCredential(UUIDMixin, TimestampMixin, Base):
    __tablename__ = "source_credentials"

    source_id: Mapped[uuid.UUID] = mapped_column(sa.ForeignKey("sources.id", ondelete="CASCADE"))
    key: Mapped[str] = mapped_column(sa.String(80))
    value_encrypted: Mapped[str] = mapped_column(sa.Text)
    hint: Mapped[str | None] = mapped_column(sa.String(40))
    rotated_at: Mapped[datetime | None] = mapped_column(sa.DateTime(timezone=True))
    expires_at: Mapped[datetime | None] = mapped_column(sa.DateTime(timezone=True))

    __table_args__ = (sa.UniqueConstraint("source_id", "key", name="ux_source_credential"),)


class SourceRun(UUIDMixin, CreatedAtMixin, Base):
    __tablename__ = "source_runs"

    source_id: Mapped[uuid.UUID] = mapped_column(sa.ForeignKey("sources.id", ondelete="CASCADE"))
    status: Mapped[str] = mapped_column(sa.String(20), default=RunStatus.RUNNING, index=True)
    trigger: Mapped[str] = mapped_column(sa.String(20), default="scheduled")
    started_at: Mapped[datetime] = mapped_column(sa.DateTime(timezone=True))
    finished_at: Mapped[datetime | None] = mapped_column(sa.DateTime(timezone=True))
    records_fetched: Mapped[int] = mapped_column(sa.Integer, default=0)
    records_stored: Mapped[int] = mapped_column(sa.Integer, default=0)
    records_duplicate: Mapped[int] = mapped_column(sa.Integer, default=0)
    records_rejected: Mapped[int] = mapped_column(sa.Integer, default=0)
    observations_written: Mapped[int] = mapped_column(sa.Integer, default=0)
    http_requests: Mapped[int] = mapped_column(sa.Integer, default=0)
    error: Mapped[str | None] = mapped_column(sa.Text)
    duration_ms: Mapped[int | None] = mapped_column(sa.Integer)

    source: Mapped[Source] = relationship(back_populates="runs")


class RawRecord(UUIDMixin, ImmutableMixin, Base):
    """Verbatim external payload. Never edited, never deleted."""

    __tablename__ = "raw_records"

    source_id: Mapped[uuid.UUID] = mapped_column(sa.ForeignKey("sources.id"), index=True)
    source_run_id: Mapped[uuid.UUID | None] = mapped_column(sa.ForeignKey("source_runs.id"))
    external_id: Mapped[str | None] = mapped_column(sa.String(400))
    content_hash: Mapped[str] = mapped_column(sa.String(64), index=True)
    url: Mapped[str | None] = mapped_column(sa.Text)
    title: Mapped[str | None] = mapped_column(sa.Text)
    content: Mapped[str | None] = mapped_column(sa.Text)  # sanitised copy
    content_raw: Mapped[str | None] = mapped_column(sa.Text)  # verbatim, for audit
    sanitizer_flags: Mapped[list[str]] = mapped_column(JSONB, default=list)
    #: Normalised fingerprint of the headline, used to spot the same wire story
    #: republished by twenty outlets. Null on records collected before Phase 3.
    title_fingerprint: Mapped[str | None] = mapped_column(sa.String(64), index=True)
    payload: Mapped[dict[str, Any]] = mapped_column(JSONB, default=dict)
    geo_scope: Mapped[str] = mapped_column(sa.String(20), default="global")
    published_at: Mapped[datetime | None] = mapped_column(sa.DateTime(timezone=True))
    fetched_at: Mapped[datetime] = mapped_column(sa.DateTime(timezone=True), index=True)

    __table_args__ = (sa.UniqueConstraint("source_id", "content_hash", name="ux_raw_records_source_hash"),)


# ------------------------------------------------------------------ entities/topics
class Entity(UUIDMixin, TimestampMixin, Base):
    __tablename__ = "entities"

    entity_type: Mapped[str] = mapped_column(sa.String(30), index=True)
    canonical_name: Mapped[str] = mapped_column(sa.String(300), index=True)
    normalized: Mapped[str] = mapped_column(sa.String(300), index=True)
    ticker: Mapped[str | None] = mapped_column(sa.String(20))
    country: Mapped[str | None] = mapped_column(sa.String(10))
    description: Mapped[str | None] = mapped_column(sa.Text)
    #: Official identifiers, e.g. {"sec_cik": "0001045810", "ticker": "NVDA",
    #: "exchange": "NASDAQ", "github_repo_id": 1863329, "iso_country": "TN",
    #: "contract_address": "0x...", "chain": "ethereum"}. An identifier match is
    #: proof; a name match never is.
    external_ids: Mapped[dict[str, Any]] = mapped_column(JSONB, default=dict)
    extra: Mapped[dict[str, Any]] = mapped_column(JSONB, default=dict)

    aliases: Mapped[list[EntityAlias]] = relationship(back_populates="entity", cascade="all, delete-orphan")

    __table_args__ = (sa.UniqueConstraint("entity_type", "normalized", name="ux_entity_type_normalized"),)


class EntityAlias(UUIDMixin, TimestampMixin, Base):
    __tablename__ = "entity_aliases"

    entity_id: Mapped[uuid.UUID] = mapped_column(sa.ForeignKey("entities.id", ondelete="CASCADE"))
    alias: Mapped[str] = mapped_column(sa.String(300))
    normalized: Mapped[str] = mapped_column(sa.String(300), index=True)
    entity_type: Mapped[str] = mapped_column(sa.String(30))
    confidence: Mapped[float] = mapped_column(sa.Float, default=1.0)

    entity: Mapped[Entity] = relationship(back_populates="aliases")

    __table_args__ = (sa.UniqueConstraint("normalized", "entity_type", name="ux_entity_alias_norm"),)


class Topic(UUIDMixin, TimestampMixin, Base):
    __tablename__ = "topics"

    label: Mapped[str] = mapped_column(sa.String(300), index=True)
    normalized: Mapped[str] = mapped_column(sa.String(300), unique=True)
    description: Mapped[str | None] = mapped_column(sa.Text)
    category: Mapped[str | None] = mapped_column(sa.String(40), index=True)
    keywords: Mapped[list[str]] = mapped_column(JSONB, default=list)
    first_seen_at: Mapped[datetime | None] = mapped_column(sa.DateTime(timezone=True))
    last_seen_at: Mapped[datetime | None] = mapped_column(sa.DateTime(timezone=True))
    #: True when a language model wrote the label/description. Membership is
    #: always deterministic; only the wording may be model-authored.
    label_is_ai_generated: Mapped[bool] = mapped_column(sa.Boolean, default=False)
    embedding: Mapped[Any | None] = mapped_column(EmbeddingType, nullable=True)
    extra: Mapped[dict[str, Any]] = mapped_column(JSONB, default=dict)


class TopicEntity(UUIDMixin, CreatedAtMixin, Base):
    __tablename__ = "topic_entities"

    topic_id: Mapped[uuid.UUID] = mapped_column(sa.ForeignKey("topics.id", ondelete="CASCADE"))
    entity_id: Mapped[uuid.UUID] = mapped_column(sa.ForeignKey("entities.id", ondelete="CASCADE"))
    weight: Mapped[float] = mapped_column(sa.Float, default=1.0)

    __table_args__ = (sa.UniqueConstraint("topic_id", "entity_id", name="ux_topic_entity"),)


# -------------------------------------------------------------------------- signals
class Signal(UUIDMixin, TimestampMixin, Base):
    """A *definition* of a measured series."""

    __tablename__ = "signals"

    entity_id: Mapped[uuid.UUID] = mapped_column(sa.ForeignKey("entities.id"), index=True)
    signal_type: Mapped[str] = mapped_column(sa.String(60), index=True)
    signal_class: Mapped[str] = mapped_column(sa.String(30), default="attention")
    geo_scope: Mapped[str] = mapped_column(sa.String(20), default="global")
    source_id: Mapped[uuid.UUID] = mapped_column(sa.ForeignKey("sources.id"))
    unit: Mapped[str | None] = mapped_column(sa.String(40))
    is_proxy: Mapped[bool] = mapped_column(sa.Boolean, default=False)
    description: Mapped[str | None] = mapped_column(sa.Text)

    observations: Mapped[list[SignalObservation]] = relationship(back_populates="signal")

    __table_args__ = (
        sa.UniqueConstraint(
            "entity_id", "signal_type", "geo_scope", "source_id", name="ux_signal_definition"
        ),
    )


class SignalObservation(UUIDMixin, ImmutableMixin, Base):
    __tablename__ = "signal_observations"

    signal_id: Mapped[uuid.UUID] = mapped_column(sa.ForeignKey("signals.id", ondelete="CASCADE"), index=True)
    raw_record_id: Mapped[uuid.UUID | None] = mapped_column(sa.ForeignKey("raw_records.id"))
    observed_at: Mapped[datetime] = mapped_column(sa.DateTime(timezone=True), index=True)
    period_start: Mapped[datetime | None] = mapped_column(sa.DateTime(timezone=True))
    period_end: Mapped[datetime | None] = mapped_column(sa.DateTime(timezone=True))
    #: Null when `status` is not `ok`. A gap is a gap, never a zero.
    value: Mapped[float | None] = mapped_column(sa.Float)
    status: Mapped[str] = mapped_column(sa.String(10), default=ObservationStatus.OK, index=True)
    #: ISO-4217 code when the value is money. The original currency is never
    #: replaced by a converted one; conversion is a display concern.
    currency: Mapped[str | None] = mapped_column(sa.String(3))
    previous_value: Mapped[float | None] = mapped_column(sa.Float)
    pct_change: Mapped[float | None] = mapped_column(sa.Float)
    confidence: Mapped[float] = mapped_column(sa.Float, default=0.5)
    source_reliability: Mapped[float] = mapped_column(sa.Float, default=0.5)
    method: Mapped[str] = mapped_column(sa.String(60), default="direct")
    is_proxy: Mapped[bool] = mapped_column(sa.Boolean, default=False)
    collected_at: Mapped[datetime] = mapped_column(sa.DateTime(timezone=True))

    signal: Mapped[Signal] = relationship(back_populates="observations")

    __table_args__ = (sa.UniqueConstraint("signal_id", "observed_at", name="ux_signal_obs_signal_time"),)


class Trend(UUIDMixin, TimestampMixin, Base):
    """One row per subject, updated over time. Not one row per day.

    A trend is a thing the system is watching, with a history; recreating it each
    evaluation would destroy exactly the record that makes it judgeable.
    """

    __tablename__ = "trends"

    subject_type: Mapped[str] = mapped_column(sa.String(20), default=SubjectType.ENTITY, index=True)
    entity_id: Mapped[uuid.UUID | None] = mapped_column(sa.ForeignKey("entities.id", ondelete="CASCADE"))
    topic_id: Mapped[uuid.UUID | None] = mapped_column(sa.ForeignKey("topics.id", ondelete="CASCADE"))

    name: Mapped[str] = mapped_column(sa.String(300), index=True)
    category: Mapped[str | None] = mapped_column(sa.String(40), index=True)
    geo_scope: Mapped[str] = mapped_column(sa.String(20), default="global", index=True)

    state: Mapped[str] = mapped_column(sa.String(20), default=TrendState.CANDIDATE, index=True)
    stage: Mapped[str] = mapped_column(sa.String(30), default=MaturityStage.WEAK_SIGNAL, index=True)

    trend_score: Mapped[float] = mapped_column(sa.Float, default=0.0, index=True)
    confidence: Mapped[float] = mapped_column(sa.Float, default=0.0, index=True)
    peak_score: Mapped[float] = mapped_column(sa.Float, default=0.0)
    peak_score_at: Mapped[datetime | None] = mapped_column(sa.DateTime(timezone=True))

    first_detected_at: Mapped[datetime] = mapped_column(sa.DateTime(timezone=True), index=True)
    last_evaluated_at: Mapped[datetime] = mapped_column(sa.DateTime(timezone=True), index=True)
    last_confirmation_at: Mapped[datetime | None] = mapped_column(sa.DateTime(timezone=True))

    components: Mapped[dict[str, Any]] = mapped_column(JSONB, default=dict)
    penalties: Mapped[dict[str, Any]] = mapped_column(JSONB, default=dict)
    metrics: Mapped[dict[str, Any]] = mapped_column(JSONB, default=dict)
    warnings: Mapped[list[str]] = mapped_column(JSONB, default=list)

    independent_source_count: Mapped[int] = mapped_column(sa.Integer, default=0)
    distinct_signal_types: Mapped[int] = mapped_column(sa.Integer, default=0)
    observation_count: Mapped[int] = mapped_column(sa.Integer, default=0)
    history_days: Mapped[int] = mapped_column(sa.Integer, default=0)
    missing_observation_count: Mapped[int] = mapped_column(sa.Integer, default=0)

    is_spike: Mapped[bool] = mapped_column(sa.Boolean, default=False)
    is_seasonal: Mapped[bool] = mapped_column(sa.Boolean, default=False)

    explanation: Mapped[str | None] = mapped_column(sa.Text)
    explanation_model_run_id: Mapped[uuid.UUID | None] = mapped_column(sa.ForeignKey("model_runs.id"))

    __table_args__ = (
        sa.UniqueConstraint("subject_type", "entity_id", "topic_id", "geo_scope", name="ux_trend_subject"),
    )


class TrendSnapshot(UUIDMixin, ImmutableMixin, Base):
    """What the engine believed on one evaluation. Append-only, so score history
    can be plotted and a past call can be audited."""

    __tablename__ = "trend_snapshots"

    trend_id: Mapped[uuid.UUID] = mapped_column(sa.ForeignKey("trends.id", ondelete="CASCADE"), index=True)
    evaluated_at: Mapped[datetime] = mapped_column(sa.DateTime(timezone=True), index=True)
    formula_version: Mapped[str] = mapped_column(sa.String(20))
    trend_score: Mapped[float] = mapped_column(sa.Float)
    confidence: Mapped[float] = mapped_column(sa.Float)
    stage: Mapped[str] = mapped_column(sa.String(30))
    state: Mapped[str] = mapped_column(sa.String(20))
    components: Mapped[dict[str, Any]] = mapped_column(JSONB, default=dict)
    penalties: Mapped[dict[str, Any]] = mapped_column(JSONB, default=dict)
    metrics: Mapped[dict[str, Any]] = mapped_column(JSONB, default=dict)
    warnings: Mapped[list[str]] = mapped_column(JSONB, default=list)


class TrendSignal(UUIDMixin, TimestampMixin, Base):
    """Which measured series support a trend, and what each contributes."""

    __tablename__ = "trend_signals"

    trend_id: Mapped[uuid.UUID] = mapped_column(sa.ForeignKey("trends.id", ondelete="CASCADE"))
    signal_id: Mapped[uuid.UUID] = mapped_column(sa.ForeignKey("signals.id", ondelete="CASCADE"))
    source_id: Mapped[uuid.UUID] = mapped_column(sa.ForeignKey("sources.id"))
    source_group: Mapped[str] = mapped_column(sa.String(80), default="independent")
    signal_type: Mapped[str] = mapped_column(sa.String(60))
    growth_30d: Mapped[float | None] = mapped_column(sa.Float)
    acceleration: Mapped[float | None] = mapped_column(sa.Float)
    observation_count: Mapped[int] = mapped_column(sa.Integer, default=0)
    is_proxy: Mapped[bool] = mapped_column(sa.Boolean, default=False)
    counted_as_independent: Mapped[bool] = mapped_column(sa.Boolean, default=True)
    contribution: Mapped[float] = mapped_column(sa.Float, default=0.0)

    __table_args__ = (sa.UniqueConstraint("trend_id", "signal_id", name="ux_trend_signal"),)


class EntityMatchCandidate(UUIDMixin, TimestampMixin, Base):
    """An uncertain name match, parked for a human instead of being guessed.

    The system merges only on an official identifier or an exact known alias.
    Everything in between lands here.
    """

    __tablename__ = "entity_match_candidates"

    observed_name: Mapped[str] = mapped_column(sa.String(300))
    observed_normalized: Mapped[str] = mapped_column(sa.String(300), index=True)
    entity_type: Mapped[str] = mapped_column(sa.String(30), index=True)
    candidate_entity_id: Mapped[uuid.UUID] = mapped_column(sa.ForeignKey("entities.id", ondelete="CASCADE"))
    created_entity_id: Mapped[uuid.UUID | None] = mapped_column(
        sa.ForeignKey("entities.id", ondelete="SET NULL")
    )
    confidence: Mapped[float] = mapped_column(sa.Float, default=0.0)
    reason: Mapped[str] = mapped_column(sa.Text)
    evidence: Mapped[dict[str, Any]] = mapped_column(JSONB, default=dict)
    source_id: Mapped[uuid.UUID | None] = mapped_column(sa.ForeignKey("sources.id"))
    decision: Mapped[str] = mapped_column(sa.String(20), default=MatchDecision.PENDING, index=True)
    decided_by_user_id: Mapped[uuid.UUID | None] = mapped_column(sa.ForeignKey("users.id"))
    decided_at: Mapped[datetime | None] = mapped_column(sa.DateTime(timezone=True))
    note: Mapped[str | None] = mapped_column(sa.Text)

    __table_args__ = (
        sa.UniqueConstraint(
            "observed_normalized", "entity_type", "candidate_entity_id", name="ux_match_candidate"
        ),
    )


# -------------------------------------------------------------------- opportunities
class Opportunity(UUIDMixin, TimestampMixin, Base):
    __tablename__ = "opportunities"

    title: Mapped[str] = mapped_column(sa.String(300))
    slug: Mapped[str] = mapped_column(sa.String(320), unique=True, index=True)
    summary: Mapped[str | None] = mapped_column(sa.Text)
    category: Mapped[str] = mapped_column(sa.String(40), index=True)
    subcategory: Mapped[str | None] = mapped_column(sa.String(80))
    geo_scope: Mapped[str] = mapped_column(sa.String(20), default="global", index=True)
    maturity_stage: Mapped[str] = mapped_column(sa.String(30), default=MaturityStage.WEAK_SIGNAL)
    time_horizon: Mapped[str] = mapped_column(sa.String(20), default=TimeHorizon.MEDIUM)
    capital_required_usd: Mapped[float | None] = mapped_column(sa.Float)
    accessibility: Mapped[str] = mapped_column(sa.String(30), default="unknown")
    liquidity: Mapped[str] = mapped_column(sa.String(30), default="unknown")
    risk_level: Mapped[str] = mapped_column(sa.String(20), default=RiskLevel.HIGH, index=True)
    status: Mapped[str] = mapped_column(sa.String(30), default=OpportunityStatus.NEW, index=True)
    adjusted_score: Mapped[float] = mapped_column(sa.Float, default=0.0, index=True)
    confidence: Mapped[float] = mapped_column(sa.Float, default=0.0)
    evidence_completeness: Mapped[float] = mapped_column(sa.Float, default=0.0)
    detected_at: Mapped[datetime] = mapped_column(sa.DateTime(timezone=True), index=True)
    extra: Mapped[dict[str, Any]] = mapped_column(JSONB, default=dict)

    # ---------------------------------------------------------------- phase 4
    #: Which of the four ways of acting this candidate represents. A single trend
    #: can legitimately produce more than one, judged by different criteria.
    opportunity_type: Mapped[str] = mapped_column(sa.String(30), default=OpportunityType.BUSINESS, index=True)
    #: Lifecycle. Separate from `status`, which is the Phase 1 workflow field kept
    #: for compatibility; `state` is the one the Phase 4 UI shows.
    state: Mapped[str] = mapped_column(sa.String(30), default=OpportunityState.CANDIDATE, index=True)
    #: Whether the evidence beneath this came from the live internet, from replayed
    #: fixtures, or from a generated scenario. Shown on every card, without exception.
    validation_status: Mapped[str] = mapped_column(sa.String(20), default=ValidationStatus.DEMO, index=True)

    country: Mapped[str | None] = mapped_column(sa.String(8), index=True)
    industry: Mapped[str | None] = mapped_column(sa.String(60), index=True)

    #: Deterministic scores. Kept apart on purpose: a thing can be attractive and
    #: poorly evidenced at the same time, and one number would hide that.
    #: THE GLOBAL SCORE. Identical for every user who looks at it, forever.
    #: Nothing user-specific may ever be mixed in here; per-user judgement lives
    #: in `user_opportunity_relevance`. Phase 5 made this separation structural
    #: rather than a convention, by moving the per-user columns out of this table.
    opportunity_score: Mapped[float] = mapped_column(sa.Float, default=0.0, index=True)
    raw_score: Mapped[float] = mapped_column(sa.Float, default=0.0)
    penalty_total: Mapped[float] = mapped_column(sa.Float, default=0.0)

    #: Kept only so historical rows written before Phase 5 still read. Nothing
    #: writes it any more; the API does not expose it. See UserOpportunityRelevance.
    personal_relevance: Mapped[float | None] = mapped_column(sa.Float, index=True)

    components: Mapped[dict[str, Any]] = mapped_column(JSONB, default=dict)
    penalties: Mapped[dict[str, Any]] = mapped_column(JSONB, default=dict)
    confidence_parts: Mapped[dict[str, Any]] = mapped_column(JSONB, default=dict)
    #: Legacy, as above.
    relevance_parts: Mapped[dict[str, Any]] = mapped_column(JSONB, default=dict)
    metrics: Mapped[dict[str, Any]] = mapped_column(JSONB, default=dict)

    #: Where in the world the evidence for this is strongest, e.g.
    #: {"CN": 0.9, "DE": 0.6, "US": 0.55}. Used by /discover and by the
    #: cross-country engine; never by the score itself.
    evidence_countries: Mapped[dict[str, Any]] = mapped_column(JSONB, default=dict)
    #: Experimental, excluded from the global score until backtested.
    transferability: Mapped[dict[str, Any]] = mapped_column(JSONB, default=dict)
    warnings: Mapped[list[str]] = mapped_column(JSONB, default=list)

    #: Experimental measures, displayed separately and NOT folded into the score
    #: until they have been backtested.
    geographic_gap: Mapped[float | None] = mapped_column(sa.Float)
    geographic_gap_parts: Mapped[dict[str, Any]] = mapped_column(JSONB, default=dict)
    adoption_attention_ratio: Mapped[float | None] = mapped_column(sa.Float)
    adoption_attention_parts: Mapped[dict[str, Any]] = mapped_column(JSONB, default=dict)

    thesis: Mapped[str | None] = mapped_column(sa.Text)
    counter_thesis: Mapped[str | None] = mapped_column(sa.Text)
    mechanism: Mapped[str | None] = mapped_column(sa.Text)
    why_early: Mapped[list[str]] = mapped_column(JSONB, default=list)
    missing_evidence: Mapped[list[str]] = mapped_column(JSONB, default=list)
    next_research_steps: Mapped[list[str]] = mapped_column(JSONB, default=list)
    #: Output of the type-specific analyzer (business / import / equity / crypto).
    analysis: Mapped[dict[str, Any]] = mapped_column(JSONB, default=dict)

    independent_source_count: Mapped[int] = mapped_column(sa.Integer, default=0)
    distinct_signal_types: Mapped[int] = mapped_column(sa.Integer, default=0)
    evidence_count: Mapped[int] = mapped_column(sa.Integer, default=0)

    algorithm_version: Mapped[str] = mapped_column(sa.String(20), default="0.0.0")
    last_evaluated_at: Mapped[datetime | None] = mapped_column(sa.DateTime(timezone=True), index=True)
    peak_score: Mapped[float] = mapped_column(sa.Float, default=0.0)

    #: The trend this candidate was generated from. Together with the type and the
    #: geography it is the identity of the opportunity, which is what stops a new
    #: one being invented on every evaluation.
    primary_trend_id: Mapped[uuid.UUID | None] = mapped_column(
        sa.ForeignKey("trends.id", ondelete="CASCADE"), index=True
    )

    __table_args__ = (
        sa.UniqueConstraint(
            "opportunity_type", "primary_trend_id", "geo_scope", name="ux_opportunity_subject"
        ),
    )


class OpportunityTrend(UUIDMixin, CreatedAtMixin, Base):
    """Every trend that supports an opportunity, not only the primary one."""

    __tablename__ = "opportunity_trends"

    opportunity_id: Mapped[uuid.UUID] = mapped_column(
        sa.ForeignKey("opportunities.id", ondelete="CASCADE"), index=True
    )
    trend_id: Mapped[uuid.UUID] = mapped_column(sa.ForeignKey("trends.id", ondelete="CASCADE"))
    role: Mapped[str] = mapped_column(sa.String(30), default="primary")

    __table_args__ = (sa.UniqueConstraint("opportunity_id", "trend_id", name="ux_opportunity_trend"),)


class OpportunityTopic(UUIDMixin, CreatedAtMixin, Base):
    __tablename__ = "opportunity_topics"

    opportunity_id: Mapped[uuid.UUID] = mapped_column(
        sa.ForeignKey("opportunities.id", ondelete="CASCADE"), index=True
    )
    topic_id: Mapped[uuid.UUID] = mapped_column(sa.ForeignKey("topics.id", ondelete="CASCADE"))

    __table_args__ = (sa.UniqueConstraint("opportunity_id", "topic_id", name="ux_opportunity_topic"),)


class OpportunityCondition(UUIDMixin, TimestampMixin, Base):
    """What would confirm this thesis, and what would prove it wrong.

    Both are stored as rows rather than prose so that a later phase can actually
    check them against arriving data instead of re-reading a paragraph.
    """

    __tablename__ = "opportunity_conditions"

    opportunity_id: Mapped[uuid.UUID] = mapped_column(
        sa.ForeignKey("opportunities.id", ondelete="CASCADE"), index=True
    )
    kind: Mapped[str] = mapped_column(sa.String(20), default=ConditionKind.CONFIRMATION, index=True)
    description: Mapped[str] = mapped_column(sa.Text)
    #: Machine-checkable parts, when the condition can be expressed numerically:
    #: {"signal_type": ..., "comparator": "gte", "value": 0.0, "window_days": 90}
    measurable: Mapped[dict[str, Any]] = mapped_column(JSONB, default=dict)
    state: Mapped[str] = mapped_column(sa.String(20), default=ConditionState.PENDING, index=True)
    checked_at: Mapped[datetime | None] = mapped_column(sa.DateTime(timezone=True))


class OpportunityParticipation(UUIDMixin, CreatedAtMixin, Base):
    """A concrete way a person could take part. Never forced to exist."""

    __tablename__ = "opportunity_participation"

    opportunity_id: Mapped[uuid.UUID] = mapped_column(
        sa.ForeignKey("opportunities.id", ondelete="CASCADE"), index=True
    )
    kind: Mapped[str] = mapped_column(sa.String(40), default=ParticipationKind.WATCH)
    description: Mapped[str] = mapped_column(sa.Text)
    difficulty: Mapped[str] = mapped_column(sa.String(20), default="unknown")
    capital_hint: Mapped[str | None] = mapped_column(sa.String(120))


class OpportunityDecision(UUIDMixin, ImmutableMixin, Base):
    """A human judgement, frozen with the numbers as they stood at the time.

    Phase 6 backtesting is only possible if we can ask "what did the system say
    when the person decided?", so the scores are copied here, not referenced.
    """

    __tablename__ = "opportunity_decisions"

    opportunity_id: Mapped[uuid.UUID] = mapped_column(
        sa.ForeignKey("opportunities.id", ondelete="CASCADE"), index=True
    )
    user_id: Mapped[uuid.UUID] = mapped_column(sa.ForeignKey("users.id", ondelete="CASCADE"))
    interest: Mapped[str] = mapped_column(sa.String(20), default=UserInterest.WATCHING, index=True)
    note: Mapped[str | None] = mapped_column(sa.Text)
    decided_at: Mapped[datetime] = mapped_column(sa.DateTime(timezone=True), index=True)

    score_at_decision: Mapped[float] = mapped_column(sa.Float)
    confidence_at_decision: Mapped[float] = mapped_column(sa.Float)
    risk_at_decision: Mapped[str] = mapped_column(sa.String(20))
    relevance_at_decision: Mapped[float | None] = mapped_column(sa.Float)
    state_at_decision: Mapped[str] = mapped_column(sa.String(30))
    algorithm_version: Mapped[str] = mapped_column(sa.String(20))


class OpportunitySignal(UUIDMixin, CreatedAtMixin, Base):
    __tablename__ = "opportunity_signals"

    opportunity_id: Mapped[uuid.UUID] = mapped_column(sa.ForeignKey("opportunities.id", ondelete="CASCADE"))
    signal_id: Mapped[uuid.UUID] = mapped_column(sa.ForeignKey("signals.id"))
    contribution: Mapped[float] = mapped_column(sa.Float, default=0.0)

    __table_args__ = (sa.UniqueConstraint("opportunity_id", "signal_id", name="ux_opportunity_signal"),)


class OpportunityEntity(UUIDMixin, CreatedAtMixin, Base):
    __tablename__ = "opportunity_entities"

    opportunity_id: Mapped[uuid.UUID] = mapped_column(sa.ForeignKey("opportunities.id", ondelete="CASCADE"))
    entity_id: Mapped[uuid.UUID] = mapped_column(sa.ForeignKey("entities.id"))
    role: Mapped[str] = mapped_column(sa.String(40), default="subject")

    __table_args__ = (sa.UniqueConstraint("opportunity_id", "entity_id", name="ux_opportunity_entity"),)


class OpportunityScore(UUIDMixin, ImmutableMixin, Base):
    __tablename__ = "opportunity_scores"

    opportunity_id: Mapped[uuid.UUID] = mapped_column(
        sa.ForeignKey("opportunities.id", ondelete="CASCADE"), index=True
    )
    formula_version: Mapped[str] = mapped_column(sa.String(20))
    components: Mapped[dict[str, Any]] = mapped_column(JSONB, default=dict)
    penalties: Mapped[dict[str, Any]] = mapped_column(JSONB, default=dict)
    raw_score: Mapped[float] = mapped_column(sa.Float)
    penalty_total: Mapped[float] = mapped_column(sa.Float)
    adjusted_score: Mapped[float] = mapped_column(sa.Float)
    confidence: Mapped[float] = mapped_column(sa.Float)
    evidence_completeness: Mapped[float] = mapped_column(sa.Float)
    risk_level: Mapped[str] = mapped_column(sa.String(20))
    computed_at: Mapped[datetime] = mapped_column(sa.DateTime(timezone=True))


class OpportunityRisk(UUIDMixin, CreatedAtMixin, Base):
    __tablename__ = "opportunity_risks"

    opportunity_id: Mapped[uuid.UUID] = mapped_column(sa.ForeignKey("opportunities.id", ondelete="CASCADE"))
    code: Mapped[str] = mapped_column(sa.String(60), index=True)
    category: Mapped[str] = mapped_column(sa.String(40), default=RiskCategory.MARKET, index=True)
    severity: Mapped[str] = mapped_column(sa.String(20), default=RiskSeverity.MEDIUM, index=True)
    rationale: Mapped[str] = mapped_column(sa.Text)
    evidence_item_ids: Mapped[list[str]] = mapped_column(JSONB, default=list)
    #: How sure we are that this risk is real, separate from how bad it would be.
    confidence: Mapped[float] = mapped_column(sa.Float, default=0.5)
    mitigation: Mapped[str | None] = mapped_column(sa.Text)
    #: A blocking risk dominates the overall level instead of being averaged away.
    is_blocking: Mapped[bool] = mapped_column(sa.Boolean, default=False)


class SkepticReview(UUIDMixin, ImmutableMixin, Base):
    __tablename__ = "skeptic_reviews"

    opportunity_id: Mapped[uuid.UUID] = mapped_column(
        sa.ForeignKey("opportunities.id", ondelete="CASCADE"), index=True
    )
    counterarguments: Mapped[list[str]] = mapped_column(JSONB, default=list)
    risk_flags: Mapped[list[str]] = mapped_column(JSONB, default=list)
    missing_evidence: Mapped[list[str]] = mapped_column(JSONB, default=list)
    alternative_explanations: Mapped[list[str]] = mapped_column(JSONB, default=list)
    invalidation_conditions: Mapped[list[str]] = mapped_column(JSONB, default=list)
    manipulation_probability: Mapped[float] = mapped_column(sa.Float, default=0.0)
    #: Only ever a reduction. The skeptic has no mechanism for raising confidence,
    #: which is the entire point of having a separate agent for it.
    confidence_reduction: Mapped[float] = mapped_column(sa.Float, default=0.0)
    status: Mapped[str] = mapped_column(sa.String(30), default=SkepticStatus.CONTINUE_RESEARCH)
    model_run_id: Mapped[uuid.UUID | None] = mapped_column(sa.ForeignKey("model_runs.id"))
    #: The single hardest question this candidate has to answer.
    strongest_counterargument: Mapped[str | None] = mapped_column(sa.Text)
    too_late_reasons: Mapped[list[str]] = mapped_column(JSONB, default=list)
    inaccessible_reasons: Mapped[list[str]] = mapped_column(JSONB, default=list)
    questions_asked: Mapped[list[str]] = mapped_column(JSONB, default=list)
    prompt_version: Mapped[str] = mapped_column(sa.String(20), default="0.0.0")
    reviewed_at: Mapped[datetime | None] = mapped_column(sa.DateTime(timezone=True))


class EvidenceItem(UUIDMixin, ImmutableMixin, Base):
    __tablename__ = "evidence_items"

    opportunity_id: Mapped[uuid.UUID | None] = mapped_column(
        sa.ForeignKey("opportunities.id", ondelete="CASCADE"), index=True
    )
    raw_record_id: Mapped[uuid.UUID | None] = mapped_column(sa.ForeignKey("raw_records.id"))
    signal_observation_id: Mapped[uuid.UUID | None] = mapped_column(sa.ForeignKey("signal_observations.id"))
    claim: Mapped[str] = mapped_column(sa.Text)
    quote: Mapped[str | None] = mapped_column(sa.Text)
    url: Mapped[str | None] = mapped_column(sa.Text)
    source_id: Mapped[uuid.UUID | None] = mapped_column(sa.ForeignKey("sources.id"))
    claim_confidence: Mapped[float] = mapped_column(sa.Float, default=0.5)
    observed_at: Mapped[datetime | None] = mapped_column(sa.DateTime(timezone=True))


class Report(UUIDMixin, ImmutableMixin, Base):
    __tablename__ = "reports"

    opportunity_id: Mapped[uuid.UUID] = mapped_column(
        sa.ForeignKey("opportunities.id", ondelete="CASCADE"), index=True
    )
    language: Mapped[str] = mapped_column(sa.String(5), default="en")
    sections: Mapped[dict[str, Any]] = mapped_column(JSONB, default=dict)
    report_confidence: Mapped[float] = mapped_column(sa.Float, default=0.0)
    citation_density: Mapped[float] = mapped_column(sa.Float, default=0.0)
    model_run_id: Mapped[uuid.UUID | None] = mapped_column(sa.ForeignKey("model_runs.id"))
    generated_at: Mapped[datetime] = mapped_column(sa.DateTime(timezone=True))


# ---------------------------------------------------------------- user interaction
class Watchlist(UUIDMixin, TimestampMixin, Base):
    __tablename__ = "watchlists"

    user_id: Mapped[uuid.UUID] = mapped_column(sa.ForeignKey("users.id", ondelete="CASCADE"))
    name: Mapped[str] = mapped_column(sa.String(200))
    description: Mapped[str | None] = mapped_column(sa.Text)
    min_score: Mapped[float | None] = mapped_column(sa.Float)
    max_risk_level: Mapped[str | None] = mapped_column(sa.String(20))

    items: Mapped[list[WatchlistItem]] = relationship(
        back_populates="watchlist", cascade="all, delete-orphan"
    )


class WatchlistItem(UUIDMixin, TimestampMixin, Base):
    __tablename__ = "watchlist_items"

    watchlist_id: Mapped[uuid.UUID] = mapped_column(sa.ForeignKey("watchlists.id", ondelete="CASCADE"))
    #: opportunity / trend / company / technology / product / country /
    #: industry / keyword. A country or an industry is as followable as a company.
    item_type: Mapped[str] = mapped_column(sa.String(30), default=WatchTargetKind.KEYWORD, index=True)
    entity_id: Mapped[uuid.UUID | None] = mapped_column(sa.ForeignKey("entities.id"))
    opportunity_id: Mapped[uuid.UUID | None] = mapped_column(sa.ForeignKey("opportunities.id"))
    trend_id: Mapped[uuid.UUID | None] = mapped_column(sa.ForeignKey("trends.id", ondelete="CASCADE"))
    country_code: Mapped[str | None] = mapped_column(sa.String(2), index=True)
    industry: Mapped[str | None] = mapped_column(sa.String(80), index=True)
    keyword: Mapped[str | None] = mapped_column(sa.String(200))
    label: Mapped[str | None] = mapped_column(sa.String(240))

    watchlist: Mapped[Watchlist] = relationship(back_populates="items")


class Alert(UUIDMixin, TimestampMixin, Base):
    __tablename__ = "alerts"

    user_id: Mapped[uuid.UUID] = mapped_column(sa.ForeignKey("users.id", ondelete="CASCADE"))
    name: Mapped[str] = mapped_column(sa.String(200))
    trigger_type: Mapped[str] = mapped_column(sa.String(60))
    threshold: Mapped[float | None] = mapped_column(sa.Float)
    channels: Mapped[list[str]] = mapped_column(JSONB, default=list)
    enabled: Mapped[bool] = mapped_column(sa.Boolean, default=True)
    opportunity_id: Mapped[uuid.UUID | None] = mapped_column(sa.ForeignKey("opportunities.id"))
    config: Mapped[dict[str, Any]] = mapped_column(JSONB, default=dict)


class Notification(UUIDMixin, CreatedAtMixin, Base):
    __tablename__ = "notifications"

    user_id: Mapped[uuid.UUID] = mapped_column(sa.ForeignKey("users.id", ondelete="CASCADE"))
    alert_id: Mapped[uuid.UUID | None] = mapped_column(sa.ForeignKey("alerts.id"))
    channel: Mapped[str] = mapped_column(sa.String(20), default=NotificationChannel.IN_APP)
    title: Mapped[str] = mapped_column(sa.String(300))
    body: Mapped[str | None] = mapped_column(sa.Text)
    status: Mapped[str] = mapped_column(sa.String(20), default="pending")
    sent_at: Mapped[datetime | None] = mapped_column(sa.DateTime(timezone=True))
    error: Mapped[str | None] = mapped_column(sa.Text)
    read_at: Mapped[datetime | None] = mapped_column(sa.DateTime(timezone=True))


class UserNote(UUIDMixin, TimestampMixin, Base):
    __tablename__ = "user_notes"

    user_id: Mapped[uuid.UUID] = mapped_column(sa.ForeignKey("users.id", ondelete="CASCADE"))
    opportunity_id: Mapped[uuid.UUID | None] = mapped_column(sa.ForeignKey("opportunities.id"))
    body: Mapped[str] = mapped_column(sa.Text)


class UserDecision(UUIDMixin, ImmutableMixin, Base):
    __tablename__ = "user_decisions"

    user_id: Mapped[uuid.UUID] = mapped_column(sa.ForeignKey("users.id", ondelete="CASCADE"))
    opportunity_id: Mapped[uuid.UUID] = mapped_column(sa.ForeignKey("opportunities.id"))
    decision: Mapped[str] = mapped_column(sa.String(20), default=DecisionKind.WATCH)
    reason: Mapped[str | None] = mapped_column(sa.Text)
    decided_at: Mapped[datetime] = mapped_column(sa.DateTime(timezone=True))


# --------------------------------------------------------------------- backtesting
class Prediction(UUIDMixin, ImmutableMixin, Base):
    """Frozen snapshot at detection time. Never edited, never deleted."""

    __tablename__ = "predictions"

    opportunity_id: Mapped[uuid.UUID] = mapped_column(sa.ForeignKey("opportunities.id"), index=True)
    detected_at: Mapped[datetime] = mapped_column(sa.DateTime(timezone=True), index=True)
    category: Mapped[str] = mapped_column(sa.String(40))
    maturity_stage: Mapped[str] = mapped_column(sa.String(30))
    initial_score: Mapped[float] = mapped_column(sa.Float)
    initial_confidence: Mapped[float] = mapped_column(sa.Float)
    initial_risk_level: Mapped[str] = mapped_column(sa.String(20))
    initial_status: Mapped[str] = mapped_column(sa.String(30))
    initial_evidence_ids: Mapped[list[str]] = mapped_column(JSONB, default=list)
    initial_risk_codes: Mapped[list[str]] = mapped_column(JSONB, default=list)
    snapshot: Mapped[dict[str, Any]] = mapped_column(JSONB, default=dict)


class PredictionOutcome(UUIDMixin, CreatedAtMixin, Base):
    __tablename__ = "prediction_outcomes"

    prediction_id: Mapped[uuid.UUID] = mapped_column(
        sa.ForeignKey("predictions.id", ondelete="CASCADE"), index=True
    )
    horizon_days: Mapped[int] = mapped_column(sa.Integer)
    measured_at: Mapped[datetime] = mapped_column(sa.DateTime(timezone=True))
    outcome_kind: Mapped[str] = mapped_column(sa.String(40), default=OutcomeKind.NO_OUTCOME)
    outcome_value: Mapped[float | None] = mapped_column(sa.Float)
    succeeded: Mapped[bool | None] = mapped_column(sa.Boolean)
    notes: Mapped[str | None] = mapped_column(sa.Text)
    evidence_item_ids: Mapped[list[str]] = mapped_column(JSONB, default=list)

    __table_args__ = (sa.UniqueConstraint("prediction_id", "horizon_days", name="ux_prediction_horizon"),)


# ---------------------------------------------------------------- ops / governance
class SystemAuditLog(UUIDMixin, ImmutableMixin, Base):
    __tablename__ = "system_audit_logs"

    actor_user_id: Mapped[uuid.UUID | None] = mapped_column(sa.ForeignKey("users.id"))
    actor_label: Mapped[str] = mapped_column(sa.String(120), default="system")
    action: Mapped[str] = mapped_column(sa.String(80), index=True)
    object_type: Mapped[str | None] = mapped_column(sa.String(80))
    object_id: Mapped[str | None] = mapped_column(sa.String(80))
    ip_address: Mapped[str | None] = mapped_column(sa.String(64))
    request_id: Mapped[str | None] = mapped_column(sa.String(64))
    before: Mapped[dict[str, Any]] = mapped_column(JSONB, default=dict)
    after: Mapped[dict[str, Any]] = mapped_column(JSONB, default=dict)


class PromptVersion(UUIDMixin, ImmutableMixin, Base):
    __tablename__ = "prompt_versions"

    name: Mapped[str] = mapped_column(sa.String(80), index=True)
    version: Mapped[str] = mapped_column(sa.String(20))
    template: Mapped[str] = mapped_column(sa.Text)
    template_hash: Mapped[str] = mapped_column(sa.String(64))

    __table_args__ = (sa.UniqueConstraint("name", "version", name="ux_prompt_name_version"),)


class ModelRun(UUIDMixin, ImmutableMixin, Base):
    __tablename__ = "model_runs"

    provider: Mapped[str] = mapped_column(sa.String(40))
    model: Mapped[str] = mapped_column(sa.String(80))
    purpose: Mapped[str] = mapped_column(sa.String(60))
    prompt_version_id: Mapped[uuid.UUID | None] = mapped_column(sa.ForeignKey("prompt_versions.id"))
    opportunity_id: Mapped[uuid.UUID | None] = mapped_column(sa.ForeignKey("opportunities.id"))
    input_tokens: Mapped[int] = mapped_column(sa.Integer, default=0)
    output_tokens: Mapped[int] = mapped_column(sa.Integer, default=0)
    cost_usd: Mapped[float] = mapped_column(sa.Float, default=0.0)
    latency_ms: Mapped[int] = mapped_column(sa.Integer, default=0)
    cache_hit: Mapped[bool] = mapped_column(sa.Boolean, default=False)
    succeeded: Mapped[bool] = mapped_column(sa.Boolean, default=True)
    error: Mapped[str | None] = mapped_column(sa.Text)


class HttpCacheEntry(UUIDMixin, TimestampMixin, Base):
    """Conditional-request bookkeeping so polite adapters re-fetch as little as possible."""

    __tablename__ = "http_cache_entries"

    source_id: Mapped[uuid.UUID | None] = mapped_column(sa.ForeignKey("sources.id", ondelete="CASCADE"))
    url_hash: Mapped[str] = mapped_column(sa.String(64), index=True)
    url: Mapped[str] = mapped_column(sa.Text)
    etag: Mapped[str | None] = mapped_column(sa.String(300))
    last_modified: Mapped[str | None] = mapped_column(sa.String(120))
    status_code: Mapped[int] = mapped_column(sa.Integer, default=200)
    fetched_at: Mapped[datetime] = mapped_column(sa.DateTime(timezone=True))

    __table_args__ = (sa.UniqueConstraint("source_id", "url_hash", name="ux_http_cache_source_url"),)


# ============================================================================
# PHASE 5 — global platform, many users
#
# The architectural rule this section exists to enforce:
#
#     GLOBAL DATA        sources -> signals -> trends -> opportunities
#                        -> Global Opportunity Score   (one value, everyone)
#
#     PER USER           global opportunity + user profile
#                        -> User Relevance Score       (different per person)
#
# Nothing below may write into the global tables above, and no user's data may
# influence another user's numbers.
# ============================================================================


class UserProfile(UUIDMixin, TimestampMixin, Base):
    """Who this person is, in the terms the relevance engine needs.

    Deliberately free of any hardcoded country, profession, currency or capital
    level. Everything is a row: change the row and the same opportunity scores
    differently, which is the property the multi-user tests assert.
    """

    __tablename__ = "user_profiles"

    user_id: Mapped[uuid.UUID] = mapped_column(
        sa.ForeignKey("users.id", ondelete="CASCADE"), unique=True, index=True
    )

    # ---- location: five different questions, deliberately not one field ----
    home_country: Mapped[str | None] = mapped_column(sa.String(2), index=True)
    residence_country: Mapped[str | None] = mapped_column(sa.String(2), index=True)
    #: Where they could legally and practically operate.
    operating_countries: Mapped[list[str]] = mapped_column(JSONB, default=list)
    #: Where they know the market well enough to judge it.
    familiar_countries: Mapped[list[str]] = mapped_column(JSONB, default=list)
    #: Where they *want* opportunities from, which is often somewhere else.
    target_countries: Mapped[list[str]] = mapped_column(JSONB, default=list)
    excluded_countries: Mapped[list[str]] = mapped_column(JSONB, default=list)

    # ---- interests ---------------------------------------------------------
    #: Categories in priority order. A category absent from this list is not
    #: disabled; a category in `disabled_categories` is.
    interest_ranking: Mapped[list[str]] = mapped_column(JSONB, default=list)
    disabled_categories: Mapped[list[str]] = mapped_column(JSONB, default=list)
    industries: Mapped[list[str]] = mapped_column(JSONB, default=list)
    excluded_industries: Mapped[list[str]] = mapped_column(JSONB, default=list)

    # ---- capital: never assumed to be USD ---------------------------------
    capital_currency: Mapped[str] = mapped_column(sa.String(3), default="USD")
    preferred_investment_amount: Mapped[float | None] = mapped_column(sa.Float)
    max_capital: Mapped[float | None] = mapped_column(sa.Float)
    capital_flexibility: Mapped[str] = mapped_column(
        sa.String(20), default=CapitalFlexibility.SOMEWHAT_FLEXIBLE
    )

    # ---- what they can do --------------------------------------------------
    #: Free text, user-defined. There is no fixed vocabulary of human skill.
    skills: Mapped[list[str]] = mapped_column(JSONB, default=list)
    experience_industries: Mapped[list[str]] = mapped_column(JSONB, default=list)
    #: Advantages they already hold: supplier_network, distribution_network,
    #: audience, retail_locations, warehouse, manufacturing, technical_team,
    #: sales_team, capital, licenses, geographic_access, qualifications.
    assets: Mapped[list[str]] = mapped_column(JSONB, default=list)

    # ---- how they want to work --------------------------------------------
    risk_tolerance: Mapped[str] = mapped_column(sa.String(20), default=RiskTolerance.MODERATE)
    time_horizon: Mapped[str] = mapped_column(sa.String(20), default=UserTimeHorizon.ONE_TO_THREE_YEARS)
    time_commitment: Mapped[str] = mapped_column(sa.String(20), default=TimeCommitment.PART_TIME)

    # ---- presentation ------------------------------------------------------
    display_currency: Mapped[str] = mapped_column(sa.String(3), default="USD")
    timezone: Mapped[str] = mapped_column(sa.String(60), default="UTC")
    language: Mapped[str] = mapped_column(sa.String(5), default="en")

    # ---- discovery settings ------------------------------------------------
    min_global_score: Mapped[float] = mapped_column(sa.Float, default=0.0)
    min_confidence: Mapped[float] = mapped_column(sa.Float, default=0.0)
    max_risk_level: Mapped[str] = mapped_column(sa.String(20), default=RiskLevel.VERY_HIGH)
    max_capital_required: Mapped[float | None] = mapped_column(sa.Float)
    #: The discovery escape hatch. Without it a profile becomes a cage.
    show_outside_profile: Mapped[bool] = mapped_column(sa.Boolean, default=True)

    digest_frequency: Mapped[str] = mapped_column(sa.String(20), default=DigestFrequency.WEEKLY)
    extra: Mapped[dict[str, Any]] = mapped_column(JSONB, default=dict)


class UserOpportunityRelevance(UUIDMixin, TimestampMixin, Base):
    """How relevant one global opportunity is to one user. Never global.

    Recomputed from the user's profile; a row here can be deleted and rebuilt at
    any time without touching a single global number.
    """

    __tablename__ = "user_opportunity_relevance"

    user_id: Mapped[uuid.UUID] = mapped_column(sa.ForeignKey("users.id", ondelete="CASCADE"), index=True)
    opportunity_id: Mapped[uuid.UUID] = mapped_column(
        sa.ForeignKey("opportunities.id", ondelete="CASCADE"), index=True
    )

    relevance: Mapped[float] = mapped_column(sa.Float, default=0.0, index=True)
    parts: Mapped[dict[str, Any]] = mapped_column(JSONB, default=dict)
    #: Per participation path, e.g. {"build": 84, "learn_skill": 91}. The whole
    #: point: one opportunity is not one number for one person.
    path_relevance: Mapped[dict[str, Any]] = mapped_column(JSONB, default=dict)
    best_path: Mapped[str | None] = mapped_column(sa.String(40))
    #: True when the opportunity falls outside the user's stated filters and is
    #: only visible because they asked to see beyond their profile.
    outside_profile: Mapped[bool] = mapped_column(sa.Boolean, default=False)
    formula_version: Mapped[str] = mapped_column(sa.String(20), default="0.0.0")
    computed_at: Mapped[datetime] = mapped_column(sa.DateTime(timezone=True))

    __table_args__ = (sa.UniqueConstraint("user_id", "opportunity_id", name="ux_user_opportunity_relevance"),)


class UserOpportunityFeedback(UUIDMixin, CreatedAtMixin, Base):
    """What a user told us about a candidate.

    Stored for later research. It never retrains the Global Opportunity Score:
    one person finding something irrelevant says nothing about whether it is a
    good opportunity, and letting it would make the global score personal.
    """

    __tablename__ = "user_opportunity_feedback"

    user_id: Mapped[uuid.UUID] = mapped_column(sa.ForeignKey("users.id", ondelete="CASCADE"), index=True)
    opportunity_id: Mapped[uuid.UUID] = mapped_column(
        sa.ForeignKey("opportunities.id", ondelete="CASCADE"), index=True
    )
    feedback: Mapped[str] = mapped_column(sa.String(30), index=True)
    note: Mapped[str | None] = mapped_column(sa.Text)


# ------------------------------------------------------------ country model
class CountryProfile(UUIDMixin, TimestampMixin, Base):
    """One row per country. No country receives special code anywhere."""

    __tablename__ = "country_profiles"

    iso_code: Mapped[str] = mapped_column(sa.String(2), unique=True, index=True)
    name: Mapped[str] = mapped_column(sa.String(120))
    region: Mapped[str | None] = mapped_column(sa.String(60), index=True)
    currency: Mapped[str | None] = mapped_column(sa.String(3))
    languages: Mapped[list[str]] = mapped_column(JSONB, default=list)

    #: 0-100: how much evidence this system actually holds about this country.
    #: NOT a judgement of the country — a measure of our own blindness.
    data_coverage: Mapped[float] = mapped_column(sa.Float, default=0.0, index=True)
    coverage_parts: Mapped[dict[str, Any]] = mapped_column(JSONB, default=dict)
    last_coverage_at: Mapped[datetime | None] = mapped_column(sa.DateTime(timezone=True))


class CountryFact(UUIDMixin, TimestampMixin, Base):
    """One evidenced fact about one country.

    Every fact carries its source, its date, our confidence in it, and the value
    exactly as published with its own unit. A fact with no source cannot be
    stored, which is what stops a language model from inventing a country.
    """

    __tablename__ = "country_facts"

    country_id: Mapped[uuid.UUID] = mapped_column(
        sa.ForeignKey("country_profiles.id", ondelete="CASCADE"), index=True
    )
    #: Stable internal code, e.g. "population", "gdp_per_capita_ppp",
    #: "internet_penetration", "business_formation_days", "import_restrictions".
    key: Mapped[str] = mapped_column(sa.String(60), index=True)
    value_numeric: Mapped[float | None] = mapped_column(sa.Float)
    value_text: Mapped[str | None] = mapped_column(sa.Text)
    unit: Mapped[str | None] = mapped_column(sa.String(40))
    currency: Mapped[str | None] = mapped_column(sa.String(3))

    source_name: Mapped[str] = mapped_column(sa.String(200))
    source_url: Mapped[str | None] = mapped_column(sa.Text)
    source_id: Mapped[uuid.UUID | None] = mapped_column(sa.ForeignKey("sources.id"))
    as_of: Mapped[datetime] = mapped_column(sa.DateTime(timezone=True), index=True)
    confidence: Mapped[float] = mapped_column(sa.Float, default=0.5)
    #: measured / no_signal / no_data — the distinction the brief calls mandatory.
    status: Mapped[str] = mapped_column(sa.String(20), default=EvidenceStatus.MEASURED, index=True)
    note: Mapped[str | None] = mapped_column(sa.Text)

    __table_args__ = (sa.UniqueConstraint("country_id", "key", "as_of", name="ux_country_fact"),)


class CountryAdoption(UUIDMixin, TimestampMixin, Base):
    """Observed adoption of one subject in one country, for the geographic engine.

    `status` is what makes NO DATA distinguishable from NO SIGNAL: a country with
    `no_data` is not evidence of low adoption, and the engine refuses to treat it
    as such.
    """

    __tablename__ = "country_adoption"

    subject_key: Mapped[str] = mapped_column(sa.String(200), index=True)
    country_code: Mapped[str] = mapped_column(sa.String(2), index=True)
    status: Mapped[str] = mapped_column(sa.String(20), default=EvidenceStatus.NO_DATA, index=True)
    #: none / low / emerging / growing / high — only meaningful when measured.
    level: Mapped[str | None] = mapped_column(sa.String(20))
    adoption_growth: Mapped[float | None] = mapped_column(sa.Float)
    attention_growth: Mapped[float | None] = mapped_column(sa.Float)
    supplier_count: Mapped[int | None] = mapped_column(sa.Integer)
    competitor_count: Mapped[int | None] = mapped_column(sa.Integer)
    unit_price: Mapped[float | None] = mapped_column(sa.Float)
    price_currency: Mapped[str | None] = mapped_column(sa.String(3))
    first_observed_days_ago: Mapped[int | None] = mapped_column(sa.Integer)
    observation_count: Mapped[int] = mapped_column(sa.Integer, default=0)
    source_count: Mapped[int] = mapped_column(sa.Integer, default=0)
    as_of: Mapped[datetime] = mapped_column(sa.DateTime(timezone=True))

    __table_args__ = (sa.UniqueConstraint("subject_key", "country_code", name="ux_country_adoption"),)


class FxRate(UUIDMixin, CreatedAtMixin, Base):
    """A stored exchange rate, with where and when it came from.

    Nothing is ever converted without one of these, and a converted figure never
    replaces an original — see `MoneyValue` on the schemas side.
    """

    __tablename__ = "fx_rates"

    base_currency: Mapped[str] = mapped_column(sa.String(3), index=True)
    quote_currency: Mapped[str] = mapped_column(sa.String(3), index=True)
    rate: Mapped[float] = mapped_column(sa.Float)
    source_name: Mapped[str] = mapped_column(sa.String(120))
    as_of: Mapped[datetime] = mapped_column(sa.DateTime(timezone=True), index=True)

    __table_args__ = (sa.UniqueConstraint("base_currency", "quote_currency", "as_of", name="ux_fx_rate"),)


# ------------------------------------------------------- monitoring & alerts
class OpportunityChangeEvent(UUIDMixin, CreatedAtMixin, Base):
    """A meaningful change in a global opportunity. Global, not per-user."""

    __tablename__ = "opportunity_change_events"

    opportunity_id: Mapped[uuid.UUID] = mapped_column(
        sa.ForeignKey("opportunities.id", ondelete="CASCADE"), index=True
    )
    kind: Mapped[str] = mapped_column(sa.String(40), index=True)
    summary: Mapped[str] = mapped_column(sa.Text)
    old_value: Mapped[str | None] = mapped_column(sa.String(120))
    new_value: Mapped[str | None] = mapped_column(sa.String(120))
    magnitude: Mapped[float | None] = mapped_column(sa.Float)
    detail: Mapped[dict[str, Any]] = mapped_column(JSONB, default=dict)
    occurred_at: Mapped[datetime] = mapped_column(sa.DateTime(timezone=True), index=True)


class ConditionCheck(UUIDMixin, CreatedAtMixin, Base):
    """One evaluation of one confirmation or invalidation condition.

    Immutable history: the interesting question later is not only what the
    condition says now, but when it changed and on what evidence.
    """

    __tablename__ = "condition_checks"

    condition_id: Mapped[uuid.UUID] = mapped_column(
        sa.ForeignKey("opportunity_conditions.id", ondelete="CASCADE"), index=True
    )
    state: Mapped[str] = mapped_column(sa.String(20), default=ConditionCheckState.NOT_CHECKED, index=True)
    previous_state: Mapped[str | None] = mapped_column(sa.String(20))
    reason: Mapped[str] = mapped_column(sa.Text)
    #: The observations that decided it. Empty means we could not check, which
    #: is recorded as UNKNOWN and never as FAILED.
    evidence: Mapped[dict[str, Any]] = mapped_column(JSONB, default=dict)
    checked_at: Mapped[datetime] = mapped_column(sa.DateTime(timezone=True), index=True)


class AlertRule(UUIDMixin, TimestampMixin, Base):
    """A user's standing request to be told about something."""

    __tablename__ = "alert_rules"

    user_id: Mapped[uuid.UUID] = mapped_column(sa.ForeignKey("users.id", ondelete="CASCADE"), index=True)
    name: Mapped[str] = mapped_column(sa.String(140))
    trigger: Mapped[str] = mapped_column(sa.String(40), index=True)
    enabled: Mapped[bool] = mapped_column(sa.Boolean, default=True)
    #: {"min_score": 70, "opportunity_type": "import_distribution", ...}
    conditions: Mapped[dict[str, Any]] = mapped_column(JSONB, default=dict)
    channels: Mapped[list[str]] = mapped_column(JSONB, default=list)
    #: Silence for this many hours after firing, so one noisy day cannot bury
    #: the user in notifications about the same thing.
    cooldown_hours: Mapped[int] = mapped_column(sa.Integer, default=24)
    watchlist_id: Mapped[uuid.UUID | None] = mapped_column(sa.ForeignKey("watchlists.id", ondelete="CASCADE"))
    last_fired_at: Mapped[datetime | None] = mapped_column(sa.DateTime(timezone=True))


class AlertDelivery(UUIDMixin, CreatedAtMixin, Base):
    """One alert, for one user, once.

    `dedupe_key` is what makes "do not overwhelm the user" enforceable rather
    than aspirational: the same fact never reaches the same person twice.
    """

    __tablename__ = "alert_deliveries"

    user_id: Mapped[uuid.UUID] = mapped_column(sa.ForeignKey("users.id", ondelete="CASCADE"), index=True)
    rule_id: Mapped[uuid.UUID | None] = mapped_column(sa.ForeignKey("alert_rules.id", ondelete="SET NULL"))
    opportunity_id: Mapped[uuid.UUID | None] = mapped_column(
        sa.ForeignKey("opportunities.id", ondelete="CASCADE")
    )
    event_id: Mapped[uuid.UUID | None] = mapped_column(
        sa.ForeignKey("opportunity_change_events.id", ondelete="CASCADE")
    )
    channel: Mapped[str] = mapped_column(sa.String(20), index=True)
    title: Mapped[str] = mapped_column(sa.String(300))
    body: Mapped[str] = mapped_column(sa.Text)
    dedupe_key: Mapped[str] = mapped_column(sa.String(200), index=True)
    status: Mapped[str] = mapped_column(sa.String(20), default=DeliveryStatus.PENDING, index=True)
    suppressed_reason: Mapped[str | None] = mapped_column(sa.String(200))
    sent_at: Mapped[datetime | None] = mapped_column(sa.DateTime(timezone=True))

    __table_args__ = (sa.UniqueConstraint("user_id", "dedupe_key", name="ux_alert_dedupe"),)


class Digest(UUIDMixin, CreatedAtMixin, Base):
    """A generated personal digest. Immutable once written."""

    __tablename__ = "digests"

    user_id: Mapped[uuid.UUID] = mapped_column(sa.ForeignKey("users.id", ondelete="CASCADE"), index=True)
    frequency: Mapped[str] = mapped_column(sa.String(20))
    period_start: Mapped[datetime] = mapped_column(sa.DateTime(timezone=True))
    period_end: Mapped[datetime] = mapped_column(sa.DateTime(timezone=True))
    sections: Mapped[dict[str, Any]] = mapped_column(JSONB, default=dict)
    item_count: Mapped[int] = mapped_column(sa.Integer, default=0)
    generated_at: Mapped[datetime] = mapped_column(sa.DateTime(timezone=True), index=True)


class NotificationChannelLink(UUIDMixin, TimestampMixin, Base):
    """A user's link to an external channel, e.g. a Telegram chat.

    The link is established by a one-time code the user must present in the
    channel, so a chat id alone can never read another person's data.
    """

    __tablename__ = "notification_channel_links"

    user_id: Mapped[uuid.UUID] = mapped_column(sa.ForeignKey("users.id", ondelete="CASCADE"), index=True)
    channel: Mapped[str] = mapped_column(sa.String(20), index=True)
    #: The external identity, e.g. a Telegram chat id. Unique per channel so two
    #: users can never claim the same chat.
    external_id: Mapped[str] = mapped_column(sa.String(200), index=True)
    link_code: Mapped[str | None] = mapped_column(sa.String(64), index=True)
    #: When the pending code stops being accepted. NULL on rows with no code.
    #: A code with no expiry is a password with no expiry, and this one is
    #: pasted into a chat window where it tends to stay visible.
    link_code_expires_at: Mapped[datetime | None] = mapped_column(sa.DateTime(timezone=True))
    verified: Mapped[bool] = mapped_column(sa.Boolean, default=False)
    verified_at: Mapped[datetime | None] = mapped_column(sa.DateTime(timezone=True))

    #: One chat, one account, enforced by the database rather than by a check
    #: the application might forget to run. Two concurrent /link attempts for the
    #: same chat therefore cannot both succeed: the second hits this constraint
    #: and is rolled back. Pending rows hold a unique "pending:<code>" placeholder
    #: so they never collide with each other or with a real chat id.
    __table_args__ = (sa.UniqueConstraint("channel", "external_id", name="ux_channel_external"),)


class SourceValidation(UUIDMixin, TimestampMixin, Base):
    """Per-source live-data verification status, visible in the product.

    Section 37 of the Phase 5 brief: a deployment must show, per source, whether
    its data has actually been verified against the live internet.
    """

    __tablename__ = "source_validations"

    source_id: Mapped[uuid.UUID] = mapped_column(
        sa.ForeignKey("sources.id", ondelete="CASCADE"), unique=True, index=True
    )
    state: Mapped[str] = mapped_column(sa.String(20), default=LiveValidationState.NOT_VERIFIED, index=True)
    verified_at: Mapped[datetime | None] = mapped_column(sa.DateTime(timezone=True))
    checks_passed: Mapped[int] = mapped_column(sa.Integer, default=0)
    checks_failed: Mapped[int] = mapped_column(sa.Integer, default=0)
    detail: Mapped[dict[str, Any]] = mapped_column(JSONB, default=dict)
    note: Mapped[str | None] = mapped_column(sa.Text)


__all__ = [
    "Alert",
    "AlertDelivery",
    "AlertRule",
    "ConditionCheck",
    "CountryAdoption",
    "CountryFact",
    "CountryProfile",
    "Digest",
    "Entity",
    "EntityAlias",
    "EntityMatchCandidate",
    "EvidenceItem",
    "HttpCacheEntry",
    "ModelRun",
    "Notification",
    "Opportunity",
    "OpportunityEntity",
    "OpportunityRisk",
    "OpportunityScore",
    "OpportunitySignal",
    "Prediction",
    "PredictionOutcome",
    "PromptVersion",
    "RawRecord",
    "Report",
    "Signal",
    "SignalObservation",
    "SkepticReview",
    "Source",
    "SourceCredential",
    "SourceRun",
    "SystemAuditLog",
    "Topic",
    "TopicEntity",
    "Trend",
    "TrendSignal",
    "FxRate",
    "NotificationChannelLink",
    "OpportunityChangeEvent",
    "SourceValidation",
    "TrendSnapshot",
    "User",
    "UserDecision",
    "UserOpportunityFeedback",
    "UserOpportunityRelevance",
    "UserProfile",
    "UserNote",
    "UserPreference",
    "Watchlist",
    "WatchlistItem",
]
