"""Background tasks. Every task is idempotent and safe to retry."""

from __future__ import annotations

import asyncio
import dataclasses
import uuid

import sqlalchemy as sa

from app.core.logging import get_logger
from app.db.session import SessionLocal
from app.models.models import Source
from app.services.ingestion import reap_stale_runs, run_source
from app.workers.celery_app import celery_app

log = get_logger("worker")


def _run(coro):
    return asyncio.run(coro)


async def _run_source_by_id(source_id: uuid.UUID) -> dict:
    async with SessionLocal() as session:
        source = await session.get(Source, source_id)
        if source is None:
            return {"error": f"Source {source_id} no longer exists."}
        result = await run_source(session, source, trigger="scheduled")
        await session.commit()
        return dataclasses.asdict(result)


@celery_app.task(name="ois.run_source", bind=True, max_retries=3, default_retry_delay=60)
def task_run_source(self, source_id: str) -> dict:  # noqa: ANN001
    try:
        return _run(_run_source_by_id(uuid.UUID(source_id)))
    except Exception as exc:  # noqa: BLE001
        log.error("task_run_source_failed", source_id=source_id, error=str(exc))
        raise self.retry(exc=exc) from exc


async def _run_all() -> list[dict]:
    async with SessionLocal() as session:
        sources = (await session.execute(sa.select(Source).where(Source.enabled.is_(True)))).scalars().all()
        results = []
        for source in sources:
            # A failing source must not stop the others.
            try:
                result = await run_source(session, source, trigger="scheduled")
                results.append(dataclasses.asdict(result))
            except Exception as exc:  # noqa: BLE001
                log.error("source_failed", slug=source.slug, error=str(exc))
                results.append({"source": source.slug, "error": str(exc)})
            await session.commit()
        return results


@celery_app.task(name="ois.run_all_sources")
def task_run_all_sources() -> list[dict]:
    return _run(_run_all())


async def _reap() -> int:
    async with SessionLocal() as session:
        count = await reap_stale_runs(session)
        await session.commit()
        return count


@celery_app.task(name="ois.reap_stale_runs")
def task_reap_stale_runs() -> int:
    return _run(_reap())
