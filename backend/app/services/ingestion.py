"""The ingestion pipeline.

fetch -> validate -> normalise -> dedup -> store raw -> extract entities ->
build signals -> write observations.

Every step is idempotent: running the same collection twice stores nothing new.
Partial source failures are recorded, not swallowed.
"""

from __future__ import annotations

import time
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta

import sqlalchemy as sa
from sqlalchemy.ext.asyncio import AsyncSession

from app.analytics.dedup import title_fingerprint
from app.analytics.signal_types import class_of
from app.core.config import settings
from app.core.errors import (
    PartialFetchError,
    RateLimitedError,
    RobotsDisallowedError,
    SourceConfigError,
    SourceUnavailableError,
    SSRFBlockedError,
)
from app.core.logging import get_logger
from app.core.sanitize import sanitize_external_text
from app.core.security import decrypt_secret
from app.db.base import as_utc
from app.models.enums import ObservationStatus, RunStatus
from app.models.models import (
    HttpCacheEntry,
    RawRecord,
    Signal,
    SignalObservation,
    Source,
    SourceCredential,
    SourceRun,
    SystemAuditLog,
)
from app.services.entities import resolve_or_create_entity
from app.sources.base import RawSignal
from app.sources.http import ConditionalEntry, Fetcher, Transport, url_hash
from app.sources.registry import build_adapter, get_adapter_class

log = get_logger("ingestion")


@dataclass(slots=True)
class IngestionResult:
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


async def _load_credentials(session: AsyncSession, source: Source) -> dict[str, str]:
    rows = (
        (await session.execute(sa.select(SourceCredential).where(SourceCredential.source_id == source.id)))
        .scalars()
        .all()
    )
    return {r.key: decrypt_secret(r.value_encrypted) for r in rows}


async def _load_conditional(session: AsyncSession, source: Source) -> dict[str, ConditionalEntry]:
    rows = (
        (await session.execute(sa.select(HttpCacheEntry).where(HttpCacheEntry.source_id == source.id)))
        .scalars()
        .all()
    )
    return {r.url_hash: ConditionalEntry(etag=r.etag, last_modified=r.last_modified) for r in rows}


async def _save_conditional(
    session: AsyncSession, source: Source, touched: dict[str, ConditionalEntry]
) -> None:
    """Persist ETag / Last-Modified so the next run can ask 'has this changed?'."""
    now = datetime.now(UTC)
    for url, entry in touched.items():
        key = url_hash(url)
        existing = (
            await session.execute(
                sa.select(HttpCacheEntry).where(
                    HttpCacheEntry.source_id == source.id, HttpCacheEntry.url_hash == key
                )
            )
        ).scalar_one_or_none()
        if existing is None:
            session.add(
                HttpCacheEntry(
                    source_id=source.id,
                    url_hash=key,
                    url=url[:2000],
                    etag=entry.etag,
                    last_modified=entry.last_modified,
                    fetched_at=now,
                )
            )
        else:
            existing.etag = entry.etag
            existing.last_modified = entry.last_modified
            existing.fetched_at = now


def build_fetcher(
    source: Source, transport: Transport | None = None, conditional: dict[str, ConditionalEntry] | None = None
) -> Fetcher | None:
    """Create the HTTP policy object for a source, or None if it needs no network."""
    adapter_cls = get_adapter_class(source.adapter_key)
    if not adapter_cls.requires_network:
        return None
    return Fetcher(
        source_slug=source.slug,
        transport=transport,
        rate_limit_per_minute=source.rate_limit_per_minute or adapter_cls.default_rate_limit_per_minute,
        respect_robots=adapter_cls.respect_robots,
        conditional=conditional,
        max_requests=int((source.config or {}).get("max_requests_per_run", 500)),
    )


def _validate(record: RawSignal) -> str | None:
    """Return a reason string when the record must be dropped, else None."""
    if not record.external_id:
        return "missing external_id"
    if record.status not in {ObservationStatus.OK, ObservationStatus.MISSING, ObservationStatus.FAILED}:
        return f"unknown observation status {record.status!r}"
    is_gap = record.status != ObservationStatus.OK
    if is_gap and (record.signal_type is None or record.entity_name is None):
        return "a gap must still say which series and entity it belongs to"
    if is_gap and record.metric_value is not None:
        return "a gap cannot carry a value"
    if record.metric_value is not None and not isinstance(record.metric_value, (int, float)):
        return "metric_value is not numeric"
    if record.metric_value is not None and record.signal_type is None:
        return "metric present without a signal_type"
    if record.metric_value is not None and record.entity_name is None:
        return "metric present without an entity_name"
    if record.metric_value is not None and (
        record.metric_value != record.metric_value  # NaN
        or record.metric_value in (float("inf"), float("-inf"))
    ):
        return "metric_value is not finite"
    if record.metric_currency and len(record.metric_currency) != 3:
        return f"currency {record.metric_currency!r} is not a 3-letter ISO-4217 code"
    return None


