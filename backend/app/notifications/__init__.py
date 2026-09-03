"""Notification delivery, deliberately not coupled to any one channel."""

from app.notifications.base import (
    NotificationMessage,
    NotificationProvider,
    ProviderResult,
    get_provider,
    register_provider,
    registered_channels,
)

__all__ = [
    "NotificationMessage",
    "NotificationProvider",
    "ProviderResult",
    "get_provider",
    "register_provider",
    "registered_channels",
]
