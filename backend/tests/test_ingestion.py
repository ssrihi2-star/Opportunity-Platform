import pytest
import sqlalchemy as sa

from app.core.errors import ImmutableRowError
from app.models.models import HttpCacheEntry, RawRecord, Signal, SignalObservation, Source, SourceRun
from app.services.ingestion import build_fetcher, run_source

pytestmark = pytest.mark.asyncio


async def test_ingestion_stores_records_and_observations(session, demo_source):
    result = await run_source(session, demo_source)
    await session.commit()
    assert result.status == "succeeded"
    assert result.stored > 0
    assert result.observations > 0
    stored = (await session.execute(sa.select(sa.func.count(RawRecord.id)))).scalar_one()
    assert stored == result.stored


async def test_ingestion_is_idempotent(session, demo_source):
    first = await run_source(session, demo_source)
    await session.commit()
    demo_source.last_success_at = None  # replay the same window
    await session.commit()

    second = await run_source(session, demo_source)
    await session.commit()

    assert second.stored == 0
    assert second.duplicates == first.stored
    total_obs = (await session.execute(sa.select(sa.func.count(SignalObservation.id)))).scalar_one()
    assert total_obs == first.observations


async def test_failed_adapter_records_the_run_and_does_not_raise(session, demo_source):
    demo_source.adapter_key = "no_such_adapter"
    await session.commit()
    result = await run_source(session, demo_source)
    await session.commit()
    assert result.status == "failed"
    assert result.error and "no_such_adapter" in result.error
    run = (
        await session.execute(sa.select(SourceRun).order_by(SourceRun.started_at.desc()).limit(1))
    ).scalar_one()
    assert run.status == "failed"
    assert demo_source.consecutive_failures == 1


async def test_misconfigured_network_source_fails_clearly(session, recorded):
    source = Source(
        slug="gh-bad", name="GitHub misconfigured", adapter_key="github", config={}, reliability=0.8
    )
    session.add(source)
    await session.commit()
    result = await run_source(session, source, transport=recorded("github"))
    await session.commit()
    assert result.status == "failed"
    assert "repos" in (result.error or "")


async def test_network_source_runs_from_fixtures_and_counts_requests(session, recorded):
    source = Source(
        slug="gh-good",
        name="GitHub",
        adapter_key="github",
        reliability=0.8,
        config={"repos": ["electric-sql/electric"]},
    )
    session.add(source)
    await session.commit()

    result = await run_source(session, source, transport=recorded("github"))
    await session.commit()
    assert result.status == "succeeded"
    assert result.http_requests == 2, "one repo endpoint + one commit-activity endpoint"
    assert result.observations > 0

    signals = (await session.execute(sa.select(Signal))).scalars().all()
    assert {s.signal_type for s in signals} >= {"github_stars", "github_commit_velocity"}


async def test_etag_is_persisted_and_reused(session, recorded):
    source = Source(
        slug="gh-etag",
        name="GitHub",
        adapter_key="github",
        reliability=0.8,
        config={"repos": ["electric-sql/electric"]},
    )
    session.add(source)
    await session.commit()

    transport = recorded("github")
    await run_source(session, source, transport=transport)
    await session.commit()

    cached = (await session.execute(sa.select(HttpCacheEntry))).scalars().all()
    assert cached, "an ETag returned by the source must be stored for the next run"
    assert cached[0].etag == 'W/"repo-etag-1"'

    second = await run_source(session, source, transport=transport)
    await session.commit()
    assert second.not_modified >= 1, "the second run must send If-None-Match and get a 304"


async def test_partial_source_failure_is_recorded_as_partial(session, recorded):
    source = Source(
        slug="gh-partial",
        name="GitHub",
        adapter_key="github",
        reliability=0.8,
        config={"repos": ["electric-sql/electric", "does-not/exist"]},
    )
    session.add(source)
    await session.commit()
    result = await run_source(session, source, transport=recorded("github"))
    await session.commit()
    assert result.status == "partial"
    assert result.stored > 0, "what was collected before the failure must be kept"
    assert "does-not/exist" in (result.error or "")


async def test_invalid_records_are_counted_not_silently_dropped(session, demo_source, monkeypatch):
    from app.sources.adapters import demo_mock

    original = demo_mock.DemoMockSource.fetch

    async def broken(self, since=None):
        records = await original(self, since)
        records[0].metric_value = float("nan")
        records[1].external_id = ""
        return records

    monkeypatch.setattr(demo_mock.DemoMockSource, "fetch", broken)
    result = await run_source(session, demo_source)
    await session.commit()
    assert result.rejected == 2


async def test_observations_are_immutable(session, demo_source):
    await run_source(session, demo_source)
    await session.commit()
    obs = (await session.execute(sa.select(SignalObservation).limit(1))).scalar_one()
    obs.value = 999.0
    with pytest.raises(ImmutableRowError):
        await session.commit()
    await session.rollback()


async def test_raw_records_cannot_be_deleted(session, demo_source):
    await run_source(session, demo_source)
    await session.commit()
    row = (await session.execute(sa.select(RawRecord).limit(1))).scalar_one()
    await session.delete(row)
    with pytest.raises(ImmutableRowError):
        await session.commit()
    await session.rollback()


async def test_offline_adapter_gets_no_fetcher(session, demo_source):
    assert build_fetcher(demo_source) is None, "an offline source must not be handed a network client"
