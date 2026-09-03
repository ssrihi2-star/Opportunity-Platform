from __future__ import annotations

import dataclasses
import uuid
from datetime import UTC, datetime

import sqlalchemy as sa
from fastapi import APIRouter, Depends, File, HTTPException, Query, UploadFile, status
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.deps import current_user, db_session, require_admin
from app.core.errors import OISError
from app.core.security import decrypt_secret, encrypt_secret, mask_secret
from app.db.base import as_utc
from app.models.models import (
    RawRecord,
    Source,
    SourceCredential,
    SourceRun,
    SystemAuditLog,
    User,
)
from app.schemas.common import Message, Page
from app.schemas.signals import RawRecordOut
from app.schemas.sources import (
    AdapterInfo,
    CredentialIn,
    CredentialOut,
    CsvUploadResult,
    RunResult,
    SourceCreate,
    SourceHealthOut,
    SourceOut,
    SourceRunOut,
    SourceUpdate,
)
from app.services.ingestion import build_fetcher, run_source
from app.sources.adapters.csv_import import parse_rows
from app.sources.registry import (
    adapter_catalogue,
    available_adapters,
    build_adapter,
    get_adapter_class,
)

router = APIRouter(tags=["sources"])

MAX_CSV_BYTES = 8 * 1024 * 1024


async def _get_source(session: AsyncSession, source_id: uuid.UUID) -> Source:
    source = await session.get(Source, source_id)
    if source is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Source not found.")
    return source


@router.get("/sources", response_model=list[SourceOut])
async def list_sources(
    _: User = Depends(current_user), session: AsyncSession = Depends(db_session)
) -> list[Source]:
    return list((await session.execute(sa.select(Source).order_by(Source.slug))).scalars().all())


@router.get("/sources/adapters", response_model=list[AdapterInfo])
async def list_adapters(_: User = Depends(current_user)) -> list[dict]:
    """What can be plugged in, what it needs, and the upstream limit it must respect."""
    return adapter_catalogue()


@router.post("/sources", response_model=SourceOut, status_code=status.HTTP_201_CREATED)
async def create_source(
    payload: SourceCreate,
    user: User = Depends(require_admin),
    session: AsyncSession = Depends(db_session),
) -> Source:
    if payload.adapter_key not in available_adapters():
        raise HTTPException(
            status.HTTP_422_UNPROCESSABLE_ENTITY,
            f"Unknown adapter {payload.adapter_key!r}. Available: {available_adapters()}.",
        )
    exists = (
        await session.execute(sa.select(Source.id).where(Source.slug == payload.slug))
    ).scalar_one_or_none()
    if exists:
        raise HTTPException(status.HTTP_409_CONFLICT, f"Source {payload.slug!r} already exists.")

    source = Source(**payload.model_dump())
    session.add(source)
    await session.flush()
    session.add(
        SystemAuditLog(
            actor_user_id=user.id,
            actor_label=user.email,
            action="source.create",
            object_type="source",
            object_id=str(source.id),
            after={"slug": source.slug},
        )
    )
    return source


@router.patch("/sources/{source_id}", response_model=SourceOut)
async def update_source(
    source_id: uuid.UUID,
    payload: SourceUpdate,
    user: User = Depends(require_admin),
    session: AsyncSession = Depends(db_session),
) -> Source:
    source = await _get_source(session, source_id)
    before = {"enabled": source.enabled, "status": source.status}
    for key, value in payload.model_dump(exclude_unset=True).items():
        setattr(source, key, value)
    await session.flush()
    session.add(
        SystemAuditLog(
            actor_user_id=user.id,
            actor_label=user.email,
            action="source.update",
            object_type="source",
            object_id=str(source.id),
            before=before,
            after={"enabled": source.enabled, "status": source.status},
        )
    )
    return source


# ------------------------------------------------------------------- credentials
@router.get("/sources/{source_id}/credentials", response_model=list[CredentialOut])
async def list_credentials(
    source_id: uuid.UUID,
    _: User = Depends(require_admin),
    session: AsyncSession = Depends(db_session),
) -> list[CredentialOut]:
    """Never returns a secret. Only which keys exist and a short recognisable hint."""
    await _get_source(session, source_id)
    rows = (
        (await session.execute(sa.select(SourceCredential).where(SourceCredential.source_id == source_id)))
        .scalars()
        .all()
    )
    return [
        CredentialOut(key=r.key, hint=r.hint, rotated_at=r.rotated_at, created_at=r.created_at) for r in rows
    ]


