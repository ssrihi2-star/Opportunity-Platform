"""The receiving half of Telegram linking: how a chat proves it is itself.

The old flow could not do this. `POST /me/channels/verify` took a `link_code`
the caller had just been handed by `POST /me/channels`, plus an `external_id`
the caller simply asserted, and set ``verified = True``. Both halves came from
the same authenticated request, so the pair proved only that the user could
type. Anyone could bind any chat id — including a chat belonging to somebody
else — to their own account and start receiving that chat's alerts.

What proves control here is the direction of travel. The code is issued to the
user, the user sends it *into the chat*, and Telegram delivers the update to
this endpoint carrying the chat id it observed. The chat id is therefore
established by Telegram, never by the caller. Someone who knows a chat id still
cannot bind it, because they cannot make Telegram send us an update from a chat
they do not control.

Two things guard the endpoint itself:

* the secret header, which is what makes an update trustworthy at all. It is a
  shared secret echoed by Telegram, compared in constant time. It is **not** a
  signature: it says the caller knows the secret, and nothing about the body.
* the requirement that the secret be configured. With no secret there is no way
  to tell Telegram from a stranger with the URL, so the endpoint refuses to
  process anything rather than accepting everything.

Nothing here logs a code, a token or the secret.
"""

from __future__ import annotations

import hmac
import re
import secrets
from datetime import UTC, datetime, timedelta
from typing import Any

import sqlalchemy as sa
from fastapi import APIRouter, Depends, Header, HTTPException, Request, status
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.deps import db_session
from app.core.config import get_settings
from app.core.logging import get_logger
from app.models.models import NotificationChannelLink

router = APIRouter(tags=["telegram"])
logger = get_logger(__name__)

CHANNEL = "telegram"

#: "/link CODE", optionally addressed to the bot as "/link@my_bot CODE".
LINK_COMMAND = re.compile(r"^/link(?:@[A-Za-z0-9_]+)?\s+(?P<code>\S{6,64})\s*$")

#: Telegram's own vocabulary. Only the first is a conversation with one person;
#: the rest have more than one member and must never be bound to an account.
PRIVATE_CHAT = "private"

# Replies are deliberately identical for "no such code", "expired" and "already
# used". Distinguishing them would turn the bot into an oracle that confirms
# whether a guessed code was ever real.
REPLY_OK = "Linked. Alerts for your account will arrive in this chat."
REPLY_BAD_CODE = "That link code is not valid, has expired, or has already been used."
REPLY_TAKEN = "This chat is already linked to an account. One chat, one account."
REPLY_NOT_PRIVATE = "Linking only works in a direct message with the bot, not in a group or channel."


def _authentic(supplied: str | None, expected: str) -> bool:
    """Constant-time comparison of the shared secret.

    `compare_digest` rather than `==` so the comparison cannot be timed
    character by character. A missing header is a failure, never a pass.
    """
    if not supplied:
        return False
    return hmac.compare_digest(supplied, expected)


def _extract_command(update: dict[str, Any]) -> tuple[str | None, str | None, str | None]:
    """Pull (chat_id, chat_type, code) out of an update, tolerating anything.

    Telegram sends many kinds of update and this endpoint cares about exactly
    one. Everything else — an edited message, a photo, a poll, a malformed body,
    a truncated dict — has to be ignored without raising, because an exception
    here is a 500 that Telegram will retry indefinitely.
    """
    if not isinstance(update, dict):
        return None, None, None
    # A plain message, or the same text arriving as an edit.
    message = update.get("message") or update.get("edited_message")
    if not isinstance(message, dict):
        return None, None, None

    chat = message.get("chat")
    if not isinstance(chat, dict):
        return None, None, None
    raw_id = chat.get("id")
    # Telegram sends numeric ids; anything else is not something we can store.
    if not isinstance(raw_id, int | str) or isinstance(raw_id, bool):
        return None, None, None
    chat_id = str(raw_id).strip()
    if not chat_id or len(chat_id) > 200:
        return None, None, None

    chat_type = chat.get("type") if isinstance(chat.get("type"), str) else None

    text = message.get("text")
    if not isinstance(text, str):
        return chat_id, chat_type, None
    match = LINK_COMMAND.match(text.strip())
    return chat_id, chat_type, match.group("code") if match else None


