"""The three providers this build ships with.

All three are behind `NotificationProvider`, and the alert engine knows about
none of them individually. Removing Telegram means deleting one class and one
registration line.
"""

from __future__ import annotations

import smtplib
from email.message import EmailMessage
from typing import Any

import httpx

from app.core.config import get_settings
from app.core.errors import failure_code, is_unrecoverable
from app.core.logging import get_logger
from app.notifications.base import (
    NotificationMessage,
    NotificationProvider,
    ProviderResult,
    register_provider,
)

logger = get_logger(__name__)


def _link(message: NotificationMessage) -> str:
    base = get_settings().APP_BASE_URL.rstrip("/")
    return f"{base}{message.link}" if message.link else base


class InAppProvider(NotificationProvider):
    """The always-available channel.

    In-app delivery is the stored `AlertDelivery` row itself: the user reads it
    when they next open the product. It is always "configured" because it needs
    nothing external, which is why every user has at least one working channel.
    """

    channel = "in_app"

    @property
    def configured(self) -> bool:
        return True

    async def send(self, *, address: str, message: NotificationMessage) -> ProviderResult:
        return ProviderResult(
            delivered=True,
            detail="Stored for the user to read in the application.",
            live=True,
        )


class EmailProvider(NotificationProvider):
    """SMTP, if and only if SMTP is configured."""

    channel = "email"

    @property
    def configured(self) -> bool:
        settings = get_settings()
        return bool(settings.SMTP_HOST and settings.SMTP_FROM)

    async def send(self, *, address: str, message: NotificationMessage) -> ProviderResult:
        settings = get_settings()
        if not self.configured:
            return ProviderResult(
                delivered=False,
                detail=(
                    "No SMTP server is configured, so nothing was emailed. The alert is "
                    "still readable in the application."
                ),
            )
        mail = EmailMessage()
        mail["Subject"] = message.title
        mail["From"] = settings.SMTP_FROM
        mail["To"] = address
        mail.set_content(f"{message.body}\n\n{_link(message)}\n")
        try:
            with smtplib.SMTP(settings.SMTP_HOST, settings.SMTP_PORT, timeout=15) as server:
                if settings.SMTP_STARTTLS:
                    server.starttls()
                if settings.SMTP_USERNAME:
                    server.login(settings.SMTP_USERNAME, settings.SMTP_PASSWORD)
                server.send_message(mail)
        except Exception as exc:  # noqa: BLE001 - a failed send is data, not a crash
            if is_unrecoverable(exc):
                raise
            # The class name only. An smtplib exception carries the server's
            # reply and the recipient address, and `detail` is not just a log
            # line: it is persisted to `alert_deliveries.suppressed_reason`.
            logger.warning(
                "notification.email_failed", outcome="delivery_failed", error_type=failure_code(exc)
            )
            return ProviderResult(
                delivered=False,
                detail=(
                    f"SMTP delivery failed ({failure_code(exc)}). The alert is still readable in the "
                    "application."
                ),
            )
        return ProviderResult(delivered=True, detail=f"Emailed to {address}.", live=True)


class TelegramProvider(NotificationProvider):
    """One channel among several. Nothing in the system depends on it existing.

    `address` is the chat id from a **verified** `NotificationChannelLink`. The
    alert engine never passes an unverified one, which is what stops a stranger
    who knows a chat id from receiving another person's opportunities.
    """

    channel = "telegram"

    @property
    def configured(self) -> bool:
        return bool(get_settings().TELEGRAM_BOT_TOKEN)

    async def send(self, *, address: str, message: NotificationMessage) -> ProviderResult:
        settings = get_settings()
        if not self.configured:
            return ProviderResult(
                delivered=False,
                detail=(
                    "No Telegram bot token is configured, so nothing was sent. The alert "
                    "is still readable in the application."
                ),
            )
        url = f"{settings.TELEGRAM_API_BASE.rstrip('/')}/bot{settings.TELEGRAM_BOT_TOKEN}/sendMessage"
        payload: dict[str, Any] = {
            "chat_id": address,
            "text": f"{message.title}\n\n{message.body}\n\n{_link(message)}",
            "disable_web_page_preview": True,
        }
        try:
            async with httpx.AsyncClient(timeout=15) as client:
                response = await client.post(url, json=payload)
            if response.status_code >= 400:
                return ProviderResult(
                    delivered=False,
                    detail=f"Telegram refused the message: HTTP {response.status_code}.",
                )
        except Exception as exc:  # noqa: BLE001 - network failure is data, not a crash
            if is_unrecoverable(exc):
                raise
            # The class name only, and never the URL: the send URL contains the
            # bot token, and httpx puts the URL in the exception text. `detail`
            # is persisted to `alert_deliveries.suppressed_reason`, so leaking it
            # there would write a credential into a row users can read.
            logger.warning(
                "notification.telegram_failed", outcome="delivery_failed", error_type=failure_code(exc)
            )
            return ProviderResult(
                delivered=False,
                detail=(
                    f"Telegram delivery failed ({failure_code(exc)}). The alert is still readable in "
                    "the application."
                ),
            )
        return ProviderResult(delivered=True, detail="Sent to Telegram.", live=True)


def register_default_providers() -> None:
    register_provider(InAppProvider())
    register_provider(EmailProvider())
    register_provider(TelegramProvider())


register_default_providers()
