"""API tests for admin-only topic membership management."""
import pytest
from httpx import AsyncClient

pytestmark = pytest.mark.asyncio


async def test_non_admin_cannot_add_member(client: AsyncClient, viewer_headers: dict[str, str]):
    """Viewer role cannot add topic members."""
    from app.models.models import Entity, Topic
    from sqlalchemy import select

    # Create a topic and entity (using session from fixture would be better,
    # but we need to work within the API test constraints)
    # We'll just test that the endpoint rejects non-admin with 403
    topic_id = "00000000-0000-0000-0000-000000000001"
    entity_id = "00000000-0000-0000-0000-000000000002"

    response = await client.post(
        f"/api/v1/topics/{topic_id}/members",
        headers=viewer_headers,
        json={
            "entity_id": entity_id,
            "justification": "Test justification",
        },
    )

    assert response.status_code == 403, f"Expected 403, got {response.status_code}"


async def test_non_admin_cannot_remove_member(client: AsyncClient, viewer_headers: dict[str, str]):
    """Viewer role cannot remove topic members."""
    topic_id = "00000000-0000-0000-0000-000000000001"
    entity_id = "00000000-0000-0000-0000-000000000002"

    response = await client.delete(
        f"/api/v1/topics/{topic_id}/members/{entity_id}",
        headers=viewer_headers,
    )

    assert response.status_code == 403, f"Expected 403, got {response.status_code}"


async def test_analyst_cannot_add_member(client: AsyncClient, analyst_headers: dict[str, str]):
    """Analyst role cannot add topic members."""
    topic_id = "00000000-0000-0000-0000-000000000001"
    entity_id = "00000000-0000-0000-0000-000000000002"

    response = await client.post(
        f"/api/v1/topics/{topic_id}/members",
        headers=analyst_headers,
        json={
            "entity_id": entity_id,
            "justification": "Test justification",
        },
    )

    assert response.status_code == 403, f"Expected 403, got {response.status_code}"


async def test_missing_justification_rejected(client: AsyncClient, admin_headers: dict[str, str]):
    """Request without justification is rejected."""
    from app.models.models import Entity, Topic, User
    from sqlalchemy import select

    # We need to create a topic and entity first
    # Use the database session via a helper
    from app.api.deps import db_session
    from sqlalchemy.ext.asyncio import AsyncSession

    # For API tests, we'll create via direct DB access in setup
    # This test will be implemented as an integration test with fixtures
    pytest.skip("Requires database fixtures - will be tested in integration tests")


async def test_blank_justification_rejected(client: AsyncClient, admin_headers: dict[str, str]):
    """Request with blank justification is rejected."""
    pytest.skip("Requires database fixtures - will be tested in integration tests")


async def test_audit_log_records_addition(client: AsyncClient, admin_headers: dict[str, str]):
    """Adding a member creates an audit log entry."""
    pytest.skip("Requires database fixtures - will be tested in integration tests")


async def test_audit_log_records_removal(client: AsyncClient, admin_headers: dict[str, str]):
    """Removing a member creates an audit log entry."""
    pytest.skip("Requires database fixtures - will be tested in integration tests")


async def test_add_nonexistent_topic_returns_404(client: AsyncClient, admin_headers: dict[str, str]):
    """Adding member to nonexistent topic returns 404."""
    topic_id = "00000000-0000-0000-0000-000000000001"
    entity_id = "00000000-0000-0000-0000-000000000002"

    response = await client.post(
        f"/api/v1/topics/{topic_id}/members",
        headers=admin_headers,
        json={
            "entity_id": entity_id,
            "justification": "Test justification",
        },
    )

    # Will be 404 because topic doesn't exist
    assert response.status_code == 404, f"Expected 404, got {response.status_code}"


async def test_add_nonexistent_entity_returns_404(client: AsyncClient, admin_headers: dict[str, str]):
    """Adding nonexistent entity to topic returns 404."""
    pytest.skip("Requires database fixtures - will be tested in integration tests")


async def test_remove_nonexistent_membership_returns_404(client: AsyncClient, admin_headers: dict[str, str]):
    """Removing nonexistent membership returns 404."""
    topic_id = "00000000-0000-0000-0000-000000000001"
    entity_id = "00000000-0000-0000-0000-000000000002"

    response = await client.delete(
        f"/api/v1/topics/{topic_id}/members/{entity_id}",
        headers=admin_headers,
    )

    assert response.status_code == 404, f"Expected 404, got {response.status_code}"
