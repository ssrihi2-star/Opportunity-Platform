"""Proving that a Telegram chat is bound only by the chat itself.

The defect these tests exist for: `POST /me/channels/verify` used to accept the
caller's own `link_code` together with an `external_id` the caller simply
asserted, and set ``verified = True``. Both halves came from the same
authenticated request, so the exchange proved nothing about who controlled the
chat. Anyone could bind any chat id — including one belonging to somebody else —
and start receiving that chat's alerts.

What replaces it is a direction of travel. The code is issued to the user, the
user sends it *into the chat*, and Telegram delivers an update to the webhook
carrying the chat id **it** observed. The chat id is therefore established by
Telegram and never by the caller.

Every Telegram interaction here is a synthetic update posted at the webhook. No
network call is made and no live message is sent.
"""

from __future__ import annotations

import asyncio
import uuid
from datetime import UTC, datetime, timedelta

import pytest
import sqlalchemy as sa
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from app.api.v1.telegram import REPLY_OK
from app.core.config import get_settings
from app.models.models import NotificationChannelLink

pytestmark = pytest.mark.asyncio

WEBHOOK = "/api/v1/integrations/telegram/webhook"
SECRET = "test-webhook-secret-value"
SECRET_HEADER = "X-Telegram-Bot-Api-Secret-Token"


@pytest.fixture(autouse=True)
def _configured_secret(monkeypatch):
    """Give the server a webhook secret, as a real deployment would have.

    `get_settings` is lru_cached, so the cache is cleared on the way in and on
    the way out; otherwise one test's configuration would leak into the rest of
    the suite.
    """
    monkeypatch.setenv("TELEGRAM_WEBHOOK_SECRET", SECRET)
    get_settings.cache_clear()
    yield
    get_settings.cache_clear()


def update(chat_id: int | str, text: str, *, chat_type: str = "private") -> dict:
    """One Telegram update, shaped the way Telegram actually sends them."""
    return {
        "update_id": 1,
        "message": {
            "message_id": 1,
            "date": 1_700_000_000,
            "chat": {"id": chat_id, "type": chat_type},
            "from": {"id": chat_id, "is_bot": False, "first_name": "Test"},
            "text": text,
        },
    }


async def start_link(client, headers) -> str:
    """Ask for a code the way the product does, and return it."""
    started = await client.post("/api/v1/me/channels", json={"channel": "telegram"}, headers=headers)
    assert started.status_code == 201, started.text
    code = started.json()["link_code"]
    assert code and started.json()["verified"] is False
    return code


async def link_row(session: AsyncSession, code_or_chat: str) -> NotificationChannelLink | None:
    return (
        await session.execute(
            sa.select(NotificationChannelLink).where(
                sa.or_(
                    NotificationChannelLink.link_code == code_or_chat,
                    NotificationChannelLink.external_id == code_or_chat,
                )
            )
        )
    ).scalar_one_or_none()


# --------------------------------------------------------------- the old bypass
async def test_the_caller_supplied_verification_bypass_is_gone(client, admin_headers, session):
    """The original attack, replayed verbatim. It must no longer bind anything.

    This is the whole point of the change: presenting your own code together
    with a chat id you merely claim must not produce a verified link.
    """
    code = await start_link(client, admin_headers)

    response = await client.post(
        "/api/v1/me/channels/verify",
        json={"channel": "telegram", "link_code": code, "external_id": "999888777"},
        headers=admin_headers,
    )

    # The route still answers — clients that poll it are not broken — but it
    # reports status only.
    assert response.status_code == 200, response.text
    assert response.json()["verified"] is False, "asserting a chat id must never verify a link"

    listed = (await client.get("/api/v1/me/channels", headers=admin_headers)).json()
    assert listed[0]["verified"] is False

    stored = await link_row(session, code)
    assert stored is not None
    assert stored.verified is False
    assert stored.external_id != "999888777", "the asserted chat id must not be stored"


