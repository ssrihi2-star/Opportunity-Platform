"""Migration 0008 must add the mode without touching a single existing row.

Run in isolation against a miniature pre-0008 schema rather than by upgrading
the whole chain, because migration 0003 predates this work and already cannot
run on SQLite (it does a bare `create_unique_constraint`, which the SQLite
dialect refuses outside batch mode). Building the two tables 0008 actually
touches keeps the check honest and runnable in the offline test suite.

What is asserted is the promise the migration makes: existing trends and
opportunities are preserved, backfilled to `demo_inclusive` — never to
`live_only`, which would relabel a mixed result as a live one — and the trend
uniqueness key widens to include the mode so a live-only evaluation cannot
overwrite a demo-inclusive row.
"""

from __future__ import annotations

import pathlib
import uuid

import pytest
import sqlalchemy as sa
from alembic.migration import MigrationContext
from alembic.operations import Operations

MIGRATION = pathlib.Path(__file__).parents[1] / "alembic" / "versions" / "0008_analysis_mode.py"

PRE_0008 = sa.MetaData()

sa.Table(
    "trends",
    PRE_0008,
    sa.Column("id", sa.Uuid(), primary_key=True),
    sa.Column("subject_type", sa.String(20), nullable=False),
    sa.Column("entity_id", sa.Uuid(), nullable=True),
    sa.Column("topic_id", sa.Uuid(), nullable=True),
    sa.Column("name", sa.String(300), nullable=False),
    sa.Column("geo_scope", sa.String(20), nullable=False),
    sa.Column("trend_score", sa.Float(), nullable=False),
    sa.Column("confidence", sa.Float(), nullable=False),
    sa.UniqueConstraint("subject_type", "entity_id", "topic_id", "geo_scope", name="ux_trend_subject"),
)

sa.Table(
    "opportunities",
    PRE_0008,
    sa.Column("id", sa.Uuid(), primary_key=True),
    sa.Column("slug", sa.String(320), nullable=False, unique=True),
    sa.Column("title", sa.String(300), nullable=False),
    sa.Column("validation_status", sa.String(20), nullable=False),
    sa.Column("opportunity_score", sa.Float(), nullable=False),
)


def _load_migration():
    import importlib.util

    spec = importlib.util.spec_from_file_location("migration_0008", MIGRATION)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


@pytest.fixture
def pre_migration_db(tmp_path):
    """A pre-0008 database holding rows a user would not want to lose."""
    engine = sa.create_engine(f"sqlite:///{tmp_path / 'pre.db'}")
    PRE_0008.create_all(engine)
    trend_id = uuid.uuid4()
    with engine.begin() as conn:
        conn.execute(
            PRE_0008.tables["trends"].insert(),
            [
                {
                    "id": trend_id,
                    "subject_type": "entity",
                    "entity_id": uuid.uuid4(),
                    "topic_id": None,
                    "name": "heat pump water heater",
                    "geo_scope": "global",
                    "trend_score": 61.0,
                    "confidence": 93.0,
                },
                {
                    "id": uuid.uuid4(),
                    "subject_type": "topic",
                    "entity_id": None,
                    "topic_id": uuid.uuid4(),
                    "name": "Pump Heat Water",
                    "geo_scope": "global",
                    "trend_score": 58.0,
                    "confidence": 93.0,
                },
            ],
        )
        conn.execute(
            PRE_0008.tables["opportunities"].insert(),
            [
                {
                    "id": uuid.uuid4(),
                    "slug": "business-existing",
                    "title": "An existing candidate",
                    "validation_status": "demo",
                    "opportunity_score": 47.0,
                }
            ],
        )
    return engine, trend_id


def _run_upgrade(engine) -> None:
    module = _load_migration()
    with engine.begin() as conn:
        context = MigrationContext.configure(conn, opts={"as_sql": False})
        with Operations.context(context):
            module.upgrade()


def test_upgrade_preserves_every_row_and_backfills_demo_inclusive(pre_migration_db):
    engine, _trend_id = pre_migration_db
    _run_upgrade(engine)

    with engine.connect() as conn:
        trends = conn.execute(sa.text("SELECT name, confidence, analysis_mode FROM trends")).all()
        opportunities = conn.execute(
            sa.text("SELECT slug, opportunity_score, analysis_mode FROM opportunities")
        ).all()

    assert len(trends) == 2, "no trend may be dropped by the migration"
    assert len(opportunities) == 1, "no opportunity may be dropped by the migration"

    # Backfilled to the mixed mode, which is the literal truth about how those
    # scores were produced. Nothing is relabelled live.
    assert {row.analysis_mode for row in trends} == {"demo_inclusive"}
    assert {row.analysis_mode for row in opportunities} == {"demo_inclusive"}

    # Scores are untouched: this migration does not recompute anything.
    assert {row.confidence for row in trends} == {93.0}
    assert opportunities[0].opportunity_score == 47.0


