# Data Source Guide

## 1. What is implemented

Nine adapters. Every one of them is exercised by tests; the network ones are
driven by recorded fixtures in `backend/tests/fixtures/`.

| Adapter | Network | Credential | Signals produced | Notes |
|---|---|---|---|---|
| `demo_mock` | no | — | 15 series across 6 entities | Deterministic generator so the dashboard works with no keys |
| `csv_import` | no | — | whatever the file declares | Upload via the UI or `POST /sources/{id}/upload-csv` |
| `github` | yes | `token` (optional, strongly advised) | `github_stars`, `github_forks`, `github_issue_velocity` (levels), `github_commit_velocity` (52-week history) | 60 req/h anonymous is not enough for more than a couple of repos |
| `hackernews` | yes | — | `social_discussion_growth` + top stories as evidence records | Algolia public search API; one request per keyword |
| `rss` | yes | — | `media_coverage_growth` (or a configured type) + linked excerpts | Honours robots.txt; stores title, short excerpt and link only |
| `wikipedia_pageviews` | yes | — | `wiki_pageview_growth` (**always `is_proxy=true`**) | Stands in for Google Trends, which has no free official API |
| `fred` | yes | `api_key` | any macro series, e.g. `producer_price_index`, `policy_rate` | Free key; official revised data, reliability prior 0.95 |
| `sec_edgar` | yes | — | `sec_filing_activity`, `revenue_acceleration` + filing records | Requires a descriptive User-Agent with contact details |
| `un_comtrade` | yes | `subscription_key` | `import_growth` / `export_growth` by HS code | Supports **mirror statistics** for non-reporting countries |

### What has and has not been verified

The adapters are verified against **recorded fixtures** that reproduce each API's
real response shape, including its awkward cases (FRED's `"."` for a missing
observation, SEC restatements that repeat a period, GitHub's 404, an RSS
publisher returning an HTML error page). They have **not** been run against the
live endpoints from this build, because the environment it was developed in has
no outbound access to those hosts. Treat the first live run of each source as a
verification step: enable one source, press *Test connection*, then *Collect now*,
and read the run history.

## 2. Adding a source

1. Create `backend/app/sources/adapters/<slug>.py`.
2. Subclass `BaseDataSource`, implement `fetch()` and `health_check()`.
3. Set the class attributes that describe the upstream contract:
   `requires_network`, `requires_credentials`, `respect_robots`,
   `default_rate_limit_per_minute`, `documented_rate_limit`.
4. Register it in `backend/app/sources/registry.py` with `@register("<slug>")` and
   add it to `_load_builtin_adapters`.
5. Add a fixture file and an adapter test — **tests never hit the network**.
6. Create the source through the API or add it to `scripts/seed.py`.

```python
class DataSource(Protocol):
    async def fetch(self, since: datetime | None = None) -> list[RawSignal]: ...
    async def health_check(self) -> SourceHealth: ...
```

Adapters never import `httpx`. They call `self.fetcher.get_json(...)` /
`get_text(...)`, and the `Fetcher` applies every policy described in
`architecture.md` §6. That is the mechanism that makes "polite" true rather than
merely claimed.

`RawSignal` is the single normalised envelope every adapter returns:

| Field | Meaning |
|---|---|
| `external_id` | stable id at the source, part of the dedup hash |
| `title`, `content` | untrusted text (sanitised before storage) |
| `url` | canonical link, used later as evidence |
| `published_at`, `fetched_at` | when the thing happened vs when we saw it |
| `metric_name`, `metric_value`, `metric_unit`, `previous_value` | the numeric payload, if any |
| `entity_name`, `entity_type`, `signal_type`, `signal_class` | what this measures |
| `is_proxy` | true when the metric stands in for something it cannot measure |
| `geo_scope` | ISO country code, region code, or `global` |
| `payload` | the verbatim source object, stored for audit |