@router.put("/sources/{source_id}/credentials", response_model=CredentialOut)
async def upsert_credential(
    source_id: uuid.UUID,
    payload: CredentialIn,
    user: User = Depends(require_admin),
    session: AsyncSession = Depends(db_session),
) -> CredentialOut:
    source = await _get_source(session, source_id)
    existing = (
        await session.execute(
            sa.select(SourceCredential).where(
                SourceCredential.source_id == source_id, SourceCredential.key == payload.key
            )
        )
    ).scalar_one_or_none()
    hint = mask_secret(payload.value)
    now = datetime.now(UTC)
    if existing is None:
        row = SourceCredential(
            source_id=source_id,
            key=payload.key,
            value_encrypted=encrypt_secret(payload.value),
            hint=hint,
        )
        session.add(row)
        action = "source.credential_create"
    else:
        existing.value_encrypted = encrypt_secret(payload.value)
        existing.hint = hint
        existing.rotated_at = now
        row = existing
        action = "source.credential_rotate"
    await session.flush()
    # The audit log records that a secret changed - never the secret itself.
    session.add(
        SystemAuditLog(
            actor_user_id=user.id,
            actor_label=user.email,
            action=action,
            object_type="source_credential",
            object_id=str(row.id),
            after={"source": source.slug, "key": payload.key, "hint": hint},
        )
    )
    return CredentialOut(key=row.key, hint=row.hint, rotated_at=row.rotated_at, created_at=row.created_at)


@router.delete("/sources/{source_id}/credentials/{key}", response_model=Message)
async def delete_credential(
    source_id: uuid.UUID,
    key: str,
    user: User = Depends(require_admin),
    session: AsyncSession = Depends(db_session),
) -> Message:
    row = (
        await session.execute(
            sa.select(SourceCredential).where(
                SourceCredential.source_id == source_id, SourceCredential.key == key
            )
        )
    ).scalar_one_or_none()
    if row is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, f"No credential {key!r} on this source.")
    await session.delete(row)
    session.add(
        SystemAuditLog(
            actor_user_id=user.id,
            actor_label=user.email,
            action="source.credential_delete",
            object_type="source_credential",
            object_id=str(source_id),
            after={"key": key},
        )
    )
    return Message(detail=f"Credential {key!r} removed.")


# -------------------------------------------------------------------- csv upload
@router.post("/sources/{source_id}/upload-csv", response_model=CsvUploadResult)
async def upload_csv(
    source_id: uuid.UUID,
    file: UploadFile = File(...),
    user: User = Depends(require_admin),
    session: AsyncSession = Depends(db_session),
) -> CsvUploadResult:
    """Attach a CSV to a `csv_import` source, replacing whatever was there.

    The file is validated here, before it is stored, so a bad header is reported
    immediately instead of silently failing on the next scheduled run.
    """
    source = await _get_source(session, source_id)
    if source.adapter_key != "csv_import":
        raise HTTPException(
            status.HTTP_409_CONFLICT,
            f"Source {source.slug!r} uses the {source.adapter_key!r} adapter. "
            "CSV upload only applies to a source created with adapter_key='csv_import'.",
        )
    body = await file.read()
    if len(body) > MAX_CSV_BYTES:
        raise HTTPException(
            status.HTTP_413_REQUEST_ENTITY_TOO_LARGE,
            f"File is {len(body)} bytes; the limit is {MAX_CSV_BYTES}. Split it.",
        )
    try:
        text = body.decode("utf-8-sig")
    except UnicodeDecodeError as exc:
        raise HTTPException(
            status.HTTP_422_UNPROCESSABLE_ENTITY,
            "The file is not valid UTF-8. Re-export it as UTF-8 CSV.",
        ) from exc
    try:
        rows = parse_rows(text)
    except ValueError as exc:
        raise HTTPException(status.HTTP_422_UNPROCESSABLE_ENTITY, str(exc)) from exc

    config = dict(source.config or {})
    config["rows"] = rows
    config.pop("csv_text", None)
    source.config = config
    # The watermark is cleared so the newly uploaded rows are all considered.
    source.last_success_at = None
    await session.flush()
    session.add(
        SystemAuditLog(
            actor_user_id=user.id,
            actor_label=user.email,
            action="source.csv_upload",
            object_type="source",
            object_id=str(source.id),
            after={"filename": file.filename, "rows": len(rows)},
        )
    )
    return CsvUploadResult(
        rows_parsed=len(rows),
        columns=sorted(rows[0].keys()) if rows else [],
        stored_on_source=source.slug,
        detail=f"Stored {len(rows)} rows. Run the source to ingest them.",
    )


