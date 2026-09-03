"""Wikipedia pageviews as an attention proxy.

This exists because there is no free official Google Trends API. Pageviews are a
*proxy*: they measure encyclopaedia reading, not purchase intent or search
demand. Every observation is marked `is_proxy=True`, and the scoring layer will
not let a proxy alone satisfy the attention requirement.

Config: {"articles": [{"title": "Solid-state_battery", "entity_name": "solid-state battery",
                       "entity_type": "technology", "project": "en.wikipedia"}],
         "days": 120}
No credentials required.
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta

from app.core.errors import PartialFetchError, SourceConfigError, SourceUnavailableError
from app.sources.base import BaseDataSource, RawSignal, SourceHealth
from app.sources.registry import register
from app.sources.series import build_series

API = "https://wikimedia.org/api/rest_v1/metrics/pageviews/per-article"


@register("wikipedia_pageviews")
class WikipediaPageviewsSource(BaseDataSource):
    """Wikimedia pageviews API: daily article reads, used as an attention proxy."""

    requires_network = True
    respect_robots = False  # documented REST API
    default_rate_limit_per_minute = 100
    default_source_class = "official"
    documented_rate_limit = "100 requests/second (Wikimedia REST API); we use far less"

    async def fetch(self, since: datetime | None = None) -> list[RawSignal]:
        articles = self.cfg("articles", required=True)
        if isinstance(articles, dict):
            articles = [articles]
        days = int(self.cfg("days", 120))

        end = datetime.now(UTC).replace(hour=0, minute=0, second=0, microsecond=0) - timedelta(days=1)
        start = end - timedelta(days=days)
        if since is not None:
            start = max(start, (since if since.tzinfo else since.replace(tzinfo=UTC)))
        if start >= end:
            return []

        out: list[RawSignal] = []
        failures: list[str] = []
        for article in articles:
            try:
                out.extend(await self._collect_article(article, start, end))
            except SourceUnavailableError as exc:
                failures.append(f"{article.get('title')}: {exc}")

        if failures and not out:
            raise SourceUnavailableError("; ".join(failures))
        if failures:
            raise PartialFetchError("; ".join(failures), records=out)
        return out

    async def _collect_article(self, article: dict, start: datetime, end: datetime) -> list[RawSignal]:
        title = article.get("title")
        if not title:
            raise SourceConfigError("Every entry in `articles` needs a `title`.")
        project = article.get("project", "en.wikipedia")
        entity_name = article.get("entity_name") or title.replace("_", " ")
        entity_type = article.get("entity_type") or "keyword"

        url = (
            f"{API}/{project}/all-access/user/{title}/daily/"
            f"{start.strftime('%Y%m%d')}/{end.strftime('%Y%m%d')}"
        )
        data = await self.fetcher.get_json(url)
        if data is None:
            return []
        items = data.get("items", [])
        points = [
            (datetime.strptime(i["timestamp"][:8], "%Y%m%d").replace(tzinfo=UTC), float(i["views"]))
            for i in items
            if i.get("timestamp") and i.get("views") is not None
        ]
        if not points:
            return []
        return build_series(
            points=points,
            entity_name=entity_name,
            entity_type=entity_type,
            signal_type="wiki_pageview_growth",
            source_prefix="wiki",
            unit="views_per_day",
            confidence=0.7,
            url=f"https://{project}.org/wiki/{title}",
            payload_extra={"article": title, "project": project, "proxy_for": "search interest"},
            is_proxy=True,
        )

    async def health_check(self) -> SourceHealth:
        end = datetime.now(UTC) - timedelta(days=2)
        start = end - timedelta(days=2)
        url = (
            f"{API}/en.wikipedia/all-access/user/Wikipedia/daily/"
            f"{start.strftime('%Y%m%d')}/{end.strftime('%Y%m%d')}"
        )
        data = await self.fetcher.get_json(url)
        if data is None:
            return SourceHealth(healthy=True, detail="No change since last check.")
        return SourceHealth(
            healthy=bool(data.get("items")),
            detail=f"Wikimedia pageviews API returned {len(data.get('items', []))} probe rows.",
        )