A `RawSignal` with no `metric_value` is stored as an evidence-bearing record and
never becomes an observation. That is deliberate: a news item is a citation, not a
measurement.

## 3. Legal and technical posture

Rules enforced in code, not just in this document:

* `robots.txt` is fetched and honoured for feed and web adapters, cached 24h. It is
  **not** applied to documented JSON APIs (GitHub, FRED, SEC, Wikimedia, Comtrade,
  Algolia) whose terms govern programmatic access directly — robots.txt is a
  crawler protocol, and treating it as an API contract would block legitimate,
  documented use while doing nothing for the publisher.
* A descriptive `User-Agent` and a `From` contact address are sent on every request.
  Set `USER_AGENT` and `CONTACT_EMAIL` in `.env` to real values before enabling
  SEC EDGAR, which requires them.
* Per-source token-bucket rate limiting, plus a per-run request ceiling
  (`config.max_requests_per_run`, default 500).
* Conditional requests (ETag / If-Modified-Since) on every repeat fetch.
* `Retry-After` is surfaced as a typed error rather than ignored.
* No authenticated scraping of sites that forbid it; no CAPTCHA circumvention; no
  bulk copying of full article bodies — the store keeps title, a ~600 character
  excerpt, the link and the metrics.
* Personal data is not collected. Forum author handles are stored in the payload
  only where they are needed for bot detection.

If a source's terms forbid programmatic access, the adapter is not written. The
CSV importer exists precisely so a human can bring in data they are entitled to use.

## 4. External APIs and their real limits

| Source | Access | Documented limit | Practical constraint |
|---|---|---|---|
| GitHub REST | token (optional) | 5,000 req/h authenticated, 60/h anonymous | Star counts have no history: the series starts the day you start collecting |
| Algolia HN Search | open | ~10,000 req/h per IP | Search relevance is keyword-literal; tune the keyword list |
| RSS / Atom | open | per publisher | Many feeds only expose the last 10–50 items, so history is shallow |
| Wikimedia pageviews | open | 100 req/s | A **proxy** for interest, not demand |
| FRED | free key | 120 req/min | Series are revised; observations are dated to the period, not the release |
| SEC EDGAR | open, UA required | 10 req/s | US issuers only; XBRL concept names vary between filers |
| UN Comtrade | free subscription key | free tier ~500 calls/day | Reporting lags 1–6 months; some countries report annually or not at all |
| Google Trends | **no free official API** | — | Not implemented. Wikipedia pageviews is the labelled substitute |
| Reddit | paid tiers | plan-dependent | Not implemented |
| Product Hunt | GraphQL, licence-bound | varies | Not implemented; storage rights unclear |
| Alibaba / marketplaces | no compliant open API | — | Manual CSV or licensed data only |

Two limitations worth restating because they change how the output must be read:

1. **There is no free search-demand signal.** `wiki_pageview_growth` is flagged
   `is_proxy=true` everywhere it appears, and a proxy alone cannot satisfy the
   attention requirement in the opportunity gate.
2. **Tunisia and Libya customs data is not machine-readable at a useful cadence.**
   The Comtrade adapter therefore supports `use_mirror: true`, which reads the
   partners' reported exports *to* that country instead. Mirrored rows carry a
   lower confidence (0.75 vs 0.90) and a `note` in the payload saying so.

## 5. Source reliability

`sources.reliability` is 0–1, computed by `app/sources/reliability.py`:

```
reliability = 0.7 * class_prior + 0.3 * (1 - recent_failure_rate)            # no backtest yet
            = 0.5 * class_prior + 0.3 * (1 - failure_rate) + 0.2 * precision # once backtesting has data
```

Class priors: official/government 0.95, regulated filings 0.90, primary API 0.80,
manual import 0.70, aggregator 0.60, demo 0.50, forum/social 0.40, anonymous blog 0.25.

Note the deliberate asymmetry: Hacker News enters at 0.40 and FRED at 0.95. Forum
volume is corroboration, never a foundation.
