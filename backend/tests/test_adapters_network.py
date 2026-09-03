"""Every network adapter, driven entirely by recorded fixtures."""

from datetime import UTC, datetime

import pytest

from app.core.errors import PartialFetchError, SourceConfigError, SourceUnavailableError
from app.sources.adapters.comtrade import UnComtradeSource
from app.sources.adapters.fred import FredSource
from app.sources.adapters.github import GitHubSource
from app.sources.adapters.hackernews import HackerNewsSource
from app.sources.adapters.rss import RssSource, parse_datetime, parse_feed
from app.sources.adapters.sec_edgar import SecEdgarSource, normalise_cik
from app.sources.adapters.wikipedia import WikipediaPageviewsSource


def types_of(records) -> set[str]:
    return {r.signal_type for r in records if r.signal_type}


def series_of(records, signal_type):
    rows = [r for r in records if r.signal_type == signal_type]
    return sorted(rows, key=lambda r: r.published_at)


# ------------------------------------------------------------------- github
async def test_github_emits_levels_and_commit_history(recorded, make_fetcher):
    source = GitHubSource(
        config={"repos": ["electric-sql/electric"]}, fetcher=make_fetcher(recorded("github"))
    )
    records = await source.fetch()
    assert {"github_stars", "github_forks", "github_issue_velocity", "github_commit_velocity"} <= types_of(
        records
    )

    stars = series_of(records, "github_stars")
    assert len(stars) == 1 and stars[0].metric_value == 7421
    assert stars[0].payload["measurement"] == "point_in_time"

    commits = series_of(records, "github_commit_velocity")
    assert len(commits) == 8
    assert commits[0].previous_value is None
    assert commits[1].previous_value == commits[0].metric_value, "series must chain in date order"
    assert all(r.entity_type == "repository" for r in records)


async def test_github_partial_failure_keeps_the_good_repo(recorded, make_fetcher):
    source = GitHubSource(
        config={"repos": ["electric-sql/electric", "does-not/exist"]},
        fetcher=make_fetcher(recorded("github")),
    )
    with pytest.raises(PartialFetchError) as exc:
        await source.fetch()
    assert exc.value.records, "records collected before the failure must survive"
    assert "does-not/exist" in str(exc.value)


async def test_github_rejects_a_malformed_repo_name(recorded, make_fetcher):
    source = GitHubSource(config={"repos": ["not-a-repo"]}, fetcher=make_fetcher(recorded("github")))
    with pytest.raises(SourceConfigError):
        await source.fetch()


async def test_github_health_check_reports_quota(recorded, make_fetcher):
    source = GitHubSource(config={"repos": ["x/y"]}, fetcher=make_fetcher(recorded("github")))
    health = await source.health_check()
    assert health.healthy and health.quota_remaining == 4987
    assert "Anonymous" in health.detail


async def test_github_requires_a_fetcher():
    with pytest.raises(SourceConfigError) as exc:
        GitHubSource(config={"repos": ["x/y"]})
    assert "fetcher" in str(exc.value)


# --------------------------------------------------------------- hackernews
async def test_hackernews_counts_stories_per_day(recorded, make_fetcher):
    source = HackerNewsSource(
        config={"keywords": ["local-first"], "days": 30}, fetcher=make_fetcher(recorded("hackernews"))
    )
    records = await source.fetch()
    counts = series_of(records, "social_discussion_growth")
    assert counts, "expected a daily count series"
    assert sum(r.metric_value for r in counts) == 12
    assert all(r.is_proxy for r in counts), "forum volume is a proxy and must say so"

    stories = [r for r in records if r.signal_type is None]
    assert stories, "top stories are kept as evidence-bearing raw records"
    assert all(r.metric_value is None for r in stories), "evidence records carry no metric"
    assert stories[0].url


async def test_hackernews_does_not_double_count_as_media_coverage(recorded, make_fetcher):
    source = HackerNewsSource(config={"keywords": ["x"]}, fetcher=make_fetcher(recorded("hackernews")))
    records = await source.fetch()
    assert "media_coverage_growth" not in types_of(records)


# ----------------------------------------------------------------- wikipedia
async def test_wikipedia_series_is_marked_proxy(recorded, make_fetcher):
    source = WikipediaPageviewsSource(
        config={
            "articles": [
                {
                    "title": "Solid-state_battery",
                    "entity_name": "solid-state battery",
                    "entity_type": "technology",
                }
            ],
            "days": 40,
        },
        fetcher=make_fetcher(recorded("wikipedia")),
    )
    records = await source.fetch()
    assert types_of(records) == {"wiki_pageview_growth"}
    assert all(r.is_proxy for r in records)
    assert records[0].payload["proxy_for"] == "search interest"
    ordered = series_of(records, "wiki_pageview_growth")
    assert ordered[1].previous_value == ordered[0].metric_value


