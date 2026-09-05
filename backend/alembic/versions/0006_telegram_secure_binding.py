"""Invalidate Telegram links established through the insecure verification flow.

Until now `POST /me/channels/verify` accepted the caller's own `link_code`
together with an `external_id` the caller simply asserted, and set
``verified = True``. Both halves of that exchange came from the same
authenticated request, so it proved only that the user could type a chat id --
including somebody else's. Any Telegram row verified that way is a claim the
system never actually checked, and it cannot be distinguished after the fact
from an honest one.

So this migration does not try to sort the honest ones from the rest. It revokes
them all and requires every affected user to relink through the webhook, where
Telegram itself supplies the chat id. Re-linking costs a user one message to the
bot; leaving one forged binding in place costs somebody their private alerts.

**What is changed**

* ``notification_channel_links`` gains ``link_code_expires_at``, so codes can
  expire rather than remaining valid for ever.
* Every ``channel = 'telegram'`` row is set ``verified = false``,
  ``verified_at = NULL``, and has any outstanding ``link_code`` cleared. The row
  is kept rather than deleted, so the user still sees the channel in the product
  and understands why it stopped working.
* ``external_id`` on those rows is rewritten to a unique ``revoked:<id>``
  placeholder. It has to be unique because ``ux_channel_external`` spans
  (channel, external_id), and it has to be *changed* because leaving the old
  chat id in place would let the row keep occupying the chat's slot and block
  the legitimate owner from relinking.

**What is deliberately left alone**

* Every other channel. Email and in-app were never affected by this defect, and
  revoking them would log people out of notifications they never mis-bound.
* The ``ux_channel_external`` constraint itself, which was already correct:
  (channel, external_id) unique is exactly the one-chat-one-account rule. It is
  preserved, not rebuilt, and it is what makes the webhook's concurrent-claim
  handling safe.
"""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op

revision: str = "0006_telegram_secure_binding"
down_revision: str | None = "0005_risk_moderate"
branch_labels = None
depends_on = None

TABLE = "notification_channel_links"


def _has_table() -> bool:
    return TABLE in set(sa.inspect(op.get_bind()).get_table_names())


def _has_column(name: str) -> bool:
    return name in {c["name"] for c in sa.inspect(op.get_bind()).get_columns(TABLE)}


def upgrade() -> None:
    if not _has_table():
        return

    if not _has_column("link_code_expires_at"):
        op.add_column(
            TABLE,
            sa.Column("link_code_expires_at", sa.DateTime(timezone=True), nullable=True),
        )

    # Revoke every Telegram binding, whatever state it is in. `external_id` is
    # moved aside so the chat it names is free for its real owner to claim.
    op.execute(
        sa.text(
            f"""
            UPDATE {TABLE}
               SET verified = false,
                   verified_at = NULL,
                   link_code = NULL,
                   link_code_expires_at = NULL,
                   external_id = 'revoked:' || CAST(id AS VARCHAR)
             WHERE channel = 'telegram'
            """  # noqa: S608 - TABLE is a module constant, not user input
        )
    )


def downgrade() -> None:
    """Drop the expiry column. The revocations are not undone.

    Restoring them is not possible and would not be desirable: the original
    `external_id` values were overwritten precisely because they could not be
    trusted, and re-verifying a link that was never proven would reintroduce the
    vulnerability this migration exists to close.
    """
    if not _has_table():
        return
    if _has_column("link_code_expires_at"):
        op.drop_column(TABLE, "link_code_expires_at")