async def test_the_bypass_cannot_claim_a_chat_belonging_to_someone_else(
    client, admin_headers, second_headers, session
):
    """The consequence that made it a vulnerability, not just a weak check."""
    victim_code = await start_link(client, admin_headers)
    victim_chat = "555000111"
    bound = await client.post(WEBHOOK, json=update(victim_chat, f"/link {victim_code}"),
                              headers={SECRET_HEADER: SECRET})
    assert bound.status_code == 200
    assert bound.json()["reply"].startswith("Linked")

    # The attacker now tries to attach the victim's chat to their own account.
    attacker_code = await start_link(client, second_headers)
    stolen = await client.post(
        "/api/v1/me/channels/verify",
        json={"channel": "telegram", "link_code": attacker_code, "external_id": victim_chat},
        headers=second_headers,
    )
    assert stolen.json()["verified"] is False

    rows = (
        await session.execute(
            sa.select(NotificationChannelLink).where(
                NotificationChannelLink.external_id == victim_chat
            )
        )
    ).scalars().all()
    assert len(rows) == 1, "the victim's chat is bound exactly once"
    assert rows[0].verified is True


# ------------------------------------------------------------ the webhook itself
async def test_a_valid_private_update_binds_the_chat(client, admin_headers, session):
    code = await start_link(client, admin_headers)

    response = await client.post(
        WEBHOOK, json=update(123456, f"/link {code}"), headers={SECRET_HEADER: SECRET}
    )

    assert response.status_code == 200, response.text
    assert response.json()["reply"].startswith("Linked")

    stored = await link_row(session, "123456")
    assert stored is not None
    assert stored.verified is True
    assert stored.external_id == "123456", "the chat id comes from Telegram, not the caller"
    assert stored.link_code is None, "the code is consumed"
    assert stored.link_code_expires_at is None

    listed = (await client.get("/api/v1/me/channels", headers=admin_headers)).json()
    assert listed[0]["verified"] is True
    assert listed[0]["link_code"] is None


async def test_the_bot_accepts_an_addressed_command(client, admin_headers, session):
    """In groups Telegram rewrites /link as /link@botname; be tolerant of it."""
    code = await start_link(client, admin_headers)
    response = await client.post(
        WEBHOOK, json=update(7788, f"/link@ois_alerts_bot {code}"), headers={SECRET_HEADER: SECRET}
    )
    assert response.json()["reply"].startswith("Linked")
    stored = await link_row(session, "7788")
    assert stored is not None and stored.verified is True


# ------------------------------------------------------------------ authentication
async def test_a_missing_secret_header_is_refused(client, admin_headers, session):
    code = await start_link(client, admin_headers)
    response = await client.post(WEBHOOK, json=update(1, f"/link {code}"))
    assert response.status_code == 401
    assert (await link_row(session, code)).verified is False


async def test_a_wrong_secret_header_is_refused(client, admin_headers, session):
    code = await start_link(client, admin_headers)
    response = await client.post(
        WEBHOOK, json=update(1, f"/link {code}"), headers={SECRET_HEADER: "not-the-secret"}
    )
    assert response.status_code == 401
    assert (await link_row(session, code)).verified is False


async def test_a_secret_that_is_a_prefix_of_the_real_one_is_refused(client, admin_headers):
    """Guards against a comparison that stops at the first difference."""
    code = await start_link(client, admin_headers)
    response = await client.post(
        WEBHOOK, json=update(1, f"/link {code}"), headers={SECRET_HEADER: SECRET[:-1]}
    )
    assert response.status_code == 401


async def test_the_webhook_fails_closed_when_no_secret_is_configured(
    client, admin_headers, session, monkeypatch
):
    """With no secret, every caller is indistinguishable from Telegram."""
    code = await start_link(client, admin_headers)
    monkeypatch.setenv("TELEGRAM_WEBHOOK_SECRET", "")
    get_settings.cache_clear()

    # Even presenting the *correct* header cannot help: there is nothing to match.
    for headers in ({}, {SECRET_HEADER: SECRET}, {SECRET_HEADER: ""}):
        response = await client.post(WEBHOOK, json=update(1, f"/link {code}"), headers=headers)
        assert response.status_code == 503, response.text

    assert (await link_row(session, code)).verified is False


