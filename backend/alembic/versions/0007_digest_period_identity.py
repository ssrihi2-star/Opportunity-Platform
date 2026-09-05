"""Give digests a stable period identity, so a repeated run cannot repeat a digest.

Until now a digest period was whatever the clock said when the task ran:
`period_start = now - window`, `period_end = now`. Two consequences followed. A
retry, a redelivered message, a second beat entry or an operator running the
phase by hand wrote a **second row for the same period**, and the user read the
same summary twice — `digests` had no uniqueness constraint at all, so nothing
in the database could object. And a retry that crossed midnight silently
summarised a *different* 24 hours than the attempt it was retrying.

So this migration adds the identity the code was missing:

* ``digests.period_key`` — ``daily:2026-09-06`` or ``weekly:2026-W36``, derived
  from the schedule's configured timezone and from the canonical period, not
  from the instant the task ran. It is passed through every retry, so a retry
  after midnight, after a week boundary or after a timezone configuration change
  still writes the period it was scheduled for, over the same content window.
* ``ux_digest_period`` — a **unique index** on ``(user_id, frequency,
  period_key)``. Unique index rather than table constraint because it is
  identical in effect on PostgreSQL and it is the only form SQLite can add to an
  existing table, so one migration behaves the same everywhere.

**What is deliberately left alone**

* Every existing row. ``period_key`` is added nullable and is **not**
  backfilled. Historical rows were written over a rolling window whose
  boundaries are real instants, not canonical local days, so any key invented
  for them would be a guess — and two digests generated on the same day would
  then collide, which could only be resolved by deleting one of a user's
  existing rows. Preserving history means leaving them NULL.
* NULL is distinct from NULL in a unique index on both PostgreSQL and SQLite, so
  those historical rows stay legal, and so do on-demand digests
  (``POST /me/digests``), which remain repeatable exactly as before. Only rows
  written by the schedule carry a key, and only those are deduplicated.
"""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op

revision: str = "0007_digest_period_identity"
down_revision: str | None = "0006_telegram_secure_binding"
branch_labels = None
depends_on = None

TABLE = "digests"
COLUMN = "period_key"
INDEX = "ux_digest_period"


def _has_table() -> bool:
    return TABLE in set(sa.inspect(op.get_bind()).get_table_names())


def _has_column(name: str) -> bool:
    return name in {c["name"] for c in sa.inspect(op.get_bind()).get_columns(TABLE)}


def _has_index(name: str) -> bool:
    return name in {i["name"] for i in sa.inspect(op.get_bind()).get_indexes(TABLE)}


def upgrade() -> None:
    if not _has_table():
        return

    if not _has_column(COLUMN):
        op.add_column(TABLE, sa.Column(COLUMN, sa.String(40), nullable=True))

    if not _has_index(INDEX):
        op.create_index(INDEX, TABLE, ["user_id", "frequency", COLUMN], unique=True)


def downgrade() -> None:
    """Drop the identity again.

    Rows written while it existed keep their key until the column goes; the
    digests themselves are derived summaries, so nothing else needs repairing.
    """
    if not _has_table():
        return
    if _has_index(INDEX):
        op.drop_index(INDEX, table_name=TABLE)
    if _has_column(COLUMN):
        op.drop_column(TABLE, COLUMN)
