"""The source-management surface: catalogue, credentials, CSV upload, health."""

import io

import pytest
from httpx import AsyncClient

pytestmark = pytest.mark.asyncio


async def _create(client, headers, **overrides):
    payload = {
        "slug": "s1",
        "name": "Source one",
        "adapter_key": "demo_mock",
        "config": {"days": 20, "seed": 3},
    }
    payload.update(overrides)
    resp = await client.post("/api/v1/sources", headers=headers, json=payload)
    assert resp.status_code == 201, resp.text
    return resp.json()


async def test_adapter_catalogue_lists_requirements(client: AsyncClient, admin_headers):
    resp = await client.get("/api/v1/sources/adapters", headers=admin_headers)
    assert resp.status_code == 200
    catalogue = {a["adapter_key"]: a for a in resp.json()}
    assert set(catalogue) >= {
        "csv_import",
        "demo_mock",
        "fred",
        "github",
        "hackernews",
        "rss",
        "sec_edgar",
        "un_comtrade",
        "wikipedia_pageviews",
    }
    assert catalogue["fred"]["requires_credentials"] == ["api_key"]
    assert catalogue["github"]["requires_network"] is True
    assert catalogue["demo_mock"]["requires_network"] is False
    assert "5,000 requests/hour" in catalogue["github"]["documented_rate_limit"]


async def test_credential_is_stored_encrypted_and_never_returned(client, admin_headers, session):
    import sqlalchemy as sa

    from app.models.models import SourceCredential

    source = await _create(client, admin_headers, slug="fred1", name="FRED", adapter_key="fred")
    put = await client.put(
        f"/api/v1/sources/{source['id']}/credentials",
        headers=admin_headers,
        json={"key": "api_key", "value": "super-secret-key-value"},
    )
    assert put.status_code == 200
    body = put.json()
    assert "value" not in body
    assert body["hint"] == "sup...lue"

    listed = await client.get(f"/api/v1/sources/{source['id']}/credentials", headers=admin_headers)
    assert listed.json()[0]["key"] == "api_key"
    assert "super-secret-key-value" not in listed.text

    row = (await session.execute(sa.select(SourceCredential))).scalars().first()
    assert "super-secret-key-value" not in row.value_encrypted


async def test_credential_rotation_records_the_time(client, admin_headers):
    source = await _create(client, admin_headers, slug="fred2", name="FRED", adapter_key="fred")
    await client.put(
        f"/api/v1/sources/{source['id']}/credentials",
        headers=admin_headers,
        json={"key": "api_key", "value": "first-value-here"},
    )
    second = await client.put(
        f"/api/v1/sources/{source['id']}/credentials",
        headers=admin_headers,
        json={"key": "api_key", "value": "second-value-here"},
    )
    assert second.json()["rotated_at"] is not None


async def test_credential_delete(client, admin_headers):
    source = await _create(client, admin_headers, slug="fred3", name="FRED", adapter_key="fred")
    await client.put(
        f"/api/v1/sources/{source['id']}/credentials",
        headers=admin_headers,
        json={"key": "api_key", "value": "value-to-delete"},
    )
    resp = await client.delete(f"/api/v1/sources/{source['id']}/credentials/api_key", headers=admin_headers)
    assert resp.status_code == 200
    missing = await client.delete(
        f"/api/v1/sources/{source['id']}/credentials/api_key", headers=admin_headers
    )
    assert missing.status_code == 404


async def test_source_missing_credential_is_reported_by_health(client, admin_headers):
    source = await _create(client, admin_headers, slug="fred4", name="FRED", adapter_key="fred")
    resp = await client.get(f"/api/v1/sources/{source['id']}/health", headers=admin_headers)
    body = resp.json()
    assert body["missing_credentials"] == ["api_key"]
    assert body["requires_network"] is True
    assert "120 requests/minute" in body["documented_rate_limit"]


async def test_run_without_credential_fails_with_an_actionable_message(client, admin_headers):
    source = await _create(
        client,
        admin_headers,
        slug="fred5",
        name="FRED",
        adapter_key="fred",
        config={"series": [{"id": "GDP"}]},
    )
    resp = await client.post(f"/api/v1/sources/{source['id']}/run", headers=admin_headers)
    assert resp.status_code == 200
    body = resp.json()
    assert body["status"] == "failed"
    assert "api_key" in body["error"]
    assert "credentials" in body["error"]


