from __future__ import annotations

import sqlalchemy as sa
from fastapi import APIRouter, Depends
from fastapi.responses import PlainTextResponse
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.deps import db_session, require_admin
from app.core.config import settings
from app.models.models import ModelRun, RawRecord, SignalObservation, SourceRun, User

router = APIRouter(tags=["ops"])


@router.get("/health")
async def health() -> dict:
    return {"status": "ok", "env": settings.ENV, "app": settings.APP_NAME}


@router.get("/health/ready")
async def ready(session: AsyncSession = Depends(db_session)) -> dict:
    checks: dict[str, str] = {}
    try:
        await session.execute(sa.text("SELECT 1"))
        checks["database"] = "ok"
    except Exception as exc:  # noqa: BLE001
        checks["database"] = f"error: {exc}"

    if settings.ENV == "test" or not settings.REDIS_URL:
        checks["redis"] = "skipped (test environment or no REDIS_URL)"
    else:
        try:
            import redis.asyncio as redis

            client = redis.from_url(settings.REDIS_URL)
            await client.ping()
            checks["redis"] = "ok"
        except Exception as exc:  # noqa: BLE001
            checks["redis"] = f"error: {exc}"

    ok = all(v == "ok" or v.startswith("skipped") for v in checks.values())
    return {"status": "ok" if ok else "degraded", "checks": checks}


@router.get("/metrics", response_class=PlainTextResponse)
async def metrics(_: User = Depends(require_admin), session: AsyncSession = Depends(db_session)) -> str:
    async def count(model) -> int:
        return int((await session.execute(sa.select(sa.func.count(model.id)))).scalar_one())

    ai_cost = float(
        (await session.execute(sa.select(sa.func.coalesce(sa.func.sum(ModelRun.cost_usd), 0.0)))).scalar_one()
    )
    http_requests = int(
        (
            await session.execute(sa.select(sa.func.coalesce(sa.func.sum(SourceRun.http_requests), 0)))
        ).scalar_one()
    )
    failed_runs = int(
        (
            await session.execute(sa.select(sa.func.count(SourceRun.id)).where(SourceRun.status == "failed"))
        ).scalar_one()
    )
    lines = [
        "# HELP ois_raw_records_total Raw records stored",
        "# TYPE ois_raw_records_total counter",
        f"ois_raw_records_total {await count(RawRecord)}",
        "# HELP ois_signal_observations_total Signal observations stored",
        "# TYPE ois_signal_observations_total counter",
        f"ois_signal_observations_total {await count(SignalObservation)}",
        "# HELP ois_source_runs_total Source runs recorded",
        "# TYPE ois_source_runs_total counter",
        f"ois_source_runs_total {await count(SourceRun)}",
        "# HELP ois_source_runs_failed_total Source runs that failed",
        "# TYPE ois_source_runs_failed_total counter",
        f"ois_source_runs_failed_total {failed_runs}",
        "# HELP ois_http_requests_total Outbound HTTP requests made by collectors",
        "# TYPE ois_http_requests_total counter",
        f"ois_http_requests_total {http_requests}",
        "# HELP ois_ai_cost_usd_total Cumulative AI spend",
        "# TYPE ois_ai_cost_usd_total counter",
        f"ois_ai_cost_usd_total {ai_cost}",
    ]
    return "\n".join(lines) + "\n"
