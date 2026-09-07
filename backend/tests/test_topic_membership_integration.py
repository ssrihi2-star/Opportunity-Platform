"""Comprehensive integration tests for admin topic membership."""
import pytest
from httpx import AsyncClient
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy import select

from app.models.models import (
    Entity, Topic, TopicEntity, Source, Signal, SignalObservation,
    SystemAuditLog, User, Role
)
from app.models.enums import Role as RoleEnum
from app.services.topics import rebuild_topics
from app.core.security import hash_password

pytestmark = pytest.mark.asyncio


@pytest.fixture
async def first_local_topic(session: AsyncSession):
    """Create 'First Local' topic with automatic members."""
    # Create entities for First Local topic
    entity1 = Entity(
        canonical_name="local-first sync",
        normalized="local first sync",
        entity_type="technology",
    )
    entity2 = Entity(
        canonical_name="local-first tool",
        normalized="local first tool",
        entity_type="technology",
    )
    session.add_all([entity1, entity2])
    await session.flush()

    # Create topic
    topic = Topic(
        label="First Local",
        normalized="first|local",
        description="Test topic for local-first technologies",
        category="technology",
        keywords=["first", "local"],
    )
    session.add(topic)
    await session.flush()

    # Create automatic memberships
    for entity in [entity1, entity2]:
        membership = TopicEntity(
            topic_id=topic.id,
            entity_id=entity.id,
            weight=1.0,
            is_manual=False,
        )
        session.add(membership)
    await session.flush()

    return topic, [entity1, entity2]


@pytest.fixture
async def repository_entity(session: AsyncSession):
    """Create a repository entity that doesn't share tokens with First Local."""
    entity = Entity(
        canonical_name="pybamm-team/PyBaMM",
        normalized="pybamm team pybamm",
        entity_type="repository",
    )
    session.add(entity)
    await session.flush()
    return entity


@pytest.fixture
async def live_source(session: AsyncSession):
    """Create a live source for testing live_only filtering."""
    from app.sources.registry import get_adapter_class
    # Use GitHub adapter which requires network
    source = Source(
        slug="test-github",
        name="Test GitHub Source",
        adapter_key="github",
        source_class="primary_api",
        source_group="github",
        reliability=0.8,
        config={"repos": ["test/repo"], "weeks": 52},
        enabled=True,
    )
    session.add(source)
    await session.flush()
    return source


@pytest.fixture
async def demo_source(session: AsyncSession):
    """Create a demo source for testing live_only filtering."""
    source = Source(
        slug="test-demo",
        name="Test Demo Source",
        adapter_key="demo_mock",
        source_class="demo",
        source_group="demo",
        reliability=0.5,
        config={"entities": ["test"]},
        enabled=True,
    )
    session.add(source)
    await session.flush()
    return source


async def test_admin_can_add_member(
    client: AsyncClient,
    admin_headers: dict[str, str],
    first_local_topic: tuple[Topic, list[Entity]],
    repository_entity: Entity,
):
    """Admin can add a member to a topic with justification."""
    topic, _ = first_local_topic

    response = await client.post(
        f"/api/v1/topics/{topic.id}/members",
        headers=admin_headers,
        json={
            "entity_id": str(repository_entity.id),
            "justification": "PyBaMM is a leading battery simulation framework relevant to local-first development",
        },
    )

    assert response.status_code == 201, f"Expected 201, got {response.status_code}: {response.text}"
    data = response.json()
    assert data["status"] == "ok"
    assert data["entity_id"] == str(repository_entity.id)
    assert data["entity_name"] == "pybamm-team/PyBaMM"


async def test_non_admin_cannot_add_member(
    client: AsyncClient,
    viewer_headers: dict[str, str],
    first_local_topic: tuple[Topic, list[Entity]],
    repository_entity: Entity,
):
    """Non-admin cannot add a member to a topic."""
    topic, _ = first_local_topic

    response = await client.post(
        f"/api/v1/topics/{topic.id}/members",
        headers=viewer_headers,
        json={
            "entity_id": str(repository_entity.id),
            "justification": "Test justification",
        },
    )

    assert response.status_code == 403, f"Expected 403, got {response.status_code}"


async def test_analyst_cannot_add_member(
    client: AsyncClient,
    analyst_headers: dict[str, str],
    first_local_topic: tuple[Topic, list[Entity]],
    repository_entity: Entity,
):
    """Analyst cannot add a member to a topic."""
    topic, _ = first_local_topic

    response = await client.post(
        f"/api/v1/topics/{topic.id}/members",
        headers=analyst_headers,
        json={
            "entity_id": str(repository_entity.id),
            "justification": "Test justification",
        },
    )

    assert response.status_code == 403, f"Expected 403, got {response.status_code}"


