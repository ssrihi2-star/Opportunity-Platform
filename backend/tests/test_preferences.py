import pytest
from httpx import AsyncClient

pytestmark = pytest.mark.asyncio


async def test_preferences_created_on_first_read(client: AsyncClient, viewer_headers):
    resp = await client.get("/api/v1/preferences", headers=viewer_headers)
    assert resp.status_code == 200
    assert resp.json()["min_confidence"] == 0.65


async def test_preferences_partial_update(client: AsyncClient, viewer_headers):
    await client.get("/api/v1/preferences", headers=viewer_headers)
    resp = await client.patch(
        "/api/v1/preferences",
        headers=viewer_headers,
        json={"countries": ["TN", "LY"], "max_risk_level": "medium", "capital_max_usd": 5000},
    )
    assert resp.status_code == 200, resp.text
    body = resp.json()
    assert body["countries"] == ["TN", "LY"]
    assert body["max_risk_level"] == "medium"
    assert body["capital_max_usd"] == 5000
    assert body["min_confidence"] == 0.65  # untouched field keeps its value


async def test_invalid_risk_level_rejected(client: AsyncClient, viewer_headers):
    resp = await client.patch(
        "/api/v1/preferences", headers=viewer_headers, json={"max_risk_level": "catastrophic"}
    )
    assert resp.status_code == 422


async def test_capital_range_validated(client: AsyncClient, viewer_headers):
    await client.get("/api/v1/preferences", headers=viewer_headers)
    resp = await client.patch(
        "/api/v1/preferences",
        headers=viewer_headers,
        json={"capital_min_usd": 9000, "capital_max_usd": 100},
    )
    assert resp.status_code == 422
    assert "capital_min_usd" in resp.json()["detail"]
