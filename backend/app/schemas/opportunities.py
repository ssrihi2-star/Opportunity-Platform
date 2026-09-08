from __future__ import annotations

import uuid
from datetime import datetime
from typing import Any

from pydantic import BaseModel, ConfigDict, Field

from app.models.enums import UserInterest


class OpportunityOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: uuid.UUID
    slug: str
    title: str
    summary: str | None
    opportunity_type: str
    category: str | None
    industry: str | None
    geo_scope: str
    country: str | None
    state: str
    validation_status: str
    #: Which evidence this candidate was generated from. A "live_only" row was
    #: produced without any demo or generated evidence anywhere in its chain.
    analysis_mode: str
    maturity_stage: str
    risk_level: str
    #: THE GLOBAL OPPORTUNITY SCORE. Identical for every user who reads it.
    opportunity_score: float
    confidence: float
    peak_score: float
    detected_at: datetime
    last_evaluated_at: datetime | None
    independent_source_count: int
    distinct_signal_types: int
    evidence_count: int
    #: Experimental, shown separately and never folded into the score.
    geographic_gap: float | None
    adoption_attention_ratio: float | None
    algorithm_version: str
    warnings: list[str]

    # ---- the USER RELEVANCE SCORE, for the person holding the token ----------
    # Deliberately separate fields, never one blended number. An opportunity can
    # be a global 91 and a personal 12, and that pair is the useful statement.
    #: 0-100 for this viewer only. None when no profile has been scored yet.
    user_relevance: float | None = None
    #: The participation route that scores best for this viewer.
    best_path: str | None = None
    #: True when this sits outside the viewer's stated filters. Flagged, not hidden.
    outside_profile: bool = False
    relevance_version: str | None = None

    #: Filled in by the endpoint from the linked trend.
    trend_id: uuid.UUID | None = None
    trend_score: float | None = None
    trend_confidence: float | None = None
    trend_stage: str | None = None
    trend_name: str | None = None


class RiskOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    code: str
    category: str
    severity: str
    confidence: float
    rationale: str
    mitigation: str | None
    is_blocking: bool


class ConditionOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: uuid.UUID
    kind: str
    description: str
    measurable: dict[str, Any]
    state: str
    checked_at: datetime | None


class ParticipationOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    kind: str
    description: str
    difficulty: str
    capital_hint: str | None


class SkepticOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    strongest_counterargument: str | None
    counterarguments: list[str]
    missing_evidence: list[str]
    risk_flags: list[str]
    alternative_explanations: list[str]
    too_late_reasons: list[str]
    inaccessible_reasons: list[str]
    manipulation_probability: float
    confidence_reduction: float
    status: str
    questions_asked: list[str]
    prompt_version: str
    reviewed_at: datetime | None


class EvidenceRow(BaseModel):
    kind: str
    signal_type: str
    source_slug: str
    source_group: str
    reliability: float
    is_proxy: bool
    growth_30d: float | None
    observation_count: int
    signal_id: uuid.UUID | None = None


class DecisionOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: uuid.UUID
    interest: str
    note: str | None
    decided_at: datetime
    score_at_decision: float
    confidence_at_decision: float
    risk_at_decision: str
    relevance_at_decision: float | None
    state_at_decision: str
    algorithm_version: str


class ScoreSnapshotOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    formula_version: str
    raw_score: float
    penalty_total: float
    adjusted_score: float
    confidence: float
    risk_level: str
    computed_at: datetime


class OpportunityDetailOut(OpportunityOut):
    thesis: str | None
    counter_thesis: str | None
    mechanism: str | None
    why_early: list[str]
    missing_evidence: list[str]
    next_research_steps: list[str]
    analysis: dict[str, Any]
    components: dict[str, Any]
    penalties: dict[str, Any]
    confidence_parts: dict[str, Any]
    metrics: dict[str, Any]
    raw_score: float
    penalty_total: float
    capital_required_usd: float | None
    geographic_gap_parts: dict[str, Any]
    adoption_attention_parts: dict[str, Any]

    # ---- per-viewer relevance detail, kept in its own block ------------------
    #: Factor-by-factor explanation of the USER RELEVANCE SCORE, per section 5.
    user_relevance_parts: dict[str, Any] = Field(default_factory=dict)
    #: One score per participation route, e.g. {"import": 84, "build": 22}.
    path_relevance: dict[str, float] = Field(default_factory=dict)
    relevance_notes: list[str] = Field(default_factory=list)

    risks: list[RiskOut] = Field(default_factory=list)
    conditions: list[ConditionOut] = Field(default_factory=list)
    participation: list[ParticipationOut] = Field(default_factory=list)
    evidence: list[EvidenceRow] = Field(default_factory=list)
    skeptic: SkepticOut | None = None
    decisions: list[DecisionOut] = Field(default_factory=list)
    history: list[ScoreSnapshotOut] = Field(default_factory=list)


class ReportSectionOut(BaseModel):
    key: str
    title: str
    body: str | None = None
    items: list[dict[str, Any]] = Field(default_factory=list)
    generated: bool = False


class ReportOut(BaseModel):
    opportunity_id: uuid.UUID
    sections: list[ReportSectionOut]
    #: True when a language model phrased some sections and its output passed the
    #: grounding check. False means the report is entirely deterministic.
    narrated: bool = False
    narration_note: str | None = None


class RejectionOut(BaseModel):
    trend_id: str
    trend_name: str
    opportunity_type: str
    reasons: list[str]
    # Research brief fields
    trend_score: float = 0.0
    trend_confidence: float = 0.0
    trend_state: str = "candidate"
    trend_stage: str = "weak_signal"
    observation_count: int = 0
    history_days: int = 0
    distinct_signal_types: int = 0
    independent_source_count: int = 0
    direction: str = "unknown"  # rising, flat, declining, unknown
    evidence_summary: list[dict[str, Any]] = []


class GenerateResult(BaseModel):
    created: int
    updated: int
    rejected: int
    algorithm_version: str
    validation_status: str
    analysis_mode: str = "demo_inclusive"
    #: True when a live-only run produced nothing because there was not enough
    #: eligible live evidence. Stated plainly rather than shown as an empty list,
    #: which reads as "nothing is happening" instead of "we could not look".
    insufficient_live_evidence: bool = False
    detail: str | None = None
    rejections: list[RejectionOut] = Field(default_factory=list)


class DecisionIn(BaseModel):
    interest: UserInterest
    note: str | None = Field(default=None, max_length=4000)