async def test_missing_justification_rejected(
    client: AsyncClient,
    admin_headers: dict[str, str],
    first_local_topic: tuple[Topic, list[Entity]],
    repository_entity: Entity,
):
    """Request without justification is rejected."""
    topic, _ = first_local_topic

    response = await client.post(
        f"/api/v1/topics/{topic.id}/members",
        headers=admin_headers,
        json={
            "entity_id": str(repository_entity.id),
        },
    )

    assert response.status_code == 422, f"Expected 422, got {response.status_code}"


async def test_blank_justification_rejected(
    client: AsyncClient,
    admin_headers: dict[str, str],
    first_local_topic: tuple[Topic, list[Entity]],
    repository_entity: Entity,
):
    """Request with blank justification is rejected."""
    topic, _ = first_local_topic

    response = await client.post(
        f"/api/v1/topics/{topic.id}/members",
        headers=admin_headers,
        json={
            "entity_id": str(repository_entity.id),
            "justification": "   ",
        },
    )

    # Should be rejected because justification is blank after stripping
    assert response.status_code == 422, f"Expected 422, got {response.status_code}"


async def test_audit_log_records_addition(
    session: AsyncSession,
    client: AsyncClient,
    admin_headers: dict[str, str],
    first_local_topic: tuple[Topic, list[Entity]],
    repository_entity: Entity,
):
    """Adding a member creates an audit log entry."""
    topic, _ = first_local_topic

    response = await client.post(
        f"/api/v1/topics/{topic.id}/members",
        headers=admin_headers,
        json={
            "entity_id": str(repository_entity.id),
            "justification": "Test justification for audit",
        },
    )

    assert response.status_code == 201

    # Check audit log
    result = await session.execute(
        select(SystemAuditLog).where(
            SystemAuditLog.action == "topic.membership_added",
            SystemAuditLog.object_id == str(topic.id),
        )
    )
    logs = result.scalars().all()
    assert len(logs) == 1

    log = logs[0]
    assert log.after["entity_id"] == str(repository_entity.id)
    assert log.after["entity_name"] == "pybamm-team/PyBaMM"
    assert log.after["justification"] == "Test justification for audit"


async def test_audit_log_records_removal(
    session: AsyncSession,
    client: AsyncClient,
    admin_headers: dict[str, str],
    first_local_topic: tuple[Topic, list[Entity]],
    repository_entity: Entity,
):
    """Removing a member creates an audit log entry."""
    topic, _ = first_local_topic

    # First add a member
    await client.post(
        f"/api/v1/topics/{topic.id}/members",
        headers=admin_headers,
        json={
            "entity_id": str(repository_entity.id),
            "justification": "Test justification",
        },
    )

    # Clear audit logs from addition
    await session.execute(
        select(SystemAuditLog).where(
            SystemAuditLog.action == "topic.membership_added"
        ).execution_options(synchronize_session=False)
    )
    await session.flush()

    # Now remove the member
    response = await client.delete(
        f"/api/v1/topics/{topic.id}/members/{repository_entity.id}",
        headers=admin_headers,
    )

    assert response.status_code == 200

    # Check audit log for removal
    result = await session.execute(
        select(SystemAuditLog).where(
            SystemAuditLog.action == "topic.membership_removed",
            SystemAuditLog.object_id == str(topic.id),
        )
    )
    logs = result.scalars().all()
    assert len(logs) == 1

    log = logs[0]
    assert log.before["entity_id"] == str(repository_entity.id)
    assert log.before["entity_name"] == "pybamm-team/PyBaMM"
    assert log.before["was_manual"] is True


async def test_manual_membership_preserved_across_rebuild(
    session: AsyncSession,
    client: AsyncClient,
    admin_headers: dict[str, str],
    first_local_topic: tuple[Topic, list[Entity]],
    repository_entity: Entity,
):
    """Manual membership is preserved when rebuild_topics runs."""
    topic, _ = first_local_topic

    # Add manual member
    await client.post(
        f"/api/v1/topics/{topic.id}/members",
        headers=admin_headers,
        json={
            "entity_id": str(repository_entity.id),
            "justification": "Relevant repository",
        },
    )

    # Run rebuild_topics
    await rebuild_topics(session)
    await session.flush()

    # Check that manual membership is still there
    result = await session.execute(
        select(TopicEntity).where(
            TopicEntity.topic_id == topic.id,
            TopicEntity.entity_id == repository_entity.id,
        )
    )
    membership = result.scalar_one_or_none()

    assert membership is not None
    assert membership.is_manual is True
    assert membership.justification == "Relevant repository"