async def _consume_code(
    session: AsyncSession, *, code: str, chat_id: str, now: datetime
) -> str:
    """Atomically turn one unused, unexpired code into one verified binding.

    The single UPDATE is what makes this safe under concurrency. Both the "is
    this code still pending" test and the "claim it" write happen in one
    statement, so two simultaneous updates cannot both match the same row: the
    first flips `link_code` to NULL and the second matches nothing. A read
    followed by a separate write would leave exactly that gap open.

    The database's own unique constraint on (channel, external_id) is the second
    line of defence, catching the case where two different codes race to claim
    the same chat. That collision arrives as an IntegrityError and is rolled
    back to a controlled answer rather than a 500.
    """
    claim = (
        sa.update(NotificationChannelLink)
        .where(
            NotificationChannelLink.channel == CHANNEL,
            NotificationChannelLink.link_code == code,
            NotificationChannelLink.verified.is_(False),
            NotificationChannelLink.link_code_expires_at.is_not(None),
            NotificationChannelLink.link_code_expires_at > now,
        )
        .values(
            external_id=chat_id,
            verified=True,
            verified_at=now,
            link_code=None,
            link_code_expires_at=None,
        )
    )

    # The collision can surface either when the statement runs or when the
    # transaction commits, depending on the engine and on whether the constraint
    # is deferred. Both paths have to roll back to the same controlled answer,
    # or a claimed chat becomes a 500 that Telegram then retries forever.
    try:
        result = await session.execute(claim)
        if result.rowcount == 0:
            # Unknown, expired, or already consumed — deliberately indistinguishable.
            await session.rollback()
            return REPLY_BAD_CODE
        await session.commit()
    except IntegrityError:
        # ux_channel_external: this chat already belongs to an account. The
        # rollback leaves the pending row untouched and nothing half-written.
        await session.rollback()
        return REPLY_TAKEN
    return REPLY_OK


@router.post("/integrations/telegram/webhook", include_in_schema=False)
async def telegram_webhook(
    request: Request,
    session: AsyncSession = Depends(db_session),
    x_telegram_bot_api_secret_token: str | None = Header(default=None),
) -> dict[str, Any]:
    """Receive one update from Telegram.

    Always answers 200 with a body Telegram ignores, *except* when
    authentication fails. Telegram retries anything that is not a 2xx, so a
    failed lookup or an update we do not care about must not look like an
    outage.
    """
    settings = get_settings()
    secret = settings.TELEGRAM_WEBHOOK_SECRET

    # Fail closed. Without a configured secret every caller is indistinguishable
    # from Telegram, so there is nothing this endpoint could safely do.
    if not secret or not secret.strip():
        logger.warning("telegram.webhook_disabled_no_secret")
        raise HTTPException(
            status.HTTP_503_SERVICE_UNAVAILABLE, "Telegram webhook is not configured."
        )

    if not _authentic(x_telegram_bot_api_secret_token, secret):
        # No detail about which part failed, and never the secret itself.
        logger.warning("telegram.webhook_rejected")
        raise HTTPException(status.HTTP_401_UNAUTHORIZED, "Unauthorized.")

    # Body parsing comes *after* authentication: an unauthenticated caller never
    # reaches the parser, let alone the database.
    try:
        update = await request.json()
    except Exception:  # noqa: BLE001 - malformed JSON is data, not a crash
        return {"ok": True, "handled": False}

    chat_id, chat_type, code = _extract_command(update)
    if chat_id is None or code is None:
        # Not a /link command, or nothing we can read. Silently fine.
        return {"ok": True, "handled": False}

    if chat_type != PRIVATE_CHAT:
        # A group's members would all receive one person's private alerts.
        return {"ok": True, "handled": True, "reply": REPLY_NOT_PRIVATE}

    reply = await _consume_code(session, code=code, chat_id=chat_id, now=datetime.now(UTC))
    logger.info("telegram.link_attempt", outcome=_outcome(reply))
    return {"ok": True, "handled": True, "reply": reply}


def _outcome(reply: str) -> str:
    """A loggable label. Never the code, the chat id or the secret."""
    return {
        REPLY_OK: "linked",
        REPLY_BAD_CODE: "rejected_code",
        REPLY_TAKEN: "chat_already_linked",
        REPLY_NOT_PRIVATE: "not_private_chat",
    }.get(reply, "unknown")


def new_link_code() -> str:
    """A fresh code. `token_urlsafe(12)` is 16 characters of 96-bit entropy."""
    return secrets.token_urlsafe(12)


def code_expiry(now: datetime | None = None) -> datetime:
    minutes = get_settings().TELEGRAM_LINK_CODE_TTL_MINUTES
    return (now or datetime.now(UTC)) + timedelta(minutes=minutes)


def pending_placeholder(code: str) -> str:
    """The unique stand-in stored in external_id until a real chat id arrives.

    It has to be unique per pending row, or two users with outstanding codes
    would collide on ux_channel_external before either had linked anything.
    """
    return f"pending:{code}"


__all__ = [
    "CHANNEL",
    "code_expiry",
    "new_link_code",
    "pending_placeholder",
    "router",
]