# ------------------------------------------------------------------ csv upload
CSV = (
    "observed_at,entity_name,entity_type,signal_type,value,unit,geo_scope\n"
    "2026-05-01,ceramic tiles,product,import_growth,120,tonnes,TN\n"
    "2026-06-01,ceramic tiles,product,import_growth,145,tonnes,TN\n"
    "2026-07-01,ceramic tiles,product,import_growth,171,tonnes,TN\n"
)


async def test_csv_upload_then_run(client: AsyncClient, admin_headers):
    source = await _create(
        client, admin_headers, slug="csv1", name="Customs sheet", adapter_key="csv_import", config={}
    )
    upload = await client.post(
        f"/api/v1/sources/{source['id']}/upload-csv",
        headers=admin_headers,
        files={"file": ("customs.csv", io.BytesIO(CSV.encode()), "text/csv")},
    )
    assert upload.status_code == 200, upload.text
    assert upload.json()["rows_parsed"] == 3

    run = await client.post(f"/api/v1/sources/{source['id']}/run", headers=admin_headers)
    assert run.json()["observations"] == 3


async def test_csv_upload_rejects_a_bad_header(client: AsyncClient, admin_headers):
    source = await _create(
        client, admin_headers, slug="csv2", name="Bad sheet", adapter_key="csv_import", config={}
    )
    resp = await client.post(
        f"/api/v1/sources/{source['id']}/upload-csv",
        headers=admin_headers,
        files={"file": ("bad.csv", io.BytesIO(b"a,b,c\n1,2,3\n"), "text/csv")},
    )
    assert resp.status_code == 422
    assert "missing required column" in resp.json()["detail"]


async def test_csv_upload_rejects_non_utf8(client: AsyncClient, admin_headers):
    source = await _create(
        client, admin_headers, slug="csv3", name="Latin sheet", adapter_key="csv_import", config={}
    )
    resp = await client.post(
        f"/api/v1/sources/{source['id']}/upload-csv",
        headers=admin_headers,
        files={"file": ("l.csv", io.BytesIO(b"\xff\xfe\x00bad"), "text/csv")},
    )
    assert resp.status_code == 422
    assert "UTF-8" in resp.json()["detail"]


async def test_csv_upload_refused_on_a_non_csv_source(client: AsyncClient, admin_headers):
    source = await _create(client, admin_headers, slug="notcsv", name="Demo")
    resp = await client.post(
        f"/api/v1/sources/{source['id']}/upload-csv",
        headers=admin_headers,
        files={"file": ("x.csv", io.BytesIO(CSV.encode()), "text/csv")},
    )
    assert resp.status_code == 409
    assert "csv_import" in resp.json()["detail"]


# ---------------------------------------------------------------------- health
async def test_health_probe_is_off_by_default(client: AsyncClient, admin_headers):
    source = await _create(client, admin_headers, slug="probe0", name="Demo")
    resp = await client.get(f"/api/v1/sources/{source['id']}/health", headers=admin_headers)
    assert resp.json()["probe"] is None


async def test_health_probe_runs_the_adapter_check(client: AsyncClient, admin_headers):
    source = await _create(client, admin_headers, slug="probe1", name="Demo")
    resp = await client.get(f"/api/v1/sources/{source['id']}/health?probe=true", headers=admin_headers)
    body = resp.json()
    assert body["probe_healthy"] is True
    assert "Offline deterministic generator" in body["probe"]


async def test_health_probe_never_500s_on_a_broken_source(client: AsyncClient, admin_headers):
    source = await _create(
        client, admin_headers, slug="probe2", name="GitHub", adapter_key="github", config={}
    )
    resp = await client.get(f"/api/v1/sources/{source['id']}/health?probe=true", headers=admin_headers)
    assert resp.status_code == 200
    assert resp.json()["probe_healthy"] is False


async def test_run_history_is_recorded(client: AsyncClient, admin_headers):
    source = await _create(client, admin_headers, slug="hist", name="Demo")
    await client.post(f"/api/v1/sources/{source['id']}/run", headers=admin_headers)
    resp = await client.get(f"/api/v1/sources/{source['id']}/runs", headers=admin_headers)
    assert resp.status_code == 200
    runs = resp.json()
    assert len(runs) == 1
    assert runs[0]["status"] == "succeeded"
    assert runs[0]["records_stored"] > 0
