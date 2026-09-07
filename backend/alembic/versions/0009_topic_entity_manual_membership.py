"""Allow admins to explicitly link entities to topics.

Topic clustering is deterministic: entities are grouped by shared tokens in
their canonical names, with co-occurrence reinforcing the match. This produces
topics like "Local First Sync" (entities: "local-first sync", "local-first").

However, the clustering cannot connect an entity named after a repository (e.g.
"pybamm-team/PyBaMM") to a topic named after a technology concept (e.g.
"solid-state battery") — they share no tokens, and thematic relevance is not a
signal the engine is allowed to use. An admin who has verified the relationship
externally has no way to record it.

This migration adds two columns to ``topic_entities``:

* ``is_manual`` — True when an admin explicitly linked this entity to this
  topic, rather than automatic clustering.
* ``justification`` — A short note from the admin explaining why the link is
  valid. Required for manual memberships so every explicit link is auditable.

Manual memberships are preserved across ``rebuild_topics`` runs. The clustering
algorithm is unchanged; it continues to add and remove automatic members. A
manual membership is never removed by the algorithm, only by an explicit admin
action.

The same signal is not counted twice. When a topic is evaluated, its evidence
comes from the union of all member entities' signals. A signal that supports
multiple member entities still appears once in the topic's evidence set.

Existing live-only exclusions still apply. A manual membership does not bypass
source provenance filtering; if a source is excluded from live-only analysis,
its signals remain excluded even when the entity is manually linked to a topic.

**Nothing is deleted and nothing is backfilled.** Every existing membership is
``is_manual=False`` with ``justification=None``, which is the literal truth:
they were all produced by automatic clustering.
"""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op

revision: str = "0009_topic_entity_manual_membership"
down_revision: str | None = "0008_analysis_mode"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column(
        "topic_entities",
        sa.Column("is_manual", sa.Boolean, nullable=False, server_default=sa.false()),
    )
    op.add_column(
        "topic_entities",
        sa.Column("justification", sa.Text, nullable=True),
    )


def downgrade() -> None:
    op.drop_column("topic_entities", "justification")
    op.drop_column("topic_entities", "is_manual")
