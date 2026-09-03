"""The HTTP policy layer is where 'polite' is either real or a claim."""

import pytest

from app.core.errors import (
    RateLimitedError,
    RobotsDisallowedError,
    SourceUnavailableError,
    SSRFBlockedError,
)
from app.sources.http import ConditionalEntry, Fetcher, RecordedTransport, url_hash


def transport_with(**pages) -> RecordedTransport:
    t = RecordedTransport()
    for url, spec in pages.items():
        t.add(url, spec.get("body", ""), spec.get("status", 200), spec.get("headers"))
    return t


async def test_sends_identifying_headers(make_fetcher):
    transport = transport_with(**{"https://api.example.com/x": {"body": "{}"}})
    captured: dict = {}

    original = transport.request

    async def spy(method, url, *, headers, timeout):
        captured.update(headers)
        return await original(method, url, headers=headers, timeout=timeout)

    transport.request = spy  # type: ignore[method-assign]
    fetcher = make_fetcher(transport)
    await fetcher.get("https://api.example.com/x")

    assert "OpportunityIntelligenceSystem" in captured["User-Agent"]
    assert "From" in captured, "a contact address must be sent; SEC and others require it"


async def test_query_params_are_encoded_once(make_fetcher):
    transport = transport_with(**{"https://api.example.com/s?q=heat+pump&n=5": {"body": "{}"}})
    fetcher = make_fetcher(transport)
    await fetcher.get_json("https://api.example.com/s", params={"q": "heat pump", "n": 5})
    assert transport.calls == ["https://api.example.com/s?q=heat+pump&n=5"]


async def test_none_params_are_dropped(make_fetcher):
    transport = transport_with(**{"https://api.example.com/s?a=1": {"body": "{}"}})
    fetcher = make_fetcher(transport)
    await fetcher.get_json("https://api.example.com/s", params={"a": 1, "b": None})
    assert transport.calls == ["https://api.example.com/s?a=1"]


async def test_conditional_request_uses_etag_and_reports_304(make_fetcher):
    url = "https://api.example.com/feed"
    transport = transport_with(**{url: {"body": '{"v":1}', "headers": {"ETag": '"abc"'}}})
    fetcher = make_fetcher(transport)

    first = await fetcher.get(url)
    assert first.status_code == 200
    assert url_hash(url) in fetcher.conditional

    second = await fetcher.get(url)
    assert second.not_modified
    assert fetcher.stats.not_modified == 1


async def test_conditional_store_can_be_preloaded(make_fetcher):
    url = "https://api.example.com/feed"
    transport = transport_with(**{url: {"body": "x", "headers": {"ETag": '"abc"'}}})
    fetcher = make_fetcher(transport, conditional={url_hash(url): ConditionalEntry(etag='"abc"')})
    response = await fetcher.get(url)
    assert response.not_modified, "a stored ETag must be sent on the first request of a new run"


async def test_robots_disallow_blocks_the_fetch(make_fetcher, recorded):
    transport = recorded("rss")
    fetcher = make_fetcher(transport, respect_robots=True)
    with pytest.raises(RobotsDisallowedError) as exc:
        await fetcher.get_text("https://blocked.example.com/feed.xml")
    assert "robots.txt" in str(exc.value)
    assert fetcher.stats.robots_blocked == 1


async def test_robots_allow_permits_the_fetch(make_fetcher, recorded):
    fetcher = make_fetcher(recorded("rss"), respect_robots=True)
    body = await fetcher.get_text("https://regulator.example.gov/feed.xml")
    assert body and "<rss" in body


async def test_missing_robots_is_treated_as_allowed(make_fetcher, recorded):
    fetcher = make_fetcher(recorded("rss"), respect_robots=True)
    body = await fetcher.get_text("https://broken.example.com/feed.xml")
    assert body is not None


async def test_rate_limit_is_enforced_per_source(make_fetcher):
    transport = transport_with(**{"https://api.example.com/x": {"body": "{}"}})
    fetcher = make_fetcher(transport, rate_limit=2, slug="tight")
    await fetcher.get("https://api.example.com/x?a=1".replace("?a=1", ""))
    await fetcher.get("https://api.example.com/x")
    with pytest.raises(RateLimitedError):
        await fetcher.get("https://api.example.com/x")


async def test_per_run_request_ceiling(make_fetcher):
    transport = transport_with(**{"https://api.example.com/x": {"body": "{}"}})
    fetcher = make_fetcher(transport, max_requests=1)
    await fetcher.get("https://api.example.com/x")
    with pytest.raises(RateLimitedError) as exc:
        await fetcher.get("https://api.example.com/x")
    assert "request ceiling" in str(exc.value)


async def test_429_surfaces_retry_after(make_fetcher):
    transport = transport_with(
        **{"https://api.example.com/x": {"status": 429, "headers": {"Retry-After": "120"}}}
    )
    fetcher = make_fetcher(transport)
    with pytest.raises(RateLimitedError) as exc:
        await fetcher.get("https://api.example.com/x")
    assert exc.value.retry_after == 120


async def test_ssrf_guard_applies_to_adapters(make_fetcher):
    fetcher = make_fetcher(transport_with())
    with pytest.raises(SSRFBlockedError):
        await fetcher.get("http://169.254.169.254/latest/meta-data/")


async def test_unrecorded_url_fails_loudly(make_fetcher):
    fetcher = make_fetcher(transport_with())
    with pytest.raises(AssertionError) as exc:
        await fetcher.get("https://api.example.com/never-recorded")
    assert "No recorded fixture" in str(exc.value)


async def test_bad_json_error_names_the_url(make_fetcher):
    transport = transport_with(**{"https://api.example.com/x": {"body": "<html>oops</html>"}})
    fetcher = make_fetcher(transport)
    with pytest.raises(SourceUnavailableError) as exc:
        await fetcher.get_json("https://api.example.com/x")
    assert "api.example.com" in str(exc.value)


async def test_real_transport_is_not_used_in_tests():
    fetcher = Fetcher(source_slug="x")
    assert type(fetcher.transport).__name__ == "HttpxTransport"
    # Proof the socket guard is armed: the real transport cannot connect here.
    with pytest.raises(SourceUnavailableError):
        await fetcher.get("https://example.com/")