def test_the_widened_key_lets_both_modes_coexist(pre_migration_db):
    """The point of the constraint change: one subject, two evaluations."""
    engine, trend_id = pre_migration_db
    _run_upgrade(engine)

    with engine.connect() as conn:
        original = conn.execute(
            sa.text("SELECT * FROM trends WHERE name = :n"),
            {"n": "heat pump water heater"},
        ).mappings().one()

    twin = {**original, "id": str(uuid.uuid4()), "analysis_mode": "live_only", "confidence": 41.0}
    with engine.begin() as conn:
        conn.execute(
            sa.text(
                "INSERT INTO trends (id, subject_type, entity_id, topic_id, name, geo_scope, "
                "trend_score, confidence, analysis_mode) VALUES (:id, :subject_type, :entity_id, "
                ":topic_id, :name, :geo_scope, :trend_score, :confidence, :analysis_mode)"
            ),
            twin,
        )

    with engine.connect() as conn:
        rows = conn.execute(
            sa.text("SELECT analysis_mode, confidence FROM trends WHERE name = :n ORDER BY confidence"),
            {"n": "heat pump water heater"},
        ).all()

    assert [(r.analysis_mode, r.confidence) for r in rows] == [
        ("live_only", 41.0),
        ("demo_inclusive", 93.0),
    ]


def test_the_key_widened_rather_than_being_replaced(pre_migration_db):
    """Widening must not weaken: the old columns are all still in the key.

    Asserted on the constraint definition rather than by provoking a violation,
    because `topic_id` is NULL on an entity trend and both SQLite and PostgreSQL
    treat NULL as distinct from NULL in a unique key — so an entity trend would
    not collide regardless of what the constraint says. That NULL behaviour is
    pre-existing and unchanged here; what this work must not do is silently drop
    a column from the key.
    """
    engine, _trend_id = pre_migration_db
    _run_upgrade(engine)

    constraints = {c["name"]: c["column_names"] for c in sa.inspect(engine).get_unique_constraints("trends")}
    assert "ux_trend_subject" not in constraints, "the narrower key is replaced, not left alongside"
    assert constraints["ux_trend_subject_mode"] == [
        "subject_type",
        "entity_id",
        "topic_id",
        "geo_scope",
        "analysis_mode",
    ]


def test_the_upgrade_is_idempotent(pre_migration_db):
    """Re-running must not fail or duplicate; operators do re-run migrations."""
    engine, _trend_id = pre_migration_db
    _run_upgrade(engine)
    _run_upgrade(engine)

    with engine.connect() as conn:
        assert conn.execute(sa.text("SELECT COUNT(*) FROM trends")).scalar_one() == 2
        columns = {c["name"] for c in sa.inspect(engine).get_columns("trends")}
    assert "analysis_mode" in columns


def test_downgrade_refuses_rather_than_deleting_a_live_only_result(pre_migration_db):
    """A downgrade must never resolve a key clash by discarding a user's rows."""
    engine, trend_id = pre_migration_db
    _run_upgrade(engine)

    with engine.connect() as conn:
        original = conn.execute(
            sa.text("SELECT * FROM trends WHERE name = :n"),
            {"n": "heat pump water heater"},
        ).mappings().one()
    twin = {**original, "id": str(uuid.uuid4()), "analysis_mode": "live_only"}
    with engine.begin() as conn:
        conn.execute(
            sa.text(
                "INSERT INTO trends (id, subject_type, entity_id, topic_id, name, geo_scope, "
                "trend_score, confidence, analysis_mode) VALUES (:id, :subject_type, :entity_id, "
                ":topic_id, :name, :geo_scope, :trend_score, :confidence, :analysis_mode)"
            ),
            twin,
        )

    module = _load_migration()
    with pytest.raises(RuntimeError, match="live-only"), engine.begin() as conn:
        context = MigrationContext.configure(conn, opts={"as_sql": False})
        with Operations.context(context):
            module.downgrade()

    # And nothing was removed on the way to refusing.
    with engine.connect() as conn:
        assert conn.execute(sa.text("SELECT COUNT(*) FROM trends")).scalar_one() == 3


def test_downgrade_succeeds_when_only_demo_inclusive_rows_exist(pre_migration_db):
    engine, _trend_id = pre_migration_db
    _run_upgrade(engine)

    module = _load_migration()
    with engine.begin() as conn:
        context = MigrationContext.configure(conn, opts={"as_sql": False})
        with Operations.context(context):
            module.downgrade()

    columns = {c["name"] for c in sa.inspect(engine).get_columns("trends")}
    assert "analysis_mode" not in columns
    with engine.connect() as conn:
        assert conn.execute(sa.text("SELECT COUNT(*) FROM trends")).scalar_one() == 2