async def test_a_whitespace_only_secret_is_treated_as_unconfigured(client, monkeypatch):
    monkeypatch.setenv("TELEGRAM_WEBHOOK_SECRET", "   ")
    get_settings.cache_clear()
    response = await client.post(
        WEBHOOK, json=update(1, "/link abcdef"), headers={SECRET_HEADER: "   "}
    )
    assert response.status_code == 503


# ------------------------------------------------------------------------- codes
async def test_an_unknown_code_binds_nothing(client, session):
    response = await client.post(
        WEBHOOK, json=update(4242, "/link totally-made-up"), headers={SECRET_HEADER: SECRET}
    )
    assert response.status_code == 200
    assert "not valid" in response.json()["reply"]
    assert await link_row(session, "4242") is None


async def test_an_expired_code_binds_nothing(client, admin_headers, session):
    code = await start_link(client, admin_headers)

    # Age the code past its expiry, the way the clock would.
    row = await link_row(session, code)
    row.link_code_expires_at = datetime.now(UTC) - timedelta(minutes=1)
    await session.commit()

    response = await client.post(
        WEBHOOK, json=update(31337, f"/link {code}"), headers={SECRET_HEADER: SECRET}
    )
    assert "not valid" in response.json()["reply"]

    await session.refresh(row)
    assert row.verified is False
    assert row.external_id != "31337"


async def test_a_code_works_exactly_once(client, admin_headers, session):
    code = await start_link(client, admin_headers)
    first = await client.post(
        WEBHOOK, json=update(1111, f"/link {code}"), headers={SECRET_HEADER: SECRET}
    )
    assert first.json()["reply"].startswith("Linked")

    # A second chat replaying the same code must get nothing.
    second = await client.post(
        WEBHOOK, json=update(2222, f"/link {code}"), headers={SECRET_HEADER: SECRET}
    )
    assert "not valid" in second.json()["reply"]

    assert await link_row(session, "2222") is None
    still_mine = await link_row(session, "1111")
    assert still_mine is not None and still_mine.verified is True


async def test_the_reply_does_not_distinguish_unknown_from_expired_or_used(
    client, admin_headers, session
):
    """Otherwise the bot becomes an oracle for guessing codes."""
    code = await start_link(client, admin_headers)
    used = await client.post(
        WEBHOOK, json=update(9001, f"/link {code}"), headers={SECRET_HEADER: SECRET}
    )
    assert used.json()["reply"].startswith("Linked")

    replay = await client.post(
        WEBHOOK, json=update(9002, f"/link {code}"), headers={SECRET_HEADER: SECRET}
    )
    unknown = await client.post(
        WEBHOOK, json=update(9003, "/link never-existed-at-all"), headers={SECRET_HEADER: SECRET}
    )
    assert replay.json()["reply"] == unknown.json()["reply"]


async def test_the_response_never_echoes_the_code(client, admin_headers):
    code = await start_link(client, admin_headers)
    response = await client.post(
        WEBHOOK, json=update(5150, f"/link {code}"), headers={SECRET_HEADER: SECRET}
    )
    assert code not in response.text
    assert SECRET not in response.text


# ------------------------------------------------------------------ chat types
@pytest.mark.parametrize("chat_type", ["group", "supergroup", "channel"])
async def test_only_private_chats_may_be_bound(client, admin_headers, session, chat_type):
    """A group binding would put one person's alerts in front of its members."""
    code = await start_link(client, admin_headers)

    response = await client.post(
        WEBHOOK,
        json=update(-1001234, f"/link {code}", chat_type=chat_type),
        headers={SECRET_HEADER: SECRET},
    )

    assert response.status_code == 200
    assert "direct message" in response.json()["reply"]

    assert await link_row(session, "-1001234") is None
    assert (await link_row(session, code)).verified is False, "the code is still unused"


