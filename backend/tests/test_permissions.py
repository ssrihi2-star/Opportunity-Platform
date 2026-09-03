import pytest
from httpx import AsyncClient

pytestmark = pytest.mark.asyncio


async def test_viewer_cannot_create_source(client: AsyncClient, viewer_headers):
    resp = await client.post(
        "/api/v1/sources",
        headers=viewer_headers,
        json={"slug": "nope", "name": "Nope", "adapter_key": "demo_mock"},
    )
    assert resp.status_code == 403
    assert "requires the 'admin' role" in resp.json()["detail"]


async def test_viewer_cannot_read_metrics(client: AsyncClient, viewer_headers):
    assert (await client.get("/api/v1/metrics", headers=viewer_headers)).status_code == 403


async def test_viewer_cannot_read_or_write_credentials(client: AsyncClient, viewer_headers, admin_headers):
    created = await client.post(
        "/api/v1/sources",
        headers=admin_headers,
        json={
            "slug": "cred_rbac",
            "name": "Credential RBAC probe",
            "adapter_key": "github",
            "config": {"repos": ["a/b"]},
        },
    )
    source_id = created.json()["id"]
    assert (
        await client.get(f"/api/v1/sources/{source_id}/credentials", headers=viewer_headers)
    ).status_code == 403
    assert (
        await client.put(
            f"/api/v1/sources/{source_id}/credentials",
            headers=viewer_headers,
            json={"key": "token", "value": "x"},
        )
    ).status_code == 403


async def test_admin_can_create_and_run_source(client: AsyncClient, admin_headers):
    created = await client.post(
        "/api/v1/sources",
        headers=admin_headers,
        json={
            "slug": "demo_a",
            "name": "Demo A",
            "adapter_key": "demo_mock",
            "config": {"days": 20, "seed": 11},
        },
    )
    assert created.status_code == 201, created.text
    source_id = created.json()["id"]

    run = await client.post(f"/api/v1/sources/{source_id}/run", headers=admin_headers)
    assert run.status_code == 200, run.text
    body = run.json()
    assert body["status"] == "succeeded"
    assert body["stored"] > 0
    assert body["observations"] > 0
    assert body["http_requests"] == 0, "an offline adapter must make no HTTP requests"


async def test_unknown_adapter_is_rejected(client: AsyncClient, admin_headers):
    resp = await client.post(
        "/api/v1/sources",
        headers=admin_headers,
        json={"slug": "bad", "name": "Bad", "adapter_key": "does_not_exist"},
    )
    assert resp.status_code == 422
    assert "Unknown adapter" in resp.json()["detail"]


async def test_disabled_source_cannot_be_run(client: AsyncClient, admin_headers):
    created = await client.post(
        "/api/v1/sources",
        headers=admin_headers,
        json={"slug": "off", "name": "Off", "adapter_key": "demo_mock", "enabled": False},
    )
    source_id = created.json()["id"]
    resp = await client.post(f"/api/v1/sources/{source_id}/run", headers=admin_headers)
    assert resp.status_code == 409
    assert "disabled" in resp.json()["detail"]


async def test_viewer_can_read_signals(client: AsyncClient, viewer_headers):
    resp = await client.get("/api/v1/signals", headers=viewer_headers)
    assert resp.status_code == 200
    assert resp.json()["total"] == 0