# --------------------------------------------------------------------- run/health
@router.post("/sources/{source_id}/run", response_model=RunResult)
async def trigger_run(
    source_id: uuid.UUID,
    user: User = Depends(require_admin),
    session: AsyncSession = Depends(db_session),
) -> RunResult:
    source = await _get_source(session, source_id)
    if not source.enabled:
        raise HTTPException(
            status.HTTP_409_CONFLICT,
            f"Source {source.slug!r} is disabled. Enable it with PATCH /sources/{source_id}.",
        )
    result = await run_source(session, source, trigger="manual")
    session.add(
        SystemAuditLog(
            actor_user_id=user.id,
            actor_label=user.email,
            action="source.run",
            object_type="source",
            object_id=str(source.id),
            after={"status": result.status},
        )
    )
    return RunResult(**dataclasses.asdict(result))


@router.get("/sources/{source_id}/health", response_model=SourceHealthOut)
async def source_health(
    source_id: uuid.UUID,
    probe: bool = Query(default=False, description="Also call the adapter's live health check."),
    _: User = Depends(current_user),
    session: AsyncSession = Depends(db_session),
) -> SourceHealthOut:
    source = await _get_source(session, source_id)
    last_run = (
        await session.execute(
            sa.select(SourceRun)
            .where(SourceRun.source_id == source.id)
            .order_by(SourceRun.started_at.desc())
            .limit(1)
        )
    ).scalar_one_or_none()
    freshness = None
    if source.last_success_at:
        freshness = round((datetime.now(UTC) - as_utc(source.last_success_at)).total_seconds() / 3600, 2)

    adapter_available = source.adapter_key in available_adapters()
    missing: list[str] = []
    requires_network = False
    documented = "unknown adapter"
    if adapter_available:
        adapter_cls = get_adapter_class(source.adapter_key)
        requires_network = adapter_cls.requires_network
        documented = adapter_cls.documented_rate_limit
        present = set(
            (
                await session.execute(
                    sa.select(SourceCredential.key).where(SourceCredential.source_id == source.id)
                )
            )
            .scalars()
            .all()
        )
        missing = [k for k in adapter_cls.requires_credentials if k not in present]

    probe_detail: str | None = None
    probe_healthy: bool | None = None
    if probe and adapter_available:
        try:
            creds = {
                r.key: decrypt_secret(r.value_encrypted)
                for r in (
                    await session.execute(
                        sa.select(SourceCredential).where(SourceCredential.source_id == source.id)
                    )
                )
                .scalars()
                .all()
            }
            adapter = build_adapter(
                source.adapter_key,
                config=source.config,
                credentials=creds,
                fetcher=build_fetcher(source),
            )
            health = await adapter.health_check()
            probe_detail, probe_healthy = health.detail, health.healthy
        except OISError as exc:
            probe_detail, probe_healthy = f"{type(exc).__name__}: {exc}", False
        except Exception as exc:  # noqa: BLE001 - a probe must never 500 the page
            probe_detail, probe_healthy = f"Unexpected {type(exc).__name__}: {exc}", False

    return SourceHealthOut(
        source_id=source.id,
        slug=source.slug,
        enabled=source.enabled,
        status=source.status,
        reliability=source.reliability,
        consecutive_failures=source.consecutive_failures,
        last_success_at=source.last_success_at,
        freshness_hours=freshness,
        adapter_available=adapter_available,
        requires_network=requires_network,
        missing_credentials=missing,
        documented_rate_limit=documented,
        probe=probe_detail,
        probe_healthy=probe_healthy,
        last_run=SourceRunOut.model_validate(last_run) if last_run else None,
    )


@router.get("/sources/{source_id}/runs", response_model=list[SourceRunOut])
async def source_runs(
    source_id: uuid.UUID,
    limit: int = Query(default=20, ge=1, le=200),
    _: User = Depends(current_user),
    session: AsyncSession = Depends(db_session),
) -> list[SourceRun]:
    stmt = (
        sa.select(SourceRun)
        .where(SourceRun.source_id == source_id)
        .order_by(SourceRun.started_at.desc())
        .limit(limit)
    )
    return list((await session.execute(stmt)).scalars().all())


@router.get("/raw-records", response_model=Page[RawRecordOut])
async def list_raw_records(
    source_id: uuid.UUID | None = None,
    limit: int = Query(default=50, ge=1, le=200),
    offset: int = Query(default=0, ge=0),
    _: User = Depends(current_user),
    session: AsyncSession = Depends(db_session),
) -> Page[RawRecordOut]:
    where = [RawRecord.source_id == source_id] if source_id else []
    total = (await session.execute(sa.select(sa.func.count(RawRecord.id)).where(*where))).scalar_one()
    rows = (
        (
            await session.execute(
                sa.select(RawRecord)
                .where(*where)
                .order_by(RawRecord.fetched_at.desc())
                .limit(limit)
                .offset(offset)
            )
        )
        .scalars()
        .all()
    )
    return Page[RawRecordOut](
        items=[RawRecordOut.model_validate(r) for r in rows],
        total=int(total),
        limit=limit,
        offset=offset,
    )
