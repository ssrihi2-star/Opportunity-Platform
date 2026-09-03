"""The notification interface every channel implements.

Section 20 of the brief: *do not couple the system to Telegram.* Telegram is one
provider behind this interface and can be removed without touching a line of the
alert engine. Adding WhatsApp, Slack or a webhook means writing one class and
registering it.

Nothing here decides *whether* to send. That judgement — deduplication, cooldown,
whether this user asked for this — lives in `app.services.alerts`, so a provider
cannot accidentally bypass it.
"""

from __future__ import annotations

from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from typing import Any


@dataclass(slots=True)
class NotificationMessage:
    """What to say, in a form every channel can render.

    `body` is plain text on purpose. A channel that supports rich formatting may
    build it from `detail`, but no channel may *require* it, so a message is
    never undeliverable because one provider lacks a feature.
    """

    title: str
    body: str
    #: Where the user goes to see the whole picture. Relative to the app root.
    link: str | None = None
    detail: dict[str, Any] = field(default_factory=dict)


@dataclass(slots=True)
class ProviderResult:
    delivered: bool
    detail: str
    #: True when the provider is configured and could really reach the user.
    #: False means the message was recorded but nothing left this machine, and
    #: the product must say so rather than implying it was sent.
    live: bool = False


class NotificationProvider(ABC):
    """One way of reaching a person."""

    #: Stable channel key, e.g. "in_app", "email", "telegram".
    channel: str = "unknown"

    @property
    @abstractmethod
    def configured(self) -> bool:
        """True when this provider has everything it needs to actually deliver.

        A provider that is not configured must say so rather than pretending;
        the alert engine records the delivery as suppressed with the reason, so
        nobody is told a message was sent when it was not.
        """

    @abstractmethod
    async def send(self, *, address: str, message: NotificationMessage) -> ProviderResult:
        """Deliver one message to one address on this channel."""


_REGISTRY: dict[str, NotificationProvider] = {}


def register_provider(provider: NotificationProvider) -> None:
    _REGISTRY[provider.channel] = provider


def get_provider(channel: str) -> NotificationProvider | None:
    return _REGISTRY.get(channel)


def registered_channels() -> list[str]:
    return sorted(_REGISTRY)
