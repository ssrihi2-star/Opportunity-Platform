"""Outbound request guard. Blocks private ranges, cloud metadata and odd schemes."""

from __future__ import annotations

import ipaddress
import socket
from urllib.parse import urlparse

from app.core.errors import SSRFBlockedError

ALLOWED_SCHEMES = {"http", "https"}
ALLOWED_PORTS = {80, 443, 8080, 8443}
BLOCKED_HOSTNAMES = {"localhost", "metadata.google.internal", "metadata"}


def _is_blocked_ip(ip: str) -> bool:
    addr = ipaddress.ip_address(ip)
    return (
        addr.is_private
        or addr.is_loopback
        or addr.is_link_local
        or addr.is_reserved
        or addr.is_multicast
        or addr.is_unspecified
    )


def assert_url_allowed(url: str, *, resolve: bool = True) -> None:
    """Raise SSRFBlockedError if the URL must not be fetched."""
    parsed = urlparse(url)
    if parsed.scheme not in ALLOWED_SCHEMES:
        raise SSRFBlockedError(f"Scheme {parsed.scheme!r} is not allowed. Use http or https.")
    host = (parsed.hostname or "").lower()
    if not host:
        raise SSRFBlockedError("The URL has no host component.")
    if host in BLOCKED_HOSTNAMES or host.endswith(".localhost") or host.endswith(".internal"):
        raise SSRFBlockedError(f"Host {host!r} is on the internal denylist.")

    port = parsed.port or (443 if parsed.scheme == "https" else 80)
    if port not in ALLOWED_PORTS:
        raise SSRFBlockedError(f"Port {port} is not allowed for outbound requests.")

    try:
        ipaddress.ip_address(host)
    except ValueError:
        pass
    else:
        if _is_blocked_ip(host):
            raise SSRFBlockedError(f"Address {host} is in a private or reserved range.")
        return

    if not resolve:
        return
    try:
        infos = socket.getaddrinfo(host, port, proto=socket.IPPROTO_TCP)
    except socket.gaierror as exc:
        raise SSRFBlockedError(f"Host {host!r} could not be resolved.") from exc
    for info in infos:
        ip = info[4][0]
        if _is_blocked_ip(ip):
            raise SSRFBlockedError(
                f"Host {host!r} resolves to {ip}, which is in a private or reserved range."
            )