# ------------------------------------------------------- one chat, one account
async def test_a_linked_chat_cannot_be_claimed_by_another_account(
    client, admin_headers, second_headers, session
):
    chat = "606060"
    first_code = await start_link(client, admin_headers)
    assert (
        await client.post(WEBHOOK, json=update(chat, f"/link {first_code}"),
                          headers={SECRET_HEADER: SECRET})
    ).json()["reply"].startswith("Linked")

    # A second account, a valid code of its own, the same chat.
    second_code = await start_link(client, second_headers)
    response = await client.post(
        WEBHOOK, json=update(chat, f"/link {second_code}"), headers={SECRET_HEADER: SECRET}
    )

    assert "already linked" in response.json()["reply"]
    rows = (
        await session.execute(
            sa.select(NotificationChannelLink).where(
                NotificationChannelLink.external_id == chat
            )
        )
    ).scalars().all()
    assert len(rows) == 1, "the unique constraint holds; the chat is bound once"

    # And the loser's own pending link is left alone rather than half-written.
    loser = await link_row(session, second_code)
    assert loser is not None and loser.verified is False


# ------------------------------------------------------------------ concurrency
async def test_concurrent_updates_produce_exactly_one_binding(client, admin_headers, engine):
    """Two updates racing with the same code must not both succeed.

    The claim is a single UPDATE guarded on the code still being present, so the
    loser matches zero rows. Note the database: this executes on SQLite, which
    serialises writers, so it demonstrates the *logic* holds rather than proving
    PostgreSQL's concurrency behaviour. The row-level guarantee under real
    parallelism comes from ux_channel_external, exercised separately.
    """
    code = await start_link(client, admin_headers)

    replies = await asyncio.gather(
        *[
            client.post(
                WEBHOOK, json=update(70000 + i, f"/link {code}"), headers={SECRET_HEADER: SECRET}
            )
            for i in range(5)
        ]
    )

    linked = [r for r in replies if r.json().get("reply", "").startswith("Linked")]
    assert len(linked) == 1, f"expected exactly one success, got {[r.json() for r in replies]}"

    maker = async_sessionmaker(engine, expire_on_commit=False, class_=AsyncSession)
    async with maker() as s:
        verified = (
            await s.execute(
                sa.select(NotificationChannelLink).where(
                    NotificationChannelLink.channel == "telegram",
                    NotificationChannelLink.verified.is_(True),
                )
            )
        ).scalars().all()
    assert len(verified) == 1


# ------------------------------------------------------- malformed and irrelevant
@pytest.mark.parametrize(
    "body",
    [
        {},
        {"update_id": 1},
        {"message": None},
        {"message": {}},
        {"message": {"chat": None, "text": "/link abcdefgh"}},
        {"message": {"chat": {"id": None, "type": "private"}, "text": "/link abcdefgh"}},
        {"message": {"chat": {"type": "private"}, "text": "/link abcdefgh"}},
        {"message": {"chat": {"id": 5, "type": "private"}}},
        {"message": {"chat": {"id": 5, "type": "private"}, "text": 12345}},
        {"message": {"chat": {"id": 5, "type": "private"}, "text": "hello there"}},
        {"message": {"chat": {"id": 5, "type": "private"}, "text": "/link"}},
        {"message": {"chat": {"id": 5, "type": "private"}, "text": "/linkage abcdefgh"}},
        {"edited_message": {"chat": {"id": 5, "type": "private"}, "text": "not a command"}},
        {"my_chat_member": {"chat": {"id": 5, "type": "private"}}},
        {"poll": {"id": "1"}},
    ],
)
async def test_malformed_or_irrelevant_updates_are_handled_safely(client, body):
    """Telegram retries anything that is not 2xx, so none of these may raise."""
    response = await client.post(WEBHOOK, json=body, headers={SECRET_HEADER: SECRET})
    assert response.status_code == 200, response.text
    assert response.json()["ok"] is True


async def test_a_non_json_body_does_not_crash_the_webhook(client):
    response = await client.post(
        WEBHOOK,
        content=b"this is not json",
        headers={SECRET_HEADER: SECRET, "Content-Type": "application/json"},
    )
    assert response.status_code == 200
    assert response.json() == {"ok": True, "handled": False}


