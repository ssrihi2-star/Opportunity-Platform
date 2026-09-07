from __future__ import annotations

import uuid
from datetime import datetime
from typing import Any

from pydantic import BaseModel, ConfigDict, Field, model_validator, field_validator


class TrendOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: uuid.UUID
    subject_type: str
    entity_id: uuid.UUID | None
    topic_id: uuid.UUID | None
    name: str
    category: str | None
    geo_scope: str
    #: Which evidence this evaluation was computed from — "live_only" or
    #: "demo_inclusive". Every number on the row is only true for that mode.
    analysis_mode: str
    state: str
    stage: str
    trend_score: float
    confidence: float
    peak_score: float
    peak_score_at: datetime | None
    first_detected_at: datetime
    last_evaluated_at: datetime
    last_confirmation_at: datetime | None
    independent_source_count: int
    distinct_signal_types: int
    observation_count: int
    missing_observation_count: int
    history_days: int
    is_spike: bool
    is_seasonal: bool
    warnings: list[str]


class TrendSignalOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    signal_id: uuid.UUID
    source_id: uuid.UUID
    source_slug: str | None = None
    #: True when this series came from a source that actually contacts a live
    #: upstream. Displayed provenance uses the same rule the engine filtered on,
    #: so the list under a live-only trend cannot disagree with its score.
    is_live_source: bool = False
    source_group: str
    signal_type: str
    growth_30d: float | None
    acceleration: float | None
    observation_count: int
    is_proxy: bool
    contribution: float


class TrendPoint(BaseModel):
    at: datetime
    value: float | None
    status: str
    signal_type: str
    source_slug: str | None = None
    unit: str | None = None
    currency: str | None = None


class SnapshotOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    evaluated_at: datetime
    trend_score: float
    confidence: float
    stage: str
    state: str


class EvidenceEvent(BaseModel):
    at: datetime
    kind: str
    detail: str
    url: str | None = None
    source_slug: str | None = None


class TrendDetailOut(TrendOut):
    components: dict[str, Any]
    penalties: dict[str, float]
    metrics: dict[str, Any]
    explanation: str | None
    signals: list[TrendSignalOut]
    series: list[TrendPoint]
    history: list[SnapshotOut]
    evidence: list[EvidenceEvent]
    related_entities: list[dict]


class EvaluateResult(BaseModel):
    evaluated: int
    created: int
    topics: int
    analysis_mode: str = "demo_inclusive"
    detail: str


class TopicEntityOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    entity_id: uuid.UUID
    entity_name: str
    entity_type: str
    weight: float
    is_manual: bool
    justification: str | None


class TopicOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: uuid.UUID
    label: str
    description: str | None
    category: str | None
    keywords: list[str]
    first_seen_at: datetime | None
    last_seen_at: datetime | None
    label_is_ai_generated: bool
    entities: list[TopicEntityOut] = Field(default_factory=list)

    @model_validator(mode="before")
    @classmethod
    def _backfill_entity_names(cls, data: Any) -> Any:
        """Accept the old ``entity_names`` payload during the transition."""
        if isinstance(data, dict) and "entities" not in data and "entity_names" in data:
            data["entities"] = [
                {
                    "entity_id": None,
                    "entity_name": name,
                    "entity_type": "unknown",
                    "weight": 1.0,
                    "is_manual": False,
                    "justification": None,
                }
                for name in data["entity_names"]
            ]
        return data


class MatchCandidateOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: uuid.UUID
    observed_name: str
    entity_type: str
    candidate_entity_id: uuid.UUID
    candidate_entity_name: str | None = None
    candidate_external_ids: dict[str, Any] = Field(default_factory=dict)
    created_entity_id: uuid.UUID | None
    confidence: float
    reason: str
    decision: str
    decided_at: datetime | None
    note: str | None
    created_at: datetime


class DecisionIn(BaseModel):
    decision: str = Field(pattern="^(confirmed|rejected|keep_separate)$")
    note: str | None = Field(default=None, max_length=1000)


class TopicMembershipIn(BaseModel):
    entity_id: uuid.UUID
    justification: str = Field(min_length=1, max_length=1000)

    @field_validator("justification")
    @classmethod
    def strip_and_validate(cls, v: str) -> str:
        """Strip whitespace and ensure non-empty after stripping."""
        stripped = v.strip()
        if not stripped:
            raise ValueError("justification cannot be blank")
        return stripped
