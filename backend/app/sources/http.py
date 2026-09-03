"""Polite, guarded HTTP for source adapters.

Design note: adapters never touch `httpx` directly. They receive a `Fetcher`,
which owns the policy (robots.txt, rate limits, SSRF, conditional requests,
timeouts) and delegates the actual bytes to a `Transport`. In tests the transport
is a `RecordedTransport` reading fixtures from disk, which is why no test in this
repository can open a socket.
"""

from __future__ import annotations

import hashlib
import json
import time
import urllib.robotparser
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Protocol, runtime_checkable
from urllib.parse import urlencode, urljoin, urlparse

from app.core.config import settings
from app.core.errors import (
    RateLimitedError,
    RobotsDisallowedError,
    SourceUnavailableError,
)
from app.core.logging import get_logger
from app.core.ratelimit import get_limiter
from app.core.ssrf import assert_url_allowed

log = get_logger("http.fetch")


@dataclass(slots=True)
class HttpResponse:
    url: str
    status_code: int
    headers: dict[str, str]
    text: str
    elapsed_ms: int = 0
    from_cache: bool = False

    @property
    def ok(self) -> bool:
        return 200 <= self.status_code < 300

    @property
    def not_modified(self) -> bool:
        return self.status_code == 304

    def json(self) -> Any:
        try:
            return json.loads(self.text)
        except json.JSONDecodeError as exc:
            snippet = self.text[:200].replace("\n", " ")
            raise SourceUnavailableError(
                f"{self.url} did not return valid JSON (status {self.status_code}). "
                f"First 200 characters: {snippet!r}"
            ) from exc

    def header(self, name: str) -> str | None:
        lowered = {k.lower(): v for k, v in self.headers.items()}
        return lowered.get(name.lower())


@runtime_checkable
class Transport(Protocol):
    async def request(
        self, method: str, url: str, *, headers: dict[str, str], timeout: float
    ) -> HttpResponse: ...


class HttpxTransport:
    """The real transport. The only place an outbound socket is opened."""

    async def request(
        self, method: str, url: str, *, headers: dict[str, str], timeout: float
    ) -> HttpResponse:
        import httpx  # imported here so tests that never touch the network need not load it

        started = time.monotonic()
        try:
            async with httpx.AsyncClient(timeout=timeout, follow_redirects=False) as client:
                response = await client.request(method, url, headers=headers)
        except Exception as exc:  # noqa: BLE001
            # httpx raises HTTPError, but the anyio stack underneath can surface an
            # ExceptionGroup for DNS and socket failures. Callers should never have
            # to unwrap that, so everything becomes one documented error type.
            raise SourceUnavailableError(
                f"{method} {url} failed at the transport layer: {type(exc).__name__}: {exc}"
            ) from exc
        return HttpResponse(
            url=str(response.url),
            status_code=response.status_code,
            headers=dict(response.headers),
            text=response.text,
            elapsed_ms=int((time.monotonic() - started) * 1000),
        )


class RecordedTransport:
    """Replays responses recorded on disk. Used by every adapter test.

    A fixture file is a JSON object mapping a URL (or a URL prefix ending in `*`)
    to `{"status": int, "headers": {...}, "body": "..."}`. A request with no match
    raises, so a test can never silently pass because it hit the network instead.
    """

    def __init__(self, fixtures: dict[str, dict[str, Any]] | None = None) -> None:
        self.fixtures: dict[str, dict[str, Any]] = fixtures or {}
        self.calls: list[str] = []

    @classmethod
    def from_file(cls, path: str | Path) -> RecordedTransport:
        data = json.loads(Path(path).read_text(encoding="utf-8"))
        return cls(data)

    def add(self, url: str, body: str, status: int = 200, headers: dict[str, str] | None = None) -> None:
        self.fixtures[url] = {"status": status, "headers": headers or {}, "body": body}

    def _match(self, url: str) -> dict[str, Any] | None:
        if url in self.fixtures:
            return self.fixtures[url]
        for key, value in self.fixtures.items():
            if key.endswith("*") and url.startswith(key[:-1]):
                return value
        return None

    async def request(
        self, method: str, url: str, *, headers: dict[str, str], timeout: float
    ) -> HttpResponse:
        self.calls.append(url)
        entry = self._match(url)
        if entry is None:
            raise AssertionError(
                f"No recorded fixture for {method} {url}. Tests must not reach the network: "
                f"add the response to the fixture file. Known keys: {sorted(self.fixtures)[:10]}"
            )
        # Honour conditional requests so cache behaviour is testable offline.
        etag = entry.get("headers", {}).get("ETag")
        if etag and headers.get("If-None-Match") == etag:
            return HttpResponse(url=url, status_code=304, headers=entry.get("headers", {}), text="")
        return HttpResponse(
            url=url,
            status_code=int(entry.get("status", 200)),
            headers=dict(entry.get("headers", {})),
            text=str(entry.get("body", "")),
        )