# ------------------------------------------------------------------ status polling
async def test_the_owner_can_poll_verification_through_get_me_channels(
    client, admin_headers, session
):
    """Creation -> webhook redemption -> polling must show the link succeeded.

    The regression this pins: `GET /me/channels` is the supported way to watch a
    link complete, and it has to keep telling the truth across the whole
    lifecycle. An earlier revision of the legacy verify route tried to serve
    this purpose and could not — after redemption the code is cleared and
    `external_id` holds the real chat id, so nothing matched its lookup and a
    *successful* link reported 404. Anyone polling that route would have shown
    the user a failure at the exact moment it worked.
    """
    chat = "776655"

    # 1. Created: present, pending, and carrying no chat of its own yet.
    code = await start_link(client, admin_headers)
    listed = (await client.get("/api/v1/me/channels", headers=admin_headers)).json()
    assert len(listed) == 1
    assert listed[0]["channel"] == "telegram"
    assert listed[0]["verified"] is False
    assert listed[0]["verified_at"] is None
    assert listed[0]["link_code"] is None, "the code is never echoed back by the listing"

    # 2. Redeemed, by Telegram, in a private chat.
    bound = await client.post(WEBHOOK, json=update(chat, f"/link {code}"), headers={SECRET_HEADER: SECRET})
    assert bound.status_code == 200
    assert bound.json()["reply"] == REPLY_OK

    # 3. Polled: the same endpoint now reports success.
    polled = (await client.get("/api/v1/me/channels", headers=admin_headers)).json()
    assert len(polled) == 1, "redemption updates the existing row, it does not add one"
    assert polled[0]["id"] == listed[0]["id"]
    assert polled[0]["verified"] is True, "polling must reflect the completed link"
    assert polled[0]["verified_at"] is not None
    assert polled[0]["link_code"] is None

    # The consumed code is really gone, and the stored chat is the one Telegram
    # reported rather than anything a caller supplied.
    stored = (
        await session.execute(
            sa.select(NotificationChannelLink).where(NotificationChannelLink.external_id == chat)
        )
    ).scalar_one()
    assert stored.verified is True
    assert stored.link_code is None, "a consumed code is not retained"
    assert stored.link_code_expires_at is None


async def test_the_legacy_verify_route_404s_once_the_code_is_redeemed(client, admin_headers):
    """The legacy route's documented behaviour, pinned so it cannot drift.

    It reports on a *pending* link. Once the webhook consumes the code there is
    no pending link left, so it 404s — and that 404 covers both "never existed"
    and "already succeeded". The message has to point at `GET /me/channels`,
    because on its own the status code would read as failure.
    """
    code = await start_link(client, admin_headers)

    # While pending: answers, and truthfully says "not yet".
    pending = await client.post(
        "/api/v1/me/channels/verify",
        json={"channel": "telegram", "link_code": code, "external_id": "ignored"},
        headers=admin_headers,
    )
    assert pending.status_code == 200
    assert pending.json()["verified"] is False
    assert pending.json()["instructions"], "a pending link still explains what to do"

    await client.post(WEBHOOK, json=update("998877", f"/link {code}"), headers={SECRET_HEADER: SECRET})

    # After redemption: no pending link under that code.
    done = await client.post(
        "/api/v1/me/channels/verify",
        json={"channel": "telegram", "link_code": code, "external_id": "ignored"},
        headers=admin_headers,
    )
    assert done.status_code == 404
    assert "GET /me/channels" in done.json()["detail"], (
        "the 404 must send the caller to the endpoint that reports success"
    )

    # ...while the supported endpoint reports the truth.
    assert (await client.get("/api/v1/me/channels", headers=admin_headers)).json()[0]["verified"] is True


# --------------------------------------------------------- delivery and unlinking
async def test_an_unverified_link_receives_no_external_delivery(client, admin_headers, session):
    """Section 21, restated against the new flow: pending means nothing is sent."""
    from app.models.models import AlertRule, User
    from app.services.alerts import _channels_for

    await start_link(client, admin_headers)  # pending, never redeemed
    user = (
        await session.execute(sa.select(User).where(User.email == "admin@acme-corp.com"))
    ).scalar_one()
    rule = AlertRule(
        user_id=user.id, name="r", trigger="score_threshold", channels=["in_app", "telegram"]
    )
    session.add(rule)
    await session.commit()

    pairs = await _channels_for(session, user=user, rule=rule)
    assert [c for c, _ in pairs] == ["in_app"], "an unverified chat is never a delivery target"