async def _store_raw(
    session: AsyncSession, source: Source, run: SourceRun, record: RawSignal
) -> tuple[RawRecord | None, bool, int]:
    """Insert a raw record. Returns (row, was_duplicate, injection_flag_count)."""
    content_hash = record.content_hash()
    existing = (
        await session.execute(
            sa.select(RawRecord).where(
                RawRecord.source_id == source.id, RawRecord.content_hash == content_hash
            )
        )
    ).scalar_one_or_none()
    if existing is not None:
        return existing, True, 0

    sanitized_content = sanitize_external_text(record.content)
    sanitized_title = sanitize_external_text(record.title, max_chars=500)
    flags = sanitized_content.neutralised + sanitized_title.neutralised

    row = RawRecord(
        source_id=source.id,
        source_run_id=run.id,
        external_id=record.external_id[:400],
        content_hash=content_hash,
        url=record.url,
        title=sanitized_title.text or None,
        content=sanitized_content.text or None,
        content_raw=record.content,
        sanitizer_flags=flags,
        title_fingerprint=title_fingerprint(record.title),
        payload=record.payload,
        geo_scope=record.geo_scope,
        published_at=as_utc(record.published_at),
        fetched_at=as_utc(record.fetched_at) or datetime.now(UTC),
    )
    session.add(row)
    await session.flush()

    if flags:
        session.add(
            SystemAuditLog(
                actor_label=f"source:{source.slug}",
                action="sanitizer.neutralised",
                object_type="raw_record",
                object_id=str(row.id),
                after={"flags": flags, "url": record.url},
            )
        )
    return row, False, len(flags)


async def _get_or_create_signal(session: AsyncSession, source: Source, record: RawSignal) -> Signal | None:
    if record.signal_type is None or record.entity_name is None:
        return None
    entity = await resolve_or_create_entity(
        session,
        record.entity_name,
        record.entity_type or "keyword",
        external_ids=record.entity_external_ids,
        source_id=source.id,
    )
    signal = (
        await session.execute(
            sa.select(Signal).where(
                Signal.entity_id == entity.id,
                Signal.signal_type == record.signal_type,
                Signal.geo_scope == record.geo_scope,
                Signal.source_id == source.id,
            )
        )
    ).scalar_one_or_none()
    if signal is not None:
        return signal
    signal = Signal(
        entity_id=entity.id,
        signal_type=record.signal_type,
        signal_class=record.signal_class or class_of(record.signal_type),
        geo_scope=record.geo_scope,
        source_id=source.id,
        unit=record.metric_unit,
        is_proxy=record.is_proxy,
        description=f"{record.signal_type} for {entity.canonical_name} from {source.slug}",
    )
    session.add(signal)
    await session.flush()
    return signal


async def _store_observation(
    session: AsyncSession, signal: Signal, record: RawSignal, raw_row: RawRecord, source: Source
) -> bool:
    observed_at = as_utc(record.published_at or record.fetched_at)
    existing = (
        await session.execute(
            sa.select(SignalObservation.id).where(
                SignalObservation.signal_id == signal.id,
                SignalObservation.observed_at == observed_at,
            )
        )
    ).scalar_one_or_none()
    if existing is not None:
        return False

    pct = None
    if record.previous_value not in (None, 0) and record.metric_value is not None:
        pct = (record.metric_value - record.previous_value) / abs(record.previous_value) * 100.0

    session.add(
        SignalObservation(
            signal_id=signal.id,
            raw_record_id=raw_row.id,
            observed_at=observed_at,
            period_start=as_utc(record.published_at),
            period_end=as_utc(record.published_at),
            # A gap stores NULL, never 0.0: "we do not know" and "it was zero" are
            # different facts and the trend engine must be able to tell them apart.
            value=float(record.metric_value) if record.metric_value is not None else None,
            status=record.status,
            currency=(record.metric_currency or None),
            previous_value=record.previous_value,
            pct_change=pct,
            confidence=record.confidence,
            source_reliability=source.reliability,
            method="direct",
            is_proxy=record.is_proxy,
            collected_at=as_utc(record.fetched_at) or datetime.now(UTC),
        )
    )
    return True


