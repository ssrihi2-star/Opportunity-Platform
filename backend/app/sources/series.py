"""Helpers shared by adapters that emit a dated numeric series."""

from __future__ import annotations

from collections.abc import Iterable
from datetime import UTC, datetime

from app.analytics.signal_types import class_of, is_proxy_signal
from app.sources.base import RawSignal


def to_utc(value: datetime | None) -> datetime | None:
    if value is None:
        return None
    return value.replace(tzinfo=UTC) if value.tzinfo is None else value.astimezone(UTC)


def build_series(
    *,
    points: Iterable[tuple[datetime, float]],
    entity_name: str,
    entity_type: str,
    signal_type: str,
    source_prefix: str,
    unit: str | None = None,
    geo_scope: str = "global",
    confidence: float = 0.7,
    url: str | None = None,
    payload_extra: dict | None = None,
    is_proxy: bool | None = None,
    currency: str | None = None,
    external_ids: dict | None = None,
) -> list[RawSignal]:
    """Turn dated values into RawSignals, chaining `previous_value` in date order.

    Sorting first matters: `previous_value` is what the ingestion layer uses to
    compute period-over-period change, and an unsorted upstream response would
    otherwise produce nonsense deltas.
    """
    ordered = sorted(((to_utc(d), v) for d, v in points if d is not None), key=lambda p: p[0])
    proxy = is_proxy_signal(signal_type) if is_proxy is None else is_proxy
    out: list[RawSignal] = []
    previous: float | None = None
    for observed_at, value in ordered:
        out.append(
            RawSignal(
                external_id=f"{source_prefix}|{entity_name}|{signal_type}|{observed_at.date().isoformat()}",
                title=f"{entity_name} - {signal_type} on {observed_at.date().isoformat()}",
                content=f"{signal_type} for {entity_name}: {value} {unit or ''}".strip(),
                url=url,
                published_at=observed_at,
                metric_name=signal_type,
                metric_value=float(value),
                metric_unit=unit,
                metric_currency=currency,
                previous_value=previous,
                entity_name=entity_name,
                entity_type=entity_type,
                entity_external_ids=dict(external_ids or {}),
                signal_type=signal_type,
                signal_class=class_of(signal_type),
                is_proxy=proxy,
                geo_scope=geo_scope,
                confidence=confidence,
                payload={"value": value, "observed_at": observed_at.isoformat(), **(payload_extra or {})},
            )
        )
        previous = float(value)
    return out


def daily_counts(timestamps: Iterable[datetime]) -> list[tuple[datetime, float]]:
    """Bucket timestamps into UTC day counts. Days with no items are simply absent."""
    buckets: dict[datetime, int] = {}
    for ts in timestamps:
        utc = to_utc(ts)
        if utc is None:
            continue
        day = utc.replace(hour=0, minute=0, second=0, microsecond=0)
        buckets[day] = buckets.get(day, 0) + 1
    return sorted(((day, float(count)) for day, count in buckets.items()), key=lambda p: p[0])


def build_gap(
    *,
    observed_at: datetime,
    entity_name: str,
    entity_type: str,
    signal_type: str,
    source_prefix: str,
    reason: str,
    status: str = "missing",
    geo_scope: str = "global",
    unit: str | None = None,
    external_ids: dict | None = None,
) -> RawSignal:
    """Record that a period has no value, rather than pretending it has zero.

    A skipped row is invisible to the confidence calculation; a recorded gap is
    not, and that difference is the point.
    """
    day = to_utc(observed_at)
    assert day is not None
    return RawSignal(
        external_id=f"{source_prefix}|{entity_name}|{signal_type}|{day.date().isoformat()}|gap",
        title=f"{entity_name} - {signal_type} not reported on {day.date().isoformat()}",
        content=reason,
        published_at=day,
        metric_name=signal_type,
        metric_value=None,
        metric_unit=unit,
        status=status,
        entity_name=entity_name,
        entity_type=entity_type,
        entity_external_ids=dict(external_ids or {}),
        signal_type=signal_type,
        signal_class=class_of(signal_type),
        geo_scope=geo_scope,
        confidence=0.0,
        payload={"gap_reason": reason, "status": status},
    )