async def test_a_verified_link_becomes_a_delivery_target(client, admin_headers, session):
    from app.models.models import AlertRule, User
    from app.services.alerts import _channels_for

    code = await start_link(client, admin_headers)
    await client.post(WEBHOOK, json=update(818181, f"/link {code}"), headers={SECRET_HEADER: SECRET})

    user = (
        await session.execute(sa.select(User).where(User.email == "admin@acme-corp.com"))
    ).scalar_one()
    rule = AlertRule(
        user_id=user.id, name="r", trigger="score_threshold", channels=["in_app", "telegram"]
    )
    session.add(rule)
    await session.commit()

    pairs = await _channels_for(session, user=user, rule=rule)
    assert ("telegram", "818181") in pairs


async def test_only_the_owner_can_unlink(client, admin_headers, second_headers, session):
    code = await start_link(client, admin_headers)
    await client.post(WEBHOOK, json=update(343434, f"/link {code}"), headers={SECRET_HEADER: SECRET})
    link_id = (await client.get("/api/v1/me/channels", headers=admin_headers)).json()[0]["id"]

    stolen = await client.delete(f"/api/v1/me/channels/{link_id}", headers=second_headers)
    assert stolen.status_code == 404
    assert (await client.get("/api/v1/me/channels", headers=admin_headers)).json(), "still linked"

    mine = await client.delete(f"/api/v1/me/channels/{link_id}", headers=admin_headers)
    assert mine.status_code == 204
    assert (await client.get("/api/v1/me/channels", headers=admin_headers)).json() == []


async def test_relinking_the_same_chat_after_unlinking_works(client, admin_headers, session):
    """Unlinking must genuinely free the chat, or a user can never recover."""
    chat = "424242"
    code = await start_link(client, admin_headers)
    await client.post(WEBHOOK, json=update(chat, f"/link {code}"), headers={SECRET_HEADER: SECRET})
    link_id = (await client.get("/api/v1/me/channels", headers=admin_headers)).json()[0]["id"]
    await client.delete(f"/api/v1/me/channels/{link_id}", headers=admin_headers)

    again = await start_link(client, admin_headers)
    response = await client.post(
        WEBHOOK, json=update(chat, f"/link {again}"), headers={SECRET_HEADER: SECRET}
    )
    assert response.json()["reply"].startswith("Linked")


# -------------------------------------------------------------------- migration
def _apply_migration_0006(sync_conn) -> None:
    """Execute the real ``upgrade()`` from revision 0006 on a sync connection.

    The suite builds its schema with ``Base.metadata.create_all`` rather than by
    running Alembic, and the pre-existing 0003 revision cannot replay on SQLite
    at all (it does ``ALTER ... ADD CONSTRAINT``). Loading the revision module
    directly and driving it through Alembic's operations context is therefore
    the closest we can get to the production upgrade while still exercising the
    migration's own code rather than a restatement of it.
    """
    import importlib.util
    from pathlib import Path

    from alembic.migration import MigrationContext
    from alembic.operations import Operations

    path = Path(__file__).resolve().parents[1] / "alembic" / "versions" / "0006_telegram_secure_binding.py"
    spec = importlib.util.spec_from_file_location("_migration_0006", path)
    assert spec and spec.loader
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)

    with Operations.context(MigrationContext.configure(sync_conn)):
        module.upgrade()


def _columns(sync_conn) -> set[str]:
    return {r[1] for r in sync_conn.exec_driver_sql("PRAGMA table_info(notification_channel_links)")}


