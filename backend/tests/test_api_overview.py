import pytest
from httpx import AsyncClient

pytestmark = pytest.mark.asyncio


async def _seed_source(client: AsyncClient, headers, slug="ov_demo", seed=3):
    created = await client.post(
        "/api/v1/sources",
        headers=headers,
        json={"slug": slug, "name": slug, "adapter_key": "demo_mock", "config": {"days": 40, "seed": seed}},
    )
    assert created.status_code == 201, created.text
    run = await client.post(f"/api/v1/sources/{created.json()['id']}/run", headers=headers)
    assert run.status_code == 200, run.text
    return created.json()["id"]


async def test_overview_reports_real_counts_and_disclaimer(client, admin_headers):
    await _seed_source(client, admin_headers)
    resp = await client.get("/api/v1/overview", headers=admin_headers)
    assert resp.status_code == 200
    body = resp.json()
    assert body["counts"]["raw_records"] > 0
    assert body["counts"]["signals"] > 0
    # Phase 4 objects do not exist yet and must be reported as zero, not hidden.
    assert body["counts"]["opportunities"] == 0
    assert "not financial advice" in body["disclaimer"].lower()
    assert body["fastest_growing"], "expected ranked signals"
    top = body["fastest_growing"][0]
    assert {"is_proxy", "pct_change_window", "source"} <= set(top)


async def test_signal_detail_returns_series_and_statistics(client, admin_headers):
    await _seed_source(client, admin_headers, slug="detail_demo", seed=9)
    listing = await client.get("/api/v1/signals", headers=admin_headers)
    assert listing.status_code == 200
    first = listing.json()["items"][0]
    assert first["source_slug"] == "detail_demo"

    detail = await client.get(f"/api/v1/signals/{first['id']}", headers=admin_headers)
    assert detail.status_code == 200
    body = detail.json()
    assert len(body["observations"]) > 10
    assert body["stats"]["n"] == len(body["observations"])
    assert body["stats"]["direction"] in {"up", "down", "flat", "unknown"}


async def test_signals_can_be_filtered_by_class(client, admin_headers):
    await _seed_source(client, admin_headers, slug="filter_demo", seed=12)
    resp = await client.get("/api/v1/signals?signal_class=trade", headers=admin_headers)
    assert resp.status_code == 200
    assert all(i["signal_class"] == "trade" for i in resp.json()["items"])


async def test_raw_records_are_paginated(client, admin_headers):
    await _seed_source(client, admin_headers, slug="page_demo", seed=5)
    resp = await client.get("/api/v1/raw-records?limit=5", headers=admin_headers)
    body = resp.json()
    assert len(body["items"]) == 5
    assert body["total"] > 5


async def test_metrics_expose_collector_counters(client, admin_headers):
    await _seed_source(client, admin_headers, slug="metrics_demo", seed=6)
    resp = await client.get("/api/v1/metrics", headers=admin_headers)
    assert resp.status_code == 200
    assert "ois_raw_records_total" in resp.text
    assert "ois_http_requests_total" in resp.text
    assert "ois_source_runs_failed_total" in resp.text


async def test_openapi_is_served(client):
    resp = await client.get("/api/v1/openapi.json")
    assert resp.status_code == 200
    assert "/api/v1/signals" in resp.json()["paths"]
    assert "/api/v1/sources/{source_id}/upload-csv" in resp.json()["paths"]


async def test_security_headers_present(client):
    resp = await client.get("/api/v1/health")
    assert resp.headers["X-Frame-Options"] == "DENY"
    assert resp.headers["X-Content-Type-Options"] == "nosniff"
    assert resp.headers["X-Request-ID"]


async def test_readiness_reports_component_checks(client):
    resp = await client.get("/api/v1/health/ready")
    assert resp.status_code == 200
    assert resp.json()["checks"]["database"] == "ok"
