"""RSS / Atom feeds: official blogs, regulator notices, gazettes, trade press.

This is the adapter that reads ordinary web resources rather than a documented
JSON API, so it is the one that honours robots.txt and stores only a short
excerpt plus the link - never a full reproduction of a copyrighted article.

Signals: `media_coverage_growth` (items per day per feed group).

Config:
    {"feeds": [{"url": "...", "entity_name": "solid-state battery",
                "entity_type": "technology", "geo_scope": "global",
                "signal_type": "media_coverage_growth"}],
     "days": 90}
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from email.utils import parsedate_to_datetime
from xml.etree import ElementTree

# Feeds are hostile input. defusedxml disables entity expansion and external
# entity resolution outright; if it is unavailable we still refuse any document
# carrying a DOCTYPE and cap the body size, which is what the classic
# entity-expansion attacks need.
try:  # pragma: no cover - trivial import guard
    from defusedxml.ElementTree import fromstring as _safe_fromstring

    _DEFUSED = True
except Exception:  # noqa: BLE001
    _safe_fromstring = ElementTree.fromstring
    _DEFUSED = False

from app.core.errors import PartialFetchError, SourceConfigError, SourceUnavailableError
from app.core.sanitize import strip_html
from app.sources.base import BaseDataSource, RawSignal, SourceHealth
from app.sources.registry import register
from app.sources.series import build_series, daily_counts

_NS = {"atom": "http://www.w3.org/2005/Atom", "dc": "http://purl.org/dc/elements/1.1/"}
EXCERPT_CHARS = 600
MAX_FEED_BYTES = 8 * 1024 * 1024


def _text(node: ElementTree.Element | None) -> str | None:
    if node is None:
        return None
    return (node.text or "").strip() or None


def parse_datetime(value: str | None) -> datetime | None:
    """Accept the several date formats feeds actually use in the wild."""
    if not value:
        return None
    value = value.strip()
    try:
        return parsedate_to_datetime(value)
    except (TypeError, ValueError, IndexError):
        pass
    for candidate in (value, value.replace("Z", "+00:00")):
        try:
            parsed = datetime.fromisoformat(candidate)
            return parsed if parsed.tzinfo else parsed.replace(tzinfo=UTC)
        except ValueError:
            continue
    return None


def parse_feed(xml_text: str) -> list[dict[str, str | datetime | None]]:
    """Parse RSS 2.0 or Atom into a common shape. Raises on non-XML input."""
    if len(xml_text) > MAX_FEED_BYTES:
        raise SourceUnavailableError(
            f"Feed body is {len(xml_text)} bytes, above the {MAX_FEED_BYTES} limit. "
            "Refusing to parse it: an oversized document is how XML parsers are made to "
            "exhaust memory."
        )
    if "<!DOCTYPE" in xml_text[:4096].upper() and not _DEFUSED:
        raise SourceUnavailableError(
            "Feed declares a DOCTYPE and defusedxml is not installed. Refusing to parse: "
            "DTD entity expansion is the classic XML denial-of-service vector. "
            "Install the 'defusedxml' package to accept such feeds safely."
        )
    try:
        root = _safe_fromstring(xml_text)
    except ElementTree.ParseError as exc:
        raise SourceUnavailableError(
            f"Feed body is not valid XML ({exc}). The publisher may have returned an "
            "error page instead of the feed."
        ) from exc

    root_tag = root.tag.split("}")[-1].lower()
    if root_tag not in {"rss", "feed", "rdf"}:
        # An error page is often valid XML/HTML. Returning zero items would look
        # like "the publisher posted nothing", which is a different fact entirely.
        raise SourceUnavailableError(
            f"Response root element is <{root_tag}>, not an RSS or Atom feed. The "
            "publisher probably returned an error page instead of the feed."
        )

    items: list[dict[str, str | datetime | None]] = []
    for item in root.iter():
        tag = item.tag.split("}")[-1]
        if tag not in {"item", "entry"}:
            continue
        title = _text(item.find("title")) or _text(item.find("atom:title", _NS))
        link = _text(item.find("link"))
        if not link:
            link_el = item.find("atom:link", _NS)
            if link_el is not None:
                link = link_el.attrib.get("href")
        guid = _text(item.find("guid")) or _text(item.find("atom:id", _NS)) or link
        published = parse_datetime(
            _text(item.find("pubDate"))
            or _text(item.find("atom:published", _NS))
            or _text(item.find("atom:updated", _NS))
            or _text(item.find("dc:date", _NS))
        )
        summary = (
            _text(item.find("description"))
            or _text(item.find("atom:summary", _NS))
            or _text(item.find("atom:content", _NS))
        )
        items.append(
            {
                "id": guid or link or title,
                "title": title,
                "link": link,
                "published": published,
                "summary": strip_html(summary)[:EXCERPT_CHARS] if summary else None,
            }
        )
    return items


@register("rss")
class RssSource(BaseDataSource):
    """RSS/Atom feeds: media and regulator coverage volume, with linked excerpts."""

    requires_network = True
    respect_robots = True  # ordinary web resource, not a documented API
    default_rate_limit_per_minute = 20
    default_source_class = "aggregator"
    documented_rate_limit = "per publisher; conditional requests used to minimise load"

    async def fetch(self, since: datetime | None = None) -> list[RawSignal]:
        feeds = self.cfg("feeds", required=True)
        if isinstance(feeds, dict):
            feeds = [feeds]
        days = int(self.cfg("days", 90))
        cutoff = datetime.now(UTC) - timedelta(days=days)
        if since is not None:
            cutoff = max(cutoff, since if since.tzinfo else since.replace(tzinfo=UTC))

        out: list[RawSignal] = []
        failures: list[str] = []
        for feed in feeds:
            url = feed.get("url")
            if not url:
                raise SourceConfigError("Every entry in `feeds` needs a `url`.")
            try:
                out.extend(await self._collect_feed(feed, url, cutoff))
            except (SourceUnavailableError, SourceConfigError) as exc:
                failures.append(f"{url}: {exc}")

        if failures and not out:
            raise SourceUnavailableError("; ".join(failures))
        if failures:
            raise PartialFetchError("; ".join(failures), records=out)
        return out

    async def _collect_feed(self, feed: dict, url: str, cutoff: datetime) -> list[RawSignal]:
        body = await self.fetcher.get_text(url, accept="application/rss+xml, application/xml, text/xml")
        if body is None:  # 304 Not Modified: the publisher says nothing changed
            return []

        entity_name = feed.get("entity_name") or url
        entity_type = feed.get("entity_type") or "keyword"
        geo_scope = feed.get("geo_scope") or "global"
        signal_type = feed.get("signal_type") or "media_coverage_growth"

        items = [i for i in parse_feed(body) if i["published"] and i["published"] >= cutoff]
        if not items:
            return []

        records = build_series(
            points=daily_counts([i["published"] for i in items]),  # type: ignore[arg-type]
            entity_name=entity_name,
            entity_type=entity_type,
            signal_type=signal_type,
            source_prefix="rss",
            unit="items_per_day",
            geo_scope=geo_scope,
            confidence=0.6,
            url=url,
            payload_extra={"feed": url},
        )

        for item in items[:50]:
            records.append(
                RawSignal(
                    external_id=f"rss|{url}|{item['id']}",
                    title=item["title"],  # type: ignore[arg-type]
                    content=item["summary"],  # type: ignore[arg-type]
                    url=item["link"],  # type: ignore[arg-type]
                    published_at=item["published"],  # type: ignore[arg-type]
                    entity_name=entity_name,
                    entity_type=entity_type,
                    geo_scope=geo_scope,
                    confidence=0.6,
                    payload={"feed": url, "excerpt_chars": EXCERPT_CHARS},
                )
            )
        return records

    async def health_check(self) -> SourceHealth:
        feeds = self.config.get("feeds") or []
        if not feeds:
            return SourceHealth(healthy=False, detail="No feeds configured.")
        url = feeds[0]["url"]
        body = await self.fetcher.get_text(url, accept="application/rss+xml")
        if body is None:
            return SourceHealth(healthy=True, detail=f"{url} reports no change since last fetch.")
        count = len(parse_feed(body))
        return SourceHealth(healthy=count > 0, detail=f"{url} returned {count} items.")
