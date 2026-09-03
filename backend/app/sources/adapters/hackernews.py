"""Hacker News discussion volume, via the Algolia HN Search API.

Why Algolia and not the Firebase API: the Firebase endpoint would need one HTTP
request per story to answer "how much is X being discussed", which is hundreds of
requests per keyword. The Algolia search API answers it in one request and is the
documented public search interface for the same corpus.

Signals: `social_discussion_growth` (stories per day matching the keyword) and
`media_coverage_growth` is deliberately NOT emitted here - forum chatter is not
media coverage, and conflating them would let one source satisfy two signal types
and quietly weaken the multi-source gate.

Config: {"keywords": ["local-first sync", ...], "days": 90, "hits_per_keyword": 200}
No credentials required.
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta

from app.core.errors import PartialFetchError, SourceUnavailableError
from app.sources.base import BaseDataSource, RawSignal, SourceHealth
from app.sources.registry import register
from app.sources.series import build_series, daily_counts

API = "https://hn.algolia.com/api/v1/search_by_date"


@register("hackernews")
class HackerNewsSource(BaseDataSource):
    """Hacker News story volume per keyword (Algolia public search API)."""

    requires_network = True
    respect_robots = False  # documented JSON API
    default_rate_limit_per_minute = 30
    default_source_class = "forum_social"
    documented_rate_limit = "10,000 requests/hour per IP (Algolia HN API)"

    async def fetch(self, since: datetime | None = None) -> list[RawSignal]:
        keywords = self.cfg("keywords", required=True)
        if isinstance(keywords, str):
            keywords = [keywords]
        days = int(self.cfg("days", 90))
        hits = int(self.cfg("hits_per_keyword", 200))

        now = datetime.now(UTC)
        window_start = now - timedelta(days=days)
        if since is not None:
            window_start = max(window_start, since.replace(tzinfo=since.tzinfo or UTC))

        out: list[RawSignal] = []
        failures: list[str] = []
        for keyword in keywords:
            try:
                out.extend(await self._collect_keyword(keyword, window_start, hits))
            except SourceUnavailableError as exc:
                failures.append(f"{keyword}: {exc}")

        if failures and not out:
            raise SourceUnavailableError("; ".join(failures))
        if failures:
            raise PartialFetchError("; ".join(failures), records=out)
        return out

    async def _collect_keyword(self, keyword: str, window_start: datetime, hits: int) -> list[RawSignal]:
        data = await self.fetcher.get_json(
            API,
            params={
                "query": keyword,
                "tags": "story",
                "numericFilters": f"created_at_i>{int(window_start.timestamp())}",
                "hitsPerPage": min(hits, 1000),
            },
        )
        if data is None:
            return []
        stories = data.get("hits", [])
        timestamps = [
            datetime.fromtimestamp(int(h["created_at_i"]), tz=UTC) for h in stories if h.get("created_at_i")
        ]
        if not timestamps:
            return []

        records = build_series(
            points=daily_counts(timestamps),
            entity_name=keyword,
            entity_type="keyword",
            signal_type="social_discussion_growth",
            source_prefix="hn",
            unit="stories_per_day",
            confidence=0.55,
            url=f"https://hn.algolia.com/?query={keyword}",
            payload_extra={"keyword": keyword, "total_hits": data.get("nbHits")},
            is_proxy=True,
        )

        # Store the top stories themselves as evidence-bearing raw records. They
        # carry no metric, so they never become observations - they exist so a
        # future report can quote a real link instead of a summary of a summary.
        for story in sorted(stories, key=lambda h: h.get("points") or 0, reverse=True)[:10]:
            created = story.get("created_at_i")
            records.append(
                RawSignal(
                    external_id=f"hn-story|{story.get('objectID')}",
                    title=story.get("title"),
                    content=(story.get("story_text") or "")[:2000] or None,
                    url=story.get("url") or f"https://news.ycombinator.com/item?id={story.get('objectID')}",
                    published_at=datetime.fromtimestamp(int(created), tz=UTC) if created else None,
                    entity_name=keyword,
                    entity_type="keyword",
                    confidence=0.5,
                    payload={
                        "points": story.get("points"),
                        "num_comments": story.get("num_comments"),
                        "author": story.get("author"),
                        "keyword": keyword,
                    },
                )
            )
        return records

    async def health_check(self) -> SourceHealth:
        data = await self.fetcher.get_json(API, params={"query": "test", "hitsPerPage": 1})
        if data is None:
            return SourceHealth(healthy=True, detail="No change since last check.")
        return SourceHealth(
            healthy="hits" in data,
            detail=f"Algolia HN search reachable; {data.get('nbHits', 0)} hits for the probe query.",
        )
