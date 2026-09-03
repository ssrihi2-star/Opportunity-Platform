from __future__ import annotations

import uuid
from datetime import datetime

from pydantic import BaseModel, ConfigDict


class EntityOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: uuid.UUID
    entity_type: str
    canonical_name: str
    normalized: str
    ticker: str | None
    country: str | None


class ObservationOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: uuid.UUID
    observed_at: datetime
    value: float
    previous_value: float | None
    pct_change: float | None
    confidence: float
    source_reliability: float
    is_proxy: bool


class SignalOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: uuid.UUID
    entity_id: uuid.UUID
    entity_name: str | None = None
    source_slug: str | None = None
    signal_type: str
    signal_class: str
    geo_scope: str
    source_id: uuid.UUID
    unit: str | None
    is_proxy: bool


class SeriesStatsOut(BaseModel):
    n: int
    pct_change_last: float | None
    pct_change_window: float | None
    moving_average: float | None
    zscore_last: float | None
    ewma_last: float | None
    acceleration: float | None
    changepoint_index: int | None
    is_anomaly: bool
    direction: str
    strength: float
    note: str


class SignalDetailOut(SignalOut):
    observations: list[ObservationOut]
    stats: SeriesStatsOut


class RawRecordOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: uuid.UUID
    source_id: uuid.UUID
    external_id: str | None
    url: str | None
    title: str | None
    content: str | None
    sanitizer_flags: list[str]
    geo_scope: str
    published_at: datetime | None
    fetched_at: datetime


class OverviewOut(BaseModel):
    counts: dict[str, int]
    fastest_growing: list[dict]
    source_health: list[dict]
    recent_activity: list[dict]
    disclaimer: str
