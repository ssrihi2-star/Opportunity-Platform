"""Tests for manual topic membership."""
from __future__ import annotations

import pytest
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.models import Entity, Topic, TopicEntity
from app.services.topics import rebuild_topics


@pytest.fixture
async def test_entities(session: AsyncSession):
    """Create test entities."""
    entities = [
        Entity(
            canonical_name="local-first sync",
            normalized="local first sync",
            entity_type="technology",
        ),
        Entity(
            canonical_name="local-first tool",
            normalized="local first tool",
            entity_type="technology",
        ),
        Entity(
            canonical_name="pybamm-team/PyBaMM",
            normalized="pybamm team pybamm",
            entity_type="repository",
        ),
    ]
    for entity in entities:
        session.add(entity)
    await session.flush()
    return entities


@pytest.fixture
async def test_topic(session: AsyncSession, test_entities):
    """Create a test topic with automatic membership."""
    topic = Topic(
        label="Local First",
        normalized="first|local",
        description="Test topic",
        category="technology",
        keywords=["first", "local"],
    )
    session.add(topic)
    await session.flush()
    # Add automatic membership for first two entities
    for entity in test_entities[:2]:
        membership = TopicEntity(
            topic_id=topic.id,
            entity_id=entity.id,
            weight=1.0,
            is_manual=False,
        )
        session.add(membership)
    await session.flush()
    return topic


async def test_manual_membership_preserved_across_rebuild(
    session: AsyncSession, test_entities, test_topic
):
    """Manual memberships should be preserved when rebuild_topics runs."""
    # Add manual membership for the third entity
    manual_membership = TopicEntity(
        topic_id=test_topic.id,
        entity_id=test_entities[2].id,
        weight=1.0,
        is_manual=True,
        justification="Repository is closely related to local-first technology",
    )
    session.add(manual_membership)
    await session.flush()

    # Run rebuild_topics
    topics = await rebuild_topics(session)

    # Find our topic
    topic = next((t for t in topics if t.id == test_topic.id), None)
    assert topic is not None

    # Check all three memberships are still there
    memberships = (
        await session.execute(
            __import__("sqlalchemy").select(TopicEntity).where(TopicEntity.topic_id == test_topic.id)
        )
    ).scalars().all()

    assert len(memberships) == 3
    manual = next(m for m in memberships if m.entity_id == test_entities[2].id)
    assert manual.is_manual is True
    assert manual.justification == "Repository is closely related to local-first technology"


async def test_automatic_membership_removed_when_not_in_cluster(
    session: AsyncSession, test_entities, test_topic
):
    """Automatic memberships should be removed when entity is no longer in cluster."""
    # The third entity is not in the cluster (no shared tokens)
    # It should not be added automatically
    # But if it was somehow added as automatic, it should be removed

    # Add automatic membership for the third entity (shouldn't happen in practice)
    auto_membership = TopicEntity(
        topic_id=test_topic.id,
        entity_id=test_entities[2].id,
        weight=1.0,
        is_manual=False,
    )
    session.add(auto_membership)
    await session.flush()

    # Run rebuild_topics
    await rebuild_topics(session)

    # Check the automatic membership was removed
    memberships = (
        await session.execute(
            __import__("sqlalchemy").select(TopicEntity).where(TopicEntity.topic_id == test_topic.id)
        )
    ).scalars().all()

    # Only the two original automatic members should remain
    assert len(memberships) == 2
    entity_ids = {m.entity_id for m in memberships}
    assert test_entities[0].id in entity_ids
    assert test_entities[1].id in entity_ids
    assert test_entities[2].id not in entity_ids


async def test_manual_membership_not_removed_when_not_in_cluster(
    session: AsyncSession, test_entities, test_topic
):
    """Manual memberships should NOT be removed even if entity is not in cluster."""
    # Add manual membership for the third entity
    manual_membership = TopicEntity(
        topic_id=test_topic.id,
        entity_id=test_entities[2].id,
        weight=1.0,
        is_manual=True,
        justification="Admin verified relationship",
    )
    session.add(manual_membership)
    await session.flush()

    # Run rebuild_topics
    await rebuild_topics(session)

    # Check the manual membership is still there
    memberships = (
        await session.execute(
            __import__("sqlalchemy").select(TopicEntity).where(TopicEntity.topic_id == test_topic.id)
        )
    ).scalars().all()

    assert len(memberships) == 3
    manual = next(m for m in memberships if m.entity_id == test_entities[2].id)
    assert manual.is_manual is True
    assert manual.justification == "Admin verified relationship"