async def run_source(
    session: AsyncSession,
    source: Source,
    *,
    trigger: str = "scheduled",
    transport: Transport | None = None,
) -> IngestionResult:
    """Collect one source end to end. Safe to retry; safe to run concurrently."""
    started = datetime.now(UTC)
    t0 = time.monotonic()
    run = SourceRun(source_id=source.id, status=RunStatus.RUNNING, trigger=trigger, started_at=started)
    session.add(run)
    await session.flush()

    fetched: list[RawSignal] = []
    status = RunStatus.SUCCEEDED
    error: str | None = None
    fetcher: Fetcher | None = None

    try:
        credentials = await _load_credentials(session, source)
        conditional = await _load_conditional(session, source)
        fetcher = build_fetcher(source, transport=transport, conditional=conditional)
        adapter = build_adapter(
            source.adapter_key, config=source.config, credentials=credentials, fetcher=fetcher
        )
        fetched = await adapter.fetch(since=as_utc(source.last_success_at))
    except PartialFetchError as exc:
        fetched, status, error = exc.records, RunStatus.PARTIAL, str(exc)
    except RateLimitedError as exc:
        status, error = RunStatus.PARTIAL, f"Rate limited: {exc}"
    except RobotsDisallowedError as exc:
        status, error = RunStatus.FAILED, f"Blocked by robots.txt: {exc}"
    except (SourceConfigError, SSRFBlockedError) as exc:
        status, error = RunStatus.FAILED, f"{type(exc).__name__}: {exc}"
    except (SourceUnavailableError, ValueError, KeyError) as exc:
        status, error = RunStatus.FAILED, f"{type(exc).__name__}: {exc}"
    except Exception as exc:  # noqa: BLE001 - one bad source must not kill the worker
        status, error = RunStatus.FAILED, f"Unexpected {type(exc).__name__}: {exc}"
        log.error("source_run_failed", source=source.slug, error=str(exc))

    if len(fetched) > settings.MAX_RECORDS_PER_RUN:
        error = (
            (error + " | " if error else "") + f"Adapter returned {len(fetched)} records; truncated to "
            f"{settings.MAX_RECORDS_PER_RUN} (MAX_RECORDS_PER_RUN)."
        )
        fetched = fetched[: settings.MAX_RECORDS_PER_RUN]
        status = RunStatus.PARTIAL if status == RunStatus.SUCCEEDED else status

    stored = duplicates = observations = injections = rejected = 0
    for record in fetched:
        problem = _validate(record)
        if problem:
            rejected += 1
            log.warning("record_rejected", source=source.slug, reason=problem, external_id=record.external_id)
            continue
        raw_row, was_dup, flag_count = await _store_raw(session, source, run, record)
        injections += flag_count
        if was_dup:
            duplicates += 1
            continue
        stored += 1
        if raw_row is None:
            continue
        signal = await _get_or_create_signal(session, source, record)
        carries_measurement = record.metric_value is not None or record.status != ObservationStatus.OK
        if signal is not None and carries_measurement:
            if await _store_observation(session, signal, record, raw_row, source):
                observations += 1

    if fetcher is not None and fetcher.stats.touched_urls:
        await _save_conditional(session, source, fetcher.stats.touched_urls)

    finished = datetime.now(UTC)
    run.status = status
    run.finished_at = finished
    run.records_fetched = len(fetched)
    run.records_stored = stored
    run.records_duplicate = duplicates
    run.records_rejected = rejected
    run.observations_written = observations
    run.http_requests = fetcher.stats.requests if fetcher else 0
    run.error = error
    run.duration_ms = int((time.monotonic() - t0) * 1000)

    source.last_run_at = finished
    if status in (RunStatus.SUCCEEDED, RunStatus.PARTIAL):
        source.last_success_at = finished
        source.consecutive_failures = 0
        if source.status == "error":
            source.status = "active"
    else:
        source.consecutive_failures += 1
        if source.consecutive_failures >= 5:
            source.status = "error"

    await session.flush()
    log.info(
        "source_run_finished",
        source=source.slug,
        status=status,
        fetched=len(fetched),
        stored=stored,
        duplicates=duplicates,
        observations=observations,
        http_requests=run.http_requests,
    )
    return IngestionResult(
        run_id=str(run.id),
        status=str(status),
        fetched=len(fetched),
        stored=stored,
        duplicates=duplicates,
        rejected=rejected,
        observations=observations,
        http_requests=run.http_requests,
        not_modified=fetcher.stats.not_modified if fetcher else 0,
        error=error,
        injection_flags=injections,
    )


async def reap_stale_runs(session: AsyncSession) -> int:
    """Mark runs that never finished. Called by the janitor beat task."""
    cutoff = datetime.now(UTC) - timedelta(minutes=settings.SOURCE_RUN_STALE_MINUTES)
    result = await session.execute(
        sa.update(SourceRun)
        .where(SourceRun.status == RunStatus.RUNNING, SourceRun.started_at < cutoff)
        .values(
            status=RunStatus.STALE,
            error="Run exceeded SOURCE_RUN_STALE_MINUTES and was reaped by the janitor.",
        )
    )
    return int(result.rowcount or 0)
