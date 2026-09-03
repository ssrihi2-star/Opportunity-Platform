"""phase 3 trends, entity identifiers, observation status

Revision ID: b01b97f9b4ab
Revises: 0001_initial
Create Date: 2026-09-02 20:55:54.071746
"""
from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

import app.db.base
from sqlalchemy.dialects import sqlite

revision: str = '0002_phase3'
down_revision: str | None = '0001_initial'
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    # The Phase 1 `trends` table was a placeholder that never carried a row. It is
    # rebuilt here rather than mutated through thirty ALTERs, which keeps both the
    # migration and the resulting schema readable. It must be rebuilt *before* the
    # new tables that reference it: PostgreSQL refuses to drop a table another
    # table's foreign key still points at.
    op.drop_table('trends')
    op.create_table(
        'trends',
        sa.Column('id', sa.Uuid(), nullable=False),
        sa.Column('subject_type', sa.String(length=20), nullable=False),
        sa.Column('entity_id', sa.Uuid(), nullable=True),
        sa.Column('topic_id', sa.Uuid(), nullable=True),
        sa.Column('name', sa.String(length=300), nullable=False),
        sa.Column('category', sa.String(length=40), nullable=True),
        sa.Column('geo_scope', sa.String(length=20), nullable=False),
        sa.Column('state', sa.String(length=20), nullable=False),
        sa.Column('stage', sa.String(length=30), nullable=False),
        sa.Column('trend_score', sa.Float(), nullable=False),
        sa.Column('confidence', sa.Float(), nullable=False),
        sa.Column('peak_score', sa.Float(), nullable=False),
        sa.Column('peak_score_at', sa.DateTime(timezone=True), nullable=True),
        sa.Column('first_detected_at', sa.DateTime(timezone=True), nullable=False),
        sa.Column('last_evaluated_at', sa.DateTime(timezone=True), nullable=False),
        sa.Column('last_confirmation_at', sa.DateTime(timezone=True), nullable=True),
        sa.Column('components', sa.JSON(), nullable=False),
        sa.Column('penalties', sa.JSON(), nullable=False),
        sa.Column('metrics', sa.JSON(), nullable=False),
        sa.Column('warnings', sa.JSON(), nullable=False),
        sa.Column('independent_source_count', sa.Integer(), nullable=False),
        sa.Column('distinct_signal_types', sa.Integer(), nullable=False),
        sa.Column('observation_count', sa.Integer(), nullable=False),
        sa.Column('history_days', sa.Integer(), nullable=False),
        sa.Column('missing_observation_count', sa.Integer(), nullable=False),
        sa.Column('is_spike', sa.Boolean(), nullable=False),
        sa.Column('is_seasonal', sa.Boolean(), nullable=False),
        sa.Column('explanation', sa.Text(), nullable=True),
        sa.Column('explanation_model_run_id', sa.Uuid(), nullable=True),
        sa.Column('created_at', sa.DateTime(timezone=True), nullable=False),
        sa.Column('updated_at', sa.DateTime(timezone=True), nullable=False),
        sa.ForeignKeyConstraint(['entity_id'], ['entities.id'], ondelete='CASCADE'),
        sa.ForeignKeyConstraint(['topic_id'], ['topics.id'], ondelete='CASCADE'),
        sa.ForeignKeyConstraint(['explanation_model_run_id'], ['model_runs.id']),
        sa.PrimaryKeyConstraint('id'),
        sa.UniqueConstraint('subject_type', 'entity_id', 'topic_id', 'geo_scope', name='ux_trend_subject'),
    )
    with op.batch_alter_table('trends', schema=None) as batch_op:
        batch_op.create_index(batch_op.f('ix_trends_category'), ['category'], unique=False)
        batch_op.create_index(batch_op.f('ix_trends_confidence'), ['confidence'], unique=False)
        batch_op.create_index(batch_op.f('ix_trends_first_detected_at'), ['first_detected_at'], unique=False)
        batch_op.create_index(batch_op.f('ix_trends_geo_scope'), ['geo_scope'], unique=False)
        batch_op.create_index(batch_op.f('ix_trends_last_evaluated_at'), ['last_evaluated_at'], unique=False)
        batch_op.create_index(batch_op.f('ix_trends_name'), ['name'], unique=False)
        batch_op.create_index(batch_op.f('ix_trends_stage'), ['stage'], unique=False)
        batch_op.create_index(batch_op.f('ix_trends_state'), ['state'], unique=False)
        batch_op.create_index(batch_op.f('ix_trends_subject_type'), ['subject_type'], unique=False)
        batch_op.create_index(batch_op.f('ix_trends_trend_score'), ['trend_score'], unique=False)

    op.create_table('entity_match_candidates',
    sa.Column('id', sa.Uuid(), nullable=False),
    sa.Column('observed_name', sa.String(length=300), nullable=False),
    sa.Column('observed_normalized', sa.String(length=300), nullable=False),
    sa.Column('entity_type', sa.String(length=30), nullable=False),
    sa.Column('candidate_entity_id', sa.Uuid(), nullable=False),
    sa.Column('created_entity_id', sa.Uuid(), nullable=True),
    sa.Column('confidence', sa.Float(), nullable=False),
    sa.Column('reason', sa.Text(), nullable=False),
    sa.Column('evidence', sa.JSON(), nullable=False),
    sa.Column('source_id', sa.Uuid(), nullable=True),
    sa.Column('decision', sa.String(length=20), nullable=False),
    sa.Column('decided_by_user_id', sa.Uuid(), nullable=True),
    sa.Column('decided_at', sa.DateTime(timezone=True), nullable=True),
    sa.Column('note', sa.Text(), nullable=True),
    sa.Column('created_at', sa.DateTime(timezone=True), nullable=False),
    sa.Column('updated_at', sa.DateTime(timezone=True), nullable=False),
    sa.ForeignKeyConstraint(['candidate_entity_id'], ['entities.id'], ondelete='CASCADE'),
    sa.ForeignKeyConstraint(['created_entity_id'], ['entities.id'], ondelete='SET NULL'),
    sa.ForeignKeyConstraint(['decided_by_user_id'], ['users.id'], ),
    sa.ForeignKeyConstraint(['source_id'], ['sources.id'], ),
    sa.PrimaryKeyConstraint('id'),
    sa.UniqueConstraint('observed_normalized', 'entity_type', 'candidate_entity_id', name='ux_match_candidate')
    )
    with op.batch_alter_table('entity_match_candidates', schema=None) as batch_op:
        batch_op.create_index(batch_op.f('ix_entity_match_candidates_decision'), ['decision'], unique=False)
        batch_op.create_index(batch_op.f('ix_entity_match_candidates_entity_type'), ['entity_type'], unique=False)
        batch_op.create_index(batch_op.f('ix_entity_match_candidates_observed_normalized'), ['observed_normalized'], unique=False)

    op.create_table('trend_signals',
    sa.Column('id', sa.Uuid(), nullable=False),
    sa.Column('trend_id', sa.Uuid(), nullable=False),
    sa.Column('signal_id', sa.Uuid(), nullable=False),
    sa.Column('source_id', sa.Uuid(), nullable=False),
    sa.Column('source_group', sa.String(length=80), nullable=False),
    sa.Column('signal_type', sa.String(length=60), nullable=False),
    sa.Column('growth_30d', sa.Float(), nullable=True),
    sa.Column('acceleration', sa.Float(), nullable=True),
    sa.Column('observation_count', sa.Integer(), nullable=False),
    sa.Column('is_proxy', sa.Boolean(), nullable=False),
    sa.Column('counted_as_independent', sa.Boolean(), nullable=False),
    sa.Column('contribution', sa.Float(), nullable=False),
    sa.Column('created_at', sa.DateTime(timezone=True), nullable=False),
    sa.Column('updated_at', sa.DateTime(timezone=True), nullable=False),
    sa.ForeignKeyConstraint(['signal_id'], ['signals.id'], ondelete='CASCADE'),
    sa.ForeignKeyConstraint(['source_id'], ['sources.id'], ),
    sa.ForeignKeyConstraint(['trend_id'], ['trends.id'], ondelete='CASCADE'),
    sa.PrimaryKeyConstraint('id'),
    sa.UniqueConstraint('trend_id', 'signal_id', name='ux_trend_signal')
    )
    op.create_table('trend_snapshots',
    sa.Column('id', sa.Uuid(), nullable=False),
    sa.Column('trend_id', sa.Uuid(), nullable=False),
    sa.Column('evaluated_at', sa.DateTime(timezone=True), nullable=False),
    sa.Column('formula_version', sa.String(length=20), nullable=False),
    sa.Column('trend_score', sa.Float(), nullable=False),
    sa.Column('confidence', sa.Float(), nullable=False),
    sa.Column('stage', sa.String(length=30), nullable=False),
    sa.Column('state', sa.String(length=20), nullable=False),
    sa.Column('components', sa.JSON(), nullable=False),
    sa.Column('penalties', sa.JSON(), nullable=False),
    sa.Column('metrics', sa.JSON(), nullable=False),
    sa.Column('warnings', sa.JSON(), nullable=False),
    sa.Column('created_at', sa.DateTime(timezone=True), nullable=False),
    sa.ForeignKeyConstraint(['trend_id'], ['trends.id'], ondelete='CASCADE'),
    sa.PrimaryKeyConstraint('id')
    )
    with op.batch_alter_table('trend_snapshots', schema=None) as batch_op:
        batch_op.create_index(batch_op.f('ix_trend_snapshots_evaluated_at'), ['evaluated_at'], unique=False)
        batch_op.create_index(batch_op.f('ix_trend_snapshots_trend_id'), ['trend_id'], unique=False)

    with op.batch_alter_table('entities', schema=None) as batch_op:
        batch_op.add_column(sa.Column('external_ids', sa.JSON(), nullable=False, server_default='{}'))

    with op.batch_alter_table('raw_records', schema=None) as batch_op:
        batch_op.add_column(sa.Column('title_fingerprint', sa.String(length=64), nullable=True))
        batch_op.create_index(batch_op.f('ix_raw_records_title_fingerprint'), ['title_fingerprint'], unique=False)

    with op.batch_alter_table('signal_observations', schema=None) as batch_op:
        batch_op.add_column(sa.Column('status', sa.String(length=10), nullable=False, server_default='ok'))
        batch_op.add_column(sa.Column('currency', sa.String(length=3), nullable=True))
        batch_op.alter_column('value',
               existing_type=sa.FLOAT(),
               nullable=True)
        batch_op.create_index(batch_op.f('ix_signal_observations_status'), ['status'], unique=False)

    with op.batch_alter_table('topics', schema=None) as batch_op:
        batch_op.add_column(sa.Column('category', sa.String(length=40), nullable=True))
        batch_op.add_column(sa.Column('keywords', sa.JSON(), nullable=False, server_default='[]'))
        batch_op.add_column(sa.Column('first_seen_at', sa.DateTime(timezone=True), nullable=True))
        batch_op.add_column(sa.Column('last_seen_at', sa.DateTime(timezone=True), nullable=True))
        batch_op.add_column(sa.Column('label_is_ai_generated', sa.Boolean(), nullable=False, server_default=sa.false()))
        batch_op.create_index(batch_op.f('ix_topics_category'), ['category'], unique=False)