async def _make_pre_0006_schema(engine) -> str:
    """Reshape the table to how it looked *before* 0006, and return the user id.

    The suite's schema comes from ``Base.metadata.create_all`` against the
    current models, which already carry ``link_code_expires_at``. Running the
    migration against that only ever exercises the "column already present"
    branch of the guard, so the column add itself goes untested. Dropping the
    column reproduces the real starting state of an existing deployment.

    Rows are then seeded with raw SQL rather than the ORM, because the mapped
    class knows about a column this table deliberately no longer has.
    """
    from app.models.models import User

    maker = async_sessionmaker(engine, expire_on_commit=False, class_=AsyncSession)
    async with maker() as s:
        s.add(User(email="legacy@example.org", password_hash="x", full_name="Legacy",
                   role="viewer", is_active=True))
        await s.commit()

    async with engine.begin() as conn:
        user_id = (await conn.exec_driver_sql("SELECT id FROM users LIMIT 1")).scalar_one()
        await conn.exec_driver_sql(
            "ALTER TABLE notification_channel_links DROP COLUMN link_code_expires_at"
        )
        assert "link_code_expires_at" not in await conn.run_sync(_columns), (
            "the fixture must start from a genuinely pre-0006 table"
        )
        for channel, external_id, code, verified, verified_at in [
            # Verified through the insecure flow: a real chat id, never proven.
            ("telegram", "chat-legacy", None, 1, "2026-01-01 00:00:00"),
            # An outstanding legacy code, issued under the old rules.
            ("telegram", "pending:oldcode", "oldcode", 0, None),
            # A different channel, which the defect never touched.
            ("email", "legacy@example.org", None, 1, "2026-01-01 00:00:00"),
        ]:
            await conn.exec_driver_sql(
                "INSERT INTO notification_channel_links"
                " (id,user_id,channel,external_id,link_code,verified,verified_at,"
                "  created_at,updated_at)"
                " VALUES (?,?,?,?,?,?,?,datetime('now'),datetime('now'))",
                (uuid.uuid4().hex, user_id, channel, external_id, code, verified, verified_at),
            )
    return user_id


async def test_the_migration_upgrades_a_legacy_table_without_the_expiry_column(engine):
    """0006 against the schema an existing deployment actually has.

    Three things have to hold at once: the new column is added, every Telegram
    binding and outstanding code is invalidated, and no other channel is
    disturbed. The migration's own ``upgrade()`` is executed — not a copy of its
    SQL — so this fails if 0006 is ever weakened.
    """
    await _make_pre_0006_schema(engine)

    async with engine.begin() as conn:
        before = await conn.run_sync(_columns)
        assert "link_code_expires_at" not in before
        await conn.run_sync(_apply_migration_0006)
        after = await conn.run_sync(_columns)

    assert "link_code_expires_at" in after, "the migration adds the expiry column"
    assert before | {"link_code_expires_at"} == after, "and changes no other column"

    maker = async_sessionmaker(engine, expire_on_commit=False, class_=AsyncSession)
    async with maker() as s:
        rows = (await s.execute(sa.select(NotificationChannelLink))).scalars().all()
        telegram_rows = [r for r in rows if r.channel == "telegram"]
        email_rows = [r for r in rows if r.channel == "email"]

        assert len(telegram_rows) == 2
        for r in telegram_rows:
            assert r.verified is False, "every legacy Telegram binding is revoked"
            assert r.verified_at is None
            assert r.link_code is None, "outstanding legacy codes are cleared"
            assert r.link_code_expires_at is None
            assert r.external_id.startswith("revoked:"), "the chat slot is freed"
        assert len({r.external_id for r in telegram_rows}) == 2, (
            "each placeholder stays unique, or ux_channel_external would be violated"
        )
        # The old chat id is gone entirely, so its rightful owner can relink it.
        assert "chat-legacy" not in {r.external_id for r in telegram_rows}

        assert len(email_rows) == 1
        assert email_rows[0].verified is True, "other channels are preserved"
        assert email_rows[0].verified_at is not None
        assert email_rows[0].external_id == "legacy@example.org"


async def test_the_migration_is_safe_to_re_run_on_an_already_upgraded_schema(engine):
    """The guarded column add must not fail when the column is already there.

    This is the other branch of ``_has_column`` — the case a re-run, or a
    deployment already carrying the current models, actually hits.
    """
    await _make_pre_0006_schema(engine)

    async with engine.begin() as conn:
        await conn.run_sync(_apply_migration_0006)
    async with engine.begin() as conn:
        await conn.run_sync(_apply_migration_0006)  # must not raise
        assert "link_code_expires_at" in await conn.run_sync(_columns)

    maker = async_sessionmaker(engine, expire_on_commit=False, class_=AsyncSession)
    async with maker() as s:
        rows = (await s.execute(sa.select(NotificationChannelLink))).scalars().all()
        assert all(r.verified is False for r in rows if r.channel == "telegram")
        assert all(r.verified is True for r in rows if r.channel == "email")
