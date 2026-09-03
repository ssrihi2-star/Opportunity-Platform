import pytest
from httpx import AsyncClient

pytestmark = pytest.mark.asyncio


async def test_login_success_and_me(client: AsyncClient, admin_headers):
    resp = await client.get("/api/v1/auth/me", headers=admin_headers)
    assert resp.status_code == 200
    assert resp.json()["email"] == "admin@acme-corp.com"
    assert resp.json()["role"] == "admin"


async def test_login_wrong_password_is_generic(client: AsyncClient, admin_user):
    resp = await client.post("/api/v1/auth/login", json={"email": "admin@acme-corp.com", "password": "nope"})
    assert resp.status_code == 401
    assert "Incorrect email or password" in resp.json()["detail"]


async def test_login_unknown_user_same_message(client: AsyncClient):
    resp = await client.post(
        "/api/v1/auth/login", json={"email": "ghost@acme-corp.com", "password": "whatever123"}
    )
    assert resp.status_code == 401
    assert "Incorrect email or password" in resp.json()["detail"]


async def test_unauthenticated_is_rejected(client: AsyncClient):
    assert (await client.get("/api/v1/auth/me")).status_code == 401
    assert (await client.get("/api/v1/sources")).status_code == 401


async def test_logout_revokes_token(client: AsyncClient, admin_headers):
    assert (await client.post("/api/v1/auth/logout", headers=admin_headers)).status_code == 200
    resp = await client.get("/api/v1/auth/me", headers=admin_headers)
    assert resp.status_code == 401
    assert "revoked" in resp.json()["detail"]


async def test_refresh_rotates(client: AsyncClient, admin_user):
    assert (
        await client.post(
            "/api/v1/auth/login",
            json={"email": "admin@acme-corp.com", "password": "AdminPass!2026"},
        )
    ).status_code == 200
    resp = await client.post("/api/v1/auth/refresh")
    assert resp.status_code == 200
    assert resp.json()["access_token"]


async def test_health_is_public(client: AsyncClient):
    resp = await client.get("/api/v1/health")
    assert resp.status_code == 200
    assert resp.json()["status"] == "ok"
