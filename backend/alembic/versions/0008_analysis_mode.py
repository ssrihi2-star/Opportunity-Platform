"""Record which evidence an evaluation was allowed to read, and keep the two apart.

Real Hacker News and Wikipedia collection now works, but its observations land in
the same tables as the demo scenarios, the offline generator and the seeded
manual CSV. The engine read all of them together, so a trend like "Pump Heat"
could report confidence 93 on a mixture and there was no way to ask what the
live evidence alone supported.

This migration adds the distinction as *data*, not as a display filter:

* ``trends.analysis_mode`` and ``opportunities.analysis_mode`` — ``live_only`` or
  ``demo_inclusive``, describing what the row was **computed from**.
* ``ux_trend_subject_mode`` replaces ``ux_trend_subject``, adding
  ``analysis_mode`` to a trend's identity.

**Why the constraint has to change.** A trend's identity was
``(subject_type, entity_id, topic_id, geo_scope)``. Under that key a live-only
evaluation of "heat pump" would find the existing mixed-source row and update it
in place — overwriting a score computed from demo evidence with one computed
without it, while keeping the old row's snapshot history, peak score and first
detection date. The mixed result would be destroyed and its history would then
be presented as if it had always been live. Adding the mode to the key makes the
two independent rows that accumulate their own histories side by side.

**Nothing is deleted and nothing is recomputed.** Every existing row is
backfilled to ``demo_inclusive``, which is the literal truth about how it was
produced: the engine had no exclusion rule when those scores were written, so
demo evidence was eligible whether or not any happened to be present. Backfilling
anything to ``live_only`` would relabel a mixed result as a live one, which is
the exact failure this work exists to prevent. Live-only rows appear only when a
live-only evaluation is actually run.
"""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op

revision: str = "0008_analysis_mode"
down_revision: str | None = "0007_digest_period_identity"
branch_labels = None
depends_on = None

DEFAULT = "demo_inclusive"
COLUMN = "analysis_mode"
OLD_INDEX = "ux_trend_subject"
NEW_INDEX = "ux_trend_subject_mode"
TREND_KEY = ("subject_type", "entity_id", "topic_id", "geo_scope", COLUMN)


def _inspector() -> sa.Inspector:
    return sa.inspect(op.get_bind())


def _tables() -> set[str]:
    return set(_inspector().get_table_names())


def _columns(table: str) -> set[str]:
    return {c["name"] for c in _inspector().get_columns(table)}


def _constraint_names(table: str) -> set[str]:
    insp = _inspector()
    names = {c["name"] for c in insp.get_unique_constraints(table) if c.get("name")}
    names |= {i["name"] for i in insp.get_indexes(table) if i.get("name")}
    return names


def _add_mode_column(table: str) -> None:
    """Add the column non-null with an explicit backfill, safely on both backends."""
    if table not in _tables() or COLUMN in _columns(table):
        return
    # server_default first so existing rows are legal the instant the column
    # exists; the default is then dropped so the application layer owns the value.
    op.add_column(
        table,
        sa.Column(COLUMN, sa.String(length=20), nullable=False, server_default=DEFAULT),
    )
    op.execute(sa.text(f"UPDATE {table} SET {COLUMN} = '{DEFAULT}' WHERE {COLUMN} IS NULL"))  # noqa: S608
    op.create_index(op.f(f"ix_{table}_{COLUMN}"), table, [COLUMN], unique=False)
    with op.batch_alter_table(table) as batch:
        batch.alter_column(COLUMN, server_default=None)


def upgrade() -> None:
    _add_mode_column("trends")
    _add_mode_column("opportunities")

    if "trends" not in _tables():
        return
    existing = _constraint_names("trends")
    if NEW_INDEX in existing:
        return
    # batch_alter_table so SQLite (which cannot drop a constraint in place)
    # follows the same path as PostgreSQL and one migration behaves identically
    # on both.
    with op.batch_alter_table("trends") as batch:
        if OLD_INDEX in existing:
            batch.drop_constraint(OLD_INDEX, type_="unique")
        batch.create_unique_constraint(NEW_INDEX, list(TREND_KEY))


def downgrade() -> None:
    """Restore the old key, then drop the columns.

    The old constraint can only be restored if no two rows now differ solely by
    mode — which is exactly what a live-only evaluation creates. Rather than
    delete a user's live-only results to satisfy a narrower key, the downgrade
    refuses and says so.
    """
    if "trends" in _tables():
        duplicates = op.get_bind().execute(
            sa.text(
                "SELECT COUNT(*) FROM (SELECT subject_type, entity_id, topic_id, geo_scope "
                "FROM trends GROUP BY subject_type, entity_id, topic_id, geo_scope "
                "HAVING COUNT(*) > 1) AS clashes"
            )
        ).scalar_one()
        if duplicates:
            raise RuntimeError(
                f"{duplicates} subject(s) have both a live-only and a demo-inclusive "
                "evaluation. Downgrading would require deleting one of each pair. "
                "Remove the unwanted evaluations deliberately, then downgrade."
            )
        existing = _constraint_names("trends")
        with op.batch_alter_table("trends") as batch:
            if NEW_INDEX in existing:
                batch.drop_constraint(NEW_INDEX, type_="unique")
            if OLD_INDEX not in existing:
                batch.create_unique_constraint(OLD_INDEX, list(TREND_KEY[:-1]))

    for table in ("trends", "opportunities"):
        if table in _tables() and COLUMN in _columns(table):
            op.drop_index(op.f(f"ix_{table}_{COLUMN}"), table_name=table)
            op.drop_column(table, COLUMN)