def downgrade() -> None:
    op.drop_table('trend_signals')
    op.drop_table('trend_snapshots')
    op.drop_table('entity_match_candidates')
    op.drop_table('trends')
    op.create_table(
        'trends',
        sa.Column('id', sa.Uuid(), nullable=False),
        sa.Column('signal_id', sa.Uuid(), nullable=False),
        sa.Column('method', sa.String(length=40), nullable=False),
        sa.Column('window_days', sa.Integer(), nullable=False),
        sa.Column('statistic', sa.Float(), nullable=False),
        sa.Column('direction', sa.String(length=20), nullable=False),
        sa.Column('strength', sa.Float(), nullable=False),
        sa.Column('is_anomaly', sa.Boolean(), nullable=False),
        sa.Column('detected_at', sa.DateTime(timezone=True), nullable=False),
        sa.Column('details', sa.JSON(), nullable=False),
        sa.Column('created_at', sa.DateTime(timezone=True), nullable=False),
        sa.ForeignKeyConstraint(['signal_id'], ['signals.id'], ondelete='CASCADE'),
        sa.PrimaryKeyConstraint('id'),
    )
    # The Phase 1 table carried this index, and 0001's downgrade drops it. Leaving
    # it out here makes a full `downgrade base` fail on a missing object.
    with op.batch_alter_table('trends', schema=None) as batch_op:
        batch_op.create_index(batch_op.f('ix_trends_detected_at'), ['detected_at'], unique=False)
    # Indexes must go before the columns they cover: SQLite's batch mode rebuilds
    # the table and would otherwise try to recreate an index on a dropped column.
    with op.batch_alter_table('topics', schema=None) as batch_op:
        batch_op.drop_index(batch_op.f('ix_topics_category'))
    with op.batch_alter_table('topics', schema=None) as batch_op:
        batch_op.drop_column('label_is_ai_generated')
        batch_op.drop_column('last_seen_at')
        batch_op.drop_column('first_seen_at')
        batch_op.drop_column('keywords')
        batch_op.drop_column('category')
    with op.batch_alter_table('signal_observations', schema=None) as batch_op:
        batch_op.drop_index(batch_op.f('ix_signal_observations_status'))
    with op.batch_alter_table('signal_observations', schema=None) as batch_op:
        batch_op.drop_column('currency')
        batch_op.drop_column('status')
        batch_op.alter_column('value', existing_type=sa.FLOAT(), nullable=False)
    with op.batch_alter_table('raw_records', schema=None) as batch_op:
        batch_op.drop_index(batch_op.f('ix_raw_records_title_fingerprint'))
    with op.batch_alter_table('raw_records', schema=None) as batch_op:
        batch_op.drop_column('title_fingerprint')
    with op.batch_alter_table('entities', schema=None) as batch_op:
        batch_op.drop_column('external_ids')