# ---------------------------------------------------------------------- rss
async def test_rss_parses_rss20_and_respects_robots(recorded, make_fetcher):
    source = RssSource(
        config={
            "feeds": [
                {
                    "url": "https://regulator.example.gov/feed.xml",
                    "entity_name": "heat pump",
                    "entity_type": "product",
                    "signal_type": "regulatory_catalyst",
                    "geo_scope": "US",
                }
            ],
            "days": 60,
        },
        fetcher=make_fetcher(recorded("rss"), respect_robots=True),
    )
    records = await source.fetch()
    counts = series_of(records, "regulatory_catalyst")
    assert counts and sum(r.metric_value for r in counts) == 6
    articles = [r for r in records if r.signal_type is None]
    assert len(articles) == 6
    assert "<b>" not in (articles[0].content or ""), "HTML must be stripped from excerpts"
    assert articles[0].url.startswith("https://regulator.example.gov/notice/")


async def test_rss_parses_atom(recorded, make_fetcher):
    items = parse_feed(recorded("rss").fixtures["https://press.example.com/atom.xml"]["body"])
    assert len(items) == 2
    assert items[0]["title"] == "Sanitary ware imports climb"
    assert items[0]["published"].year == 2026


async def test_rss_blocked_by_robots_is_reported(recorded, make_fetcher):
    source = RssSource(
        config={"feeds": [{"url": "https://blocked.example.com/feed.xml", "entity_name": "x"}]},
        fetcher=make_fetcher(recorded("rss"), respect_robots=True),
    )
    with pytest.raises(Exception) as exc:
        await source.fetch()
    assert "robots" in str(exc.value).lower()


async def test_rss_html_instead_of_xml_is_an_error_not_silence(recorded, make_fetcher):
    """An error page is valid XML; zero items would be a lie about the publisher."""
    source = RssSource(
        config={"feeds": [{"url": "https://broken.example.com/feed.xml", "entity_name": "x"}]},
        fetcher=make_fetcher(recorded("rss"), respect_robots=True),
    )
    with pytest.raises(SourceUnavailableError) as exc:
        await source.fetch()
    assert "not an RSS or Atom feed" in str(exc.value)


def test_rss_date_parsing_handles_the_formats_feeds_actually_use():
    assert parse_datetime("Mon, 31 Aug 2026 12:00:00 +0000").year == 2026
    assert parse_datetime("2026-08-31T12:00:00Z").tzinfo is not None
    assert parse_datetime("2026-08-31").tzinfo is not None
    assert parse_datetime("garbage") is None
    assert parse_datetime(None) is None


# --------------------------------------------------------------------- fred
async def test_fred_records_a_gap_rather_than_inventing_a_zero(recorded, make_fetcher):
    source = FredSource(
        config={
            "series": [
                {
                    "id": "PCU1",
                    "entity_name": "US cement PPI",
                    "signal_type": "producer_price_index",
                    "geo_scope": "US",
                }
            ],
            "days": 3650,
        },
        credentials={"api_key": "fake-key"},
        fetcher=make_fetcher(recorded("fred")),
    )
    records = await source.fetch()
    assert types_of(records) == {"producer_price_index"}

    values = [r for r in records if r.status == "ok"]
    gaps = [r for r in records if r.status == "missing"]
    assert len(values) == 24
    assert len(gaps) == 1, "FRED's '.' placeholder must be recorded, not skipped"
    assert gaps[0].metric_value is None, "a gap carries no number at all - not even 0"
    assert "no value" in (gaps[0].content or "")
    assert values[0].geo_scope == "US"
    assert not values[0].is_proxy
    assert values[0].confidence >= 0.9


async def test_fred_gap_is_dated_to_the_period_it_describes(recorded, make_fetcher):
    source = FredSource(
        config={
            "series": [{"id": "PCU1", "entity_name": "US cement PPI", "signal_type": "producer_price_index"}],
            "days": 3650,
        },
        credentials={"api_key": "fake-key"},
        fetcher=make_fetcher(recorded("fred")),
    )
    gap = next(r for r in await source.fetch() if r.status == "missing")
    assert gap.published_at.date().isoformat() == "2025-02-01"


async def test_fred_requires_its_api_key(recorded, make_fetcher):
    with pytest.raises(SourceConfigError) as exc:
        FredSource(config={"series": [{"id": "GDP"}]}, fetcher=make_fetcher(recorded("fred")))
    assert "api_key" in str(exc.value)


# ---------------------------------------------------------------- sec_edgar
def test_cik_normalisation():
    assert normalise_cik("320193") == "0000320193"
    assert normalise_cik("CIK0000320193") == "0000320193"
    with pytest.raises(SourceConfigError):
        normalise_cik("abc")


