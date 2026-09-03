"""The single interface every data source must implement."""

from __future__ import annotations

import hashlib
import json
from abc import ABC, abstractmethod
from dataclasses import asdict, dataclass, field
from datetime import UTC, datetime
from typing import Any, Protocol, runtime_checkable

from app.core.errors import SourceConfigError
from app.sources.http import Fetcher


@dataclass(slots=True)
class RawSignal:
    """Normalised envelope returned by every adapter.

    `payload` keeps the verbatim source object so nothing is lost for auditing.
    """

    external_id: str
    title: str | None = None
    content: str | None = None
    url: str | None = None
    published_at: datetime | None = None
    fetched_at: datetime = field(default_factory=lambda: datetime.now(UTC))
    metric_name: str | None = None
    metric_value: float | None = None
    metric_unit: str | None = None
    #: ISO-4217 code when the value is money. Never converted on the way in.
    metric_currency: str | None = None
    #: "ok" | "missing" | "failed". A period the source cannot report is recorded
    #: as a gap, because a gap and a zero mean opposite things.
    status: str = "ok"
    previous_value: float | None = None
    entity_name: str | None = None
    entity_type: str | None = None
    #: Official identifiers for the entity, e.g. {"sec_cik": "...", "ticker": "..."}.
    #: These are what makes a merge safe; a matching name never is.
    entity_external_ids: dict[str, Any] = field(default_factory=dict)
    signal_type: str | None = None
    signal_class: str | None = None
    is_proxy: bool = False
    geo_scope: str = "global"
    confidence: float = 0.5
    payload: dict[str, Any] = field(default_factory=dict)

    def content_hash(self) -> str:
        """Stable fingerprint used for deduplication.

        Deliberately excludes `fetched_at` so re-fetching the same item is a no-op.
        """
        basis = json.dumps(
            {
                "external_id": self.external_id,
                "title": self.title,
                "url": self.url,
                "metric_name": self.metric_name,
                "metric_value": self.metric_value,
                "status": self.status,
                "published_at": self.published_at.isoformat() if self.published_at else None,
            },
            sort_keys=True,
            default=str,
        )
        return hashlib.sha256(basis.encode()).hexdigest()

    def as_dict(self) -> dict[str, Any]:
        return asdict(self)


@dataclass(slots=True)
class SourceHealth:
    healthy: bool
    detail: str
    checked_at: datetime = field(default_factory=lambda: datetime.now(UTC))
    latency_ms: int | None = None
    quota_remaining: int | None = None


@runtime_checkable
class DataSource(Protocol):
    async def fetch(self, since: datetime | None = None) -> list[RawSignal]: ...
    async def health_check(self) -> SourceHealth: ...


class BaseDataSource(ABC):
    """Convenience base with config plumbing. Adapters subclass this."""

    adapter_key: str = "base"
    requires_credentials: tuple[str, ...] = ()
    requires_network: bool = False
    #: robots.txt governs crawlers. Documented JSON APIs with published terms are
    #: exempt; anything that reads ordinary web pages or feeds is not.
    respect_robots: bool = True
    #: Documented upstream limit, surfaced in the UI so the operator can see it.
    documented_rate_limit: str = "not documented"
    default_rate_limit_per_minute: int = 30
    default_source_class: str = "primary_api"

    def __init__(
        self,
        config: dict[str, Any] | None = None,
        credentials: dict[str, str] | None = None,
        fetcher: Fetcher | None = None,
    ) -> None:
        self.config = config or {}
        self.credentials = credentials or {}
        self._fetcher = fetcher
        missing = [k for k in self.requires_credentials if not self.credentials.get(k)]
        if missing:
            raise SourceConfigError(
                f"Source adapter {self.adapter_key!r} is missing credential(s): "
                f"{', '.join(missing)}. Add them with "
                f"POST /api/v1/sources/{{id}}/credentials."
            )
        if self.requires_network and fetcher is None:
            raise SourceConfigError(
                f"Source adapter {self.adapter_key!r} needs network access but no HTTP fetcher "
                "was supplied. This usually means it was constructed outside the ingestion "
                "pipeline; pass fetcher=Fetcher(...) explicitly."
            )

    @property
    def fetcher(self) -> Fetcher:
        if self._fetcher is None:
            raise SourceConfigError(
                f"Adapter {self.adapter_key!r} tried to make an HTTP request without a fetcher."
            )
        return self._fetcher

    def cfg(self, key: str, default: Any = None, *, required: bool = False) -> Any:
        value = self.config.get(key, default)
        if required and value in (None, "", [], {}):
            raise SourceConfigError(
                f"Source {self.adapter_key!r} requires config key {key!r}. "
                f"Set it with PATCH /api/v1/sources/{{id}} in the `config` object."
            )
        return value

    @abstractmethod
    async def fetch(self, since: datetime | None = None) -> list[RawSignal]:
        """Return new records since `since`. Must be safe to retry."""

    async def health_check(self) -> SourceHealth:
        return SourceHealth(healthy=True, detail="No health check implemented for this adapter.")