async def test_manual_membership_not_removed_by_clustering(
    session: AsyncSession,
    client: AsyncClient,
    admin_headers: dict[str, str],
    first_local_topic: tuple[Topic, list[Entity]],
    repository_entity: Entity,
):
    """Manual membership is not removed even if entity doesn't match cluster tokens."""
    topic, _ = first_local_topic

    # Add manual member (repository doesn't share tokens with First Local)
    await client.post(
        f"/api/v1/topics/{topic.id}/members",
        headers=admin_headers,
        json={
            "entity_id": str(repository_entity.id),
            "justification": "Verified relationship",
        },
    )

    # Run rebuild_topics multiple times
    for _ in range(3):
        await rebuild_topics(session)
        await session.flush()

    # Check that manual membership is still there
    result = await session.execute(
        select(TopicEntity).where(
            TopicEntity.topic_id == topic.id,
            TopicEntity.entity_id == repository_entity.id,
        )
    )
    membership = result.scalar_one_or_none()

    assert membership is not None
    assert membership.is_manual is True


async def test_upgrade_from_automatic_to_manual(
    session: AsyncSession,
    client: AsyncClient,
    admin_headers: dict[str, str],
    first_local_topic: tuple[Topic, list[Entity]],
):
    """Upgrading from automatic to manual membership works correctly."""
    topic, entities = first_local_topic
    entity = entities[0]
    
    # Store IDs before any operations that might expire them
    topic_id = topic.id
    entity_id = entity.id

    # Check that entity is currently automatic
    result = await session.execute(
        select(TopicEntity).where(
            TopicEntity.topic_id == topic_id,
            TopicEntity.entity_id == entity_id,
        )
    )
    membership = result.scalar_one_or_none()
    assert membership is not None
    assert membership.is_manual is False

    # Add as manual (should upgrade)
    response = await client.post(
        f"/api/v1/topics/{topic_id}/members",
        headers=admin_headers,
        json={
            "entity_id": str(entity_id),
            "justification": "Upgraded to manual",
        },
    )

    assert response.status_code == 201

    # Expire all objects in the session to force reload from database
    session.expire_all()

    # Check that membership is now manual
    result = await session.execute(
        select(TopicEntity).where(
            TopicEntity.topic_id == topic_id,
            TopicEntity.entity_id == entity_id,
        )
    )
    membership = result.scalar_one_or_none()
    assert membership is not None
    assert membership.is_manual is True
    assert membership.justification == "Upgraded to manual"


async def test_duplicate_manual_membership_rejected(
    client: AsyncClient,
    admin_headers: dict[str, str],
    first_local_topic: tuple[Topic, list[Entity]],
    repository_entity: Entity,
):
    """Adding a manual membership that already exists as manual is rejected."""
    topic, _ = first_local_topic

    # Add manual member
    response1 = await client.post(
        f"/api/v1/topics/{topic.id}/members",
        headers=admin_headers,
        json={
            "entity_id": str(repository_entity.id),
            "justification": "First addition",
        },
    )
    assert response1.status_code == 201

    # Try to add again
    response2 = await client.post(
        f"/api/v1/topics/{topic.id}/members",
        headers=admin_headers,
        json={
            "entity_id": str(repository_entity.id),
            "justification": "Second addition",
        },
    )
    assert response2.status_code == 409


async def test_list_topics_shows_manual_memberships(
    client: AsyncClient,
    admin_headers: dict[str, str],
    first_local_topic: tuple[Topic, list[Entity]],
    repository_entity: Entity,
):
    """List topics endpoint shows manual membership details."""
    topic, _ = first_local_topic

    # Add manual member
    await client.post(
        f"/api/v1/topics/{topic.id}/members",
        headers=admin_headers,
        json={
            "entity_id": str(repository_entity.id),
            "justification": "Test justification",
        },
    )

    # List topics
    response = await client.get("/api/v1/topics", headers=admin_headers)
    assert response.status_code == 200

    topics = response.json()
    topic_data = next((t for t in topics if t["id"] == str(topic.id)), None)
    assert topic_data is not None

    # Check entities list
    entities = topic_data["entities"]
    assert len(entities) == 3  # 2 automatic + 1 manual

    # Find the manual member
    manual_member = next((e for e in entities if e["entity_id"] == str(repository_entity.id)), None)
    assert manual_member is not None
    assert manual_member["is_manual"] is True
    assert manual_member["justification"] == "Test justification"


