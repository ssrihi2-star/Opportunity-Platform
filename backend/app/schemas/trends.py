from __future__ import annotations

import uuid
from datetime import datetime
from typing import Any

from pydantic import BaseModel, ConfigDict, Field


class TrendOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: uuid.UUID
    subject_type: str
    entity_id: uuid.UUID | None
    topic_id: uuid.UUID | None
    name: str
    category: str | None
    geo_scope: str
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
    detail: str


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
    entity_names: list[str] = Field(default_factory=list)


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