async def test_sec_edgar_emits_filing_cadence_and_revenue(recorded, make_fetcher):
    source = SecEdgarSource(
        config={
            "companies": [{"cik": "0001045810", "name": "NVIDIA Corporation", "ticker": "NVDA"}],
            "days": 1460,
        },
        fetcher=make_fetcher(recorded("sec_edgar")),
    )
    records = await source.fetch()
    assert {"sec_filing_activity", "revenue_acceleration"} <= types_of(records)

    revenue = series_of(records, "revenue_acceleration")
    ends = [r.published_at.date().isoformat() for r in revenue]
    assert len(ends) == len(set(ends)), "restated duplicates must not create two observations"

    filings = [r for r in records if r.signal_type is None]
    assert filings and filings[0].url.startswith("https://www.sec.gov/Archives/edgar/data/")
    assert all(r.geo_scope in ("US", "global") for r in records)


# ----------------------------------------------------------------- comtrade
async def test_comtrade_money_keeps_its_currency(recorded, make_fetcher):
    """A monetary figure must never lose the currency it was reported in."""
    source = UnComtradeSource(
        config={
            "flows": [
                {
                    "reporter": "788",
                    "cmd_code": "6910",
                    "flow": "M",
                    "entity_name": "ceramic sanitary ware (Tunisia imports)",
                    "geo_scope": "TN",
                    "metric": "primaryValue",
                }
            ]
        },
        credentials={"subscription_key": "fake"},
        fetcher=make_fetcher(recorded("comtrade")),
    )
    records = await source.fetch()
    assert all(r.metric_currency == "USD" for r in records)


async def test_comtrade_weight_has_no_currency(recorded, make_fetcher):
    source = UnComtradeSource(
        config={
            "flows": [
                {
                    "reporter": "788",
                    "cmd_code": "6910",
                    "flow": "M",
                    "entity_name": "ceramic sanitary ware",
                    "geo_scope": "TN",
                }
            ]
        },
        credentials={"subscription_key": "fake"},
        fetcher=make_fetcher(recorded("comtrade")),
    )
    records = await source.fetch()
    assert all(r.metric_currency is None for r in records)
    assert records[0].metric_unit == "kg"


async def test_comtrade_builds_an_annual_series(recorded, make_fetcher):
    source = UnComtradeSource(
        config={
            "flows": [
                {
                    "reporter": "788",
                    "cmd_code": "6910",
                    "flow": "M",
                    "entity_name": "ceramic sanitary ware (Tunisia imports)",
                    "geo_scope": "TN",
                }
            ],
            "years": 8,
        },
        credentials={"subscription_key": "fake"},
        fetcher=make_fetcher(recorded("comtrade")),
    )
    records = await source.fetch()
    assert types_of(records) == {"import_growth"}
    assert len(records) == 7
    assert records[0].geo_scope == "TN"
    assert records[0].payload["mirrored"] is False
    assert records[1].previous_value == records[0].metric_value


async def test_comtrade_mirror_mode_is_labelled_and_less_confident(recorded, make_fetcher):
    source = UnComtradeSource(
        config={
            "flows": [
                {
                    "reporter": "434",
                    "cmd_code": "6910",
                    "flow": "M",
                    "entity_name": "ceramic sanitary ware (Libya imports)",
                    "geo_scope": "LY",
                    "use_mirror": True,
                }
            ]
        },
        credentials={"subscription_key": "fake"},
        fetcher=make_fetcher(recorded("comtrade")),
    )
    records = await source.fetch()
    assert records[0].payload["mirrored"] is True
    assert "Mirror statistics" in records[0].payload["note"]
    assert records[0].confidence < 0.9, "mirrored data must be trusted less than direct reports"


async def test_comtrade_requires_its_key(recorded, make_fetcher):
    with pytest.raises(SourceConfigError):
        UnComtradeSource(config={"flows": []}, fetcher=make_fetcher(recorded("comtrade")))


# ------------------------------------------------------------------- shared
@pytest.mark.parametrize(
    "factory",
    [
        lambda f: GitHubSource(config={"repos": ["electric-sql/electric"]}, fetcher=f),
        lambda f: WikipediaPageviewsSource(
            config={"articles": [{"title": "Solid-state_battery"}]}, fetcher=f
        ),
    ],
)
async def test_since_watermark_is_honoured(factory, recorded, make_fetcher):
    transport = recorded("github", "wikipedia")
    source = factory(make_fetcher(transport))
    everything = await source.fetch()
    recent = await source.fetch(since=datetime.now(UTC))
    assert len(recent) <= len(everything)