async def test_live_only_excludes_demo_evidence(
    session: AsyncSession,
    live_source: Source,
    demo_source: Source,
    first_local_topic: tuple[Topic, list[Entity]],
):
    """Live-only evaluation excludes demo/manual-import evidence."""
    from app.services.trends import evaluate_trends
    from app.models.enums import AnalysisMode
    from app.models.models import Signal, Trend, TrendSignal

    topic, entities = first_local_topic
    entity = entities[0]

    # Create signals from both sources
    live_signal = Signal(
        entity_id=entity.id,
        signal_type="github_stars",
        signal_class="developer",
        geo_scope="global",
        source_id=live_source.id,
    )
    demo_signal = Signal(
        entity_id=entity.id,
        signal_type="wiki_pageview_growth",
        signal_class="attention",
        geo_scope="global",
        source_id=demo_source.id,
        is_proxy=True,
    )
    session.add_all([live_signal, demo_signal])
    await session.flush()

    # Create observations for both signals
    from datetime import datetime, timedelta, timezone
    now = datetime.now(timezone.utc)
    for i in range(10):
        obs_date = now - timedelta(days=i)
        live_obs = SignalObservation(
            signal_id=live_signal.id,
            observed_at=obs_date,
            value=float(100 + i),
            collected_at=now,
        )
        demo_obs = SignalObservation(
            signal_id=demo_signal.id,
            observed_at=obs_date,
            value=float(1000 + i),
            collected_at=now,
        )
        session.add_all([live_obs, demo_obs])
    await session.flush()

    # Evaluate in live_only mode
    await evaluate_trends(session, analysis_mode=AnalysisMode.LIVE_ONLY)
    await session.flush()

    # Check that live-only trend was created for the entity
    from app.models.models import Trend
    result = await session.execute(
        select(Trend).where(
            Trend.entity_id == entity.id,
            Trend.analysis_mode == AnalysisMode.LIVE_ONLY,
        )
    )
    trend = result.scalar_one_or_none()

    # Entity trend should exist (we have enough live observations)
    # But it should only count live signals, not demo signals
    assert trend is not None
    assert trend.analysis_mode == AnalysisMode.LIVE_ONLY
    
    # Verify that only the live signal is included in the trend's signals
    from app.models.models import TrendSignal
    result = await session.execute(
        select(TrendSignal).where(
            TrendSignal.trend_id == trend.id,
        )
    )
    trend_signals = result.scalars().all()
    
    # Should have 1 trend signal (from live_source only)
    assert len(trend_signals) == 1
    trend_signal = trend_signals[0]
    
    # Verify it's from the live source
    result = await session.execute(
        select(Signal).where(
            Signal.id == trend_signal.signal_id,
        )
    )
    signal = result.scalar_one()
    assert signal.source_id == live_source.id
    assert signal.signal_type == "github_stars"


async def test_topic_evaluation_uses_union_of_signals(
    session: AsyncSession,
    first_local_topic: tuple[Topic, list[Entity]],
    live_source: Source,
):
    """Topic evaluation uses union of all member signals, no double-counting."""
    from app.services.trends import evaluate_trends
    from app.models.enums import AnalysisMode
    from datetime import datetime, timedelta, timezone

    topic, entities = first_local_topic

    # Create signals for both entities with the same signal_type
    signal1 = Signal(
        entity_id=entities[0].id,
        signal_type="github_stars",
        signal_class="developer",
        geo_scope="global",
        source_id=live_source.id,
    )
    signal2 = Signal(
        entity_id=entities[1].id,
        signal_type="github_stars",  # Same signal type
        signal_class="developer",
        geo_scope="global",
        source_id=live_source.id,
    )
    session.add_all([signal1, signal2])
    await session.flush()

    # Create observations
    now = datetime.now(timezone.utc)
    for i in range(10):
        obs_date = now - timedelta(days=i)
        obs1 = SignalObservation(
            signal_id=signal1.id, observed_at=obs_date, value=float(100 + i), collected_at=now
        )
        obs2 = SignalObservation(
            signal_id=signal2.id, observed_at=obs_date, value=float(200 + i), collected_at=now
        )
        session.add_all([obs1, obs2])
    await session.flush()

    # Evaluate
    await evaluate_trends(session, analysis_mode=AnalysisMode.LIVE_ONLY)
    await session.flush()

    # Check that topic trend was created
    from app.models.models import Trend
    result = await session.execute(
        select(Trend).where(
            Trend.topic_id == topic.id,
            Trend.analysis_mode == AnalysisMode.LIVE_ONLY,
        )
    )
    trend = result.scalar_one_or_none()

    # Trend should exist and should have counted the signal only once
    # (not double-counted because both entities have the same signal_type)
    assert trend is not None
    # The trend_score should reflect that we have 2 signals but they're the same type
    # So distinct_signal_types should be 1, not 2
    assert trend.distinct_signal_types == 1
