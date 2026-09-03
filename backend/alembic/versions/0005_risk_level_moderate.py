"""Repair risk levels stored as 'medium' when the engine has always said 'moderate'.

The `RiskLevel` enum declared ``MEDIUM = "medium"`` while `app.analytics.risk_engine`
has always emitted ``moderate``. The two never met, so the damage was silent and
one-directional:

* The preferences API validated ``max_risk_level`` against a hand-written pattern
  that accepted ``medium`` and rejected ``moderate``. A user could therefore only
  store a ceiling that no opportunity would ever match.
* The relevance engine read that ceiling as ``RISK_RANK.get(value, 3)``, so an
  unrecognised ``medium`` became rank 3 — **the most permissive setting there is**.
  Someone who asked for a moderate ceiling was shown very-high-risk candidates
  with no "outside your profile" flag at all. The filter failed open, in the
  unsafe direction.

Both causes are fixed in code. This migration repairs the data.

**Which columns are converted, and which are deliberately not.**

Converted — current state, describing what is true now:
    opportunities.risk_level          engine-owned; cannot produce 'medium' any
                                      more, so any row holding it is stale
    user_preferences.max_risk_level   the field the broken pattern wrote to
    user_profiles.max_risk_level      defensive; enum-validated since Phase 5
    watchlists.max_risk_level         user-settable

Left alone — immutable history, describing what was true at a moment in time:
    opportunity_decisions.risk_at_decision
    opportunity_scores.risk_level
    predictions.initial_risk_level

Rewriting an audit row to satisfy a later enum would be the more damaging
choice. Those rows record what the system said when a person made a decision,
and Phase 6 backtesting depends on that record being what it actually was, not
what we would prefer it had been. A historical 'medium' is an honest artefact of
a period when the enum and the engine disagreed; erasing it hides the defect
from the very analysis that would catch its consequences.
"""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op

revision: str = "0005_risk_moderate"
down_revision: str | None = "0004_phase5"
branch_labels = None
depends_on = None

#: (table, column) pairs holding a *current* risk level. History is excluded on
#: purpose; see the module docstring.
CURRENT_STATE_COLUMNS: tuple[tuple[str, str], ...] = (
    ("opportunities", "risk_level"),
    ("user_preferences", "max_risk_level"),
    ("user_profiles", "max_risk_level"),
    ("watchlists", "max_risk_level"),
)


def _tables() -> set[str]:
    return set(sa.inspect(op.get_bind()).get_table_names())


def upgrade() -> None:
    present = _tables()
    for table, column in CURRENT_STATE_COLUMNS:
        if table not in present:
            continue
        op.execute(
            sa.text(
                f"UPDATE {table} SET {column} = 'moderate' WHERE {column} = 'medium'"  # noqa: S608
            )
        )


def downgrade() -> None:
    """Deliberately does not put 'medium' back.

    A downgrade exists so the chain round-trips, but restoring 'medium' would
    reintroduce a value that fails open in the relevance engine. Undoing a
    safety repair is not a service to anyone, and the schema is unchanged either
    way, so there is nothing structural to reverse.
    """