@dataclass(slots=True)
class ConditionalEntry:
    etag: str | None = None
    last_modified: str | None = None


@dataclass(slots=True)
class FetchStats:
    requests: int = 0
    not_modified: int = 0
    bytes_read: int = 0
    robots_blocked: int = 0
    touched_urls: dict[str, ConditionalEntry] = field(default_factory=dict)


def url_hash(url: str) -> str:
    return hashlib.sha256(url.encode()).hexdigest()


class Fetcher:
    """Policy layer around a transport.

    Every adapter gets one of these, scoped to a single source, so rate limits and
    quotas are attributed correctly and one noisy source cannot starve another.
    """

    def __init__(
        self,
        *,
        source_slug: str,
        transport: Transport | None = None,
        rate_limit_per_minute: int = 30,
        respect_robots: bool = True,
        user_agent: str | None = None,
        timeout: float | None = None,
        conditional: dict[str, ConditionalEntry] | None = None,
        max_requests: int = 500,
    ) -> None:
        self.source_slug = source_slug
        self.transport: Transport = transport or HttpxTransport()
        self.rate_limit_per_minute = rate_limit_per_minute
        self.respect_robots = respect_robots
        self.user_agent = user_agent or settings.USER_AGENT
        self.timeout = timeout if timeout is not None else settings.HTTP_TIMEOUT_SECONDS
        self.conditional: dict[str, ConditionalEntry] = conditional if conditional is not None else {}
        self.max_requests = max_requests
        self.stats = FetchStats()
        self._robots: dict[str, tuple[float, urllib.robotparser.RobotFileParser]] = {}

    # -- robots ----------------------------------------------------------
    async def _robots_allows(self, url: str) -> bool:
        parsed = urlparse(url)
        root = f"{parsed.scheme}://{parsed.netloc}"
        cached = self._robots.get(root)
        now = time.time()
        if cached and now - cached[0] < settings.ROBOTS_CACHE_TTL_SECONDS:
            parser = cached[1]
        else:
            parser = urllib.robotparser.RobotFileParser()
            parser.set_url(urljoin(root, "/robots.txt"))
            try:
                response = await self.transport.request(
                    "GET",
                    urljoin(root, "/robots.txt"),
                    headers={"User-Agent": self.user_agent},
                    timeout=min(self.timeout, 10.0),
                )
                # 4xx means "no robots.txt", which by convention allows everything.
                parser.parse(response.text.splitlines() if response.status_code == 200 else [])
            except (SourceUnavailableError, AssertionError):
                # Unreachable or unrecorded robots.txt: treat as absent, which is
                # the documented convention, and note it in the log.
                parser.parse([])
            self._robots[root] = (now, parser)
        return parser.can_fetch(self.user_agent, url)

    # -- core ------------------------------------------------------------
    async def get(
        self,
        url: str,
        *,
        params: dict[str, Any] | None = None,
        headers: dict[str, str] | None = None,
        conditional: bool = True,
        accept: str = "application/json",
        allow_status: set[int] | None = None,
    ) -> HttpResponse:
        if params:
            clean = {k: v for k, v in params.items() if v is not None}
            url = f"{url}{'&' if urlparse(url).query else '?'}{urlencode(clean)}"

        assert_url_allowed(url, resolve=isinstance(self.transport, HttpxTransport))

        if self.stats.requests >= self.max_requests:
            raise RateLimitedError(
                f"Source {self.source_slug!r} hit its per-run request ceiling of "
                f"{self.max_requests}. Narrow the source config or raise max_requests."
            )

        if self.respect_robots and not await self._robots_allows(url):
            self.stats.robots_blocked += 1
            raise RobotsDisallowedError(
                f"robots.txt at {urlparse(url).netloc} disallows {url} for our user agent. "
                "This path will not be collected automatically."
            )

        limiter = get_limiter()
        if not await limiter.allow(f"rl:src:{self.source_slug}", self.rate_limit_per_minute):
            retry_after = await limiter.retry_after(f"rl:src:{self.source_slug}")
            raise RateLimitedError(
                f"Local rate limit for source {self.source_slug!r} "
                f"({self.rate_limit_per_minute}/min) reached; the run will resume later.",
                retry_after=retry_after,
            )

        request_headers: dict[str, str] = {
            "User-Agent": self.user_agent,
            "Accept": accept,
            "Accept-Encoding": "gzip, deflate",
            "From": settings.CONTACT_EMAIL,
            **(headers or {}),
        }
        key = url_hash(url)
        if conditional and key in self.conditional:
            entry = self.conditional[key]
            if entry.etag:
                request_headers["If-None-Match"] = entry.etag
            if entry.last_modified:
                request_headers["If-Modified-Since"] = entry.last_modified

        response = await self.transport.request("GET", url, headers=request_headers, timeout=self.timeout)
        self.stats.requests += 1
        self.stats.bytes_read += len(response.text)

        if response.status_code == 429:
            hint = response.header("Retry-After")
            raise RateLimitedError(
                f"{urlparse(url).netloc} returned 429 for {self.source_slug!r}. "
                f"Retry-After={hint or 'unset'}.",
                retry_after=float(hint) if hint and hint.isdigit() else None,
            )
        if response.status_code in (401, 403):
            raise SourceUnavailableError(
                f"{urlparse(url).netloc} refused the request with {response.status_code}. "
                "Check the source credential, or whether this endpoint needs one."
            )
        if response.status_code >= 500:
            raise SourceUnavailableError(
                f"{urlparse(url).netloc} returned {response.status_code}. This is an upstream "
                "outage; the run will be marked failed and retried on the next schedule."
            )
        # A 4xx on a resource the operator explicitly configured is a configuration
        # problem, and must surface. Callers pass allow_status={404} for endpoints
        # where "absent" is a legitimate answer (an issuer that reports no revenue
        # under the requested XBRL concept, for example).
        if response.status_code >= 400 and response.status_code not in (allow_status or set()):
            raise SourceUnavailableError(
                f"{urlparse(url).netloc} returned {response.status_code} for {url}. "
                "Check the configured identifier - it may be wrong, renamed or removed."
            )

        if response.not_modified:
            self.stats.not_modified += 1
        else:
            etag = response.header("ETag")
            last_modified = response.header("Last-Modified")
            if etag or last_modified:
                entry = ConditionalEntry(etag=etag, last_modified=last_modified)
                self.conditional[key] = entry
                self.stats.touched_urls[url] = entry

        return response

    async def get_json(self, url: str, **kwargs: Any) -> Any:
        response = await self.get(url, accept="application/json", **kwargs)
        if response.not_modified:
            return None
        if response.status_code >= 400:  # only reachable via allow_status
            return None
        return response.json()

    async def get_text(self, url: str, **kwargs: Any) -> str | None:
        kwargs.setdefault("accept", "*/*")
        response = await self.get(url, **kwargs)
        return None if response.not_modified else response.text
