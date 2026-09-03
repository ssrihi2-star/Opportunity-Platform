from __future__ import annotations

import uuid
from datetime import datetime
from typing import Any

from pydantic import BaseModel, ConfigDict, Field


class AdapterInfo(BaseModel):
    adapter_key: str
    requires_network: bool
    requires_credentials: list[str]
    documented_rate_limit: str
    default_rate_limit_per_minute: int
    default_source_class: str
    doc: str


class SourceOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: uuid.UUID
    slug: str
    name: str
    adapter_key: str
    source_group: str
    source_class: str
    status: str
    enabled: bool
    schedule_cron: str
    rate_limit_per_minute: int
    reliability: float
    consecutive_failures: int
    last_run_at: datetime | None
    last_success_at: datetime | None
    notes: str | None
    config: dict[str, Any]


class SourceCreate(BaseModel):
    slug: str = Field(pattern=r"^[a-z0-9_\-]{2,80}$")
    name: str = Field(min_length=2, max_length=200)
    adapter_key: str
    source_group: str = "independent"
    source_class: str = "primary_api"
    schedule_cron: str = "0 3 * * *"
    rate_limit_per_minute: int = Field(default=30, ge=1, le=6000)
    reliability: float = Field(default=0.5, ge=0, le=1)
    enabled: bool = True
    config: dict[str, Any] = Field(default_factory=dict)
    notes: str | None = None


class SourceUpdate(BaseModel):
    name: str | None = None
    enabled: bool | None = None
    status: str | None = Field(default=None, pattern="^(active|disabled|error)$")
    schedule_cron: str | None = None
    rate_limit_per_minute: int | None = Field(default=None, ge=1, le=6000)
    reliability: float | None = Field(default=None, ge=0, le=1)
    source_group: str | None = None
    config: dict[str, Any] | None = None
    notes: str | None = None


class CredentialIn(BaseModel):
    key: str = Field(pattern=r"^[a-z0-9_]{2,60}$")
    value: str = Field(min_length=1, max_length=4000)


class CredentialOut(BaseModel):
    key: str
    hint: str | None
    rotated_at: datetime | None
    created_at: datetime


class SourceRunOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: uuid.UUID
    status: str
    trigger: str
    started_at: datetime
    finished_at: datetime | None
    records_fetched: int
    records_stored: int
    records_duplicate: int
    records_rejected: int
    observations_written: int
    http_requests: int
    error: str | None
    duration_ms: int | None


class SourceHealthOut(BaseModel):
    source_id: uuid.UUID
    slug: str
    enabled: bool
    status: str
    reliability: float
    consecutive_failures: int
    last_success_at: datetime | None
    freshness_hours: float | None
    adapter_available: bool
    requires_network: bool
    missing_credentials: list[str]
    documented_rate_limit: str
    probe: str | None = None
    probe_healthy: bool | None = None
    last_run: SourceRunOut | None


class RunResult(BaseModel):
    run_id: str
    status: str
    fetched: int
    stored: int
    duplicates: int
    rejected: int
    observations: int
    http_requests: int = 0
    not_modified: int = 0
    error: str | None = None
    injection_flags: int = 0


class CsvUploadResult(BaseModel):
    rows_parsed: int
    columns: list[str]
    stored_on_source: str
    detail: str
