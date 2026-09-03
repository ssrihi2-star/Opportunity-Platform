# Testing Strategy

## Layers

| Layer | Tool | What it proves |
|---|---|---|
| Unit | pytest | scoring maths, statistics, sanitiser, dedup hashing, entity normalisation, CIK/date parsing |
| Trend analytics | pytest | windowed growth, momentum, acceleration, persistence, spike vs acceleration, seasonality, confirmation, syndication, Trend Score, Confidence, stage ladder, lifecycle transitions |
| Entity resolution | pytest | every identifier kind merges; similar names never merge; review candidates raised; decisions honoured and not overwritten |
| Scenario end-to-end | pytest | the demo scenarios run through the real engine and land on their expected score, stage and state |
| Opportunity gate | pytest | what is refused, and that every refusal explains itself |
| Opportunity scoring | pytest | components, penalties, confidence, lifecycle, personal relevance, and the golden **trend ≠ opportunity** test |
| Skeptic and risk | pytest | the skeptic only ever reduces confidence; critical risks dominate rather than average; crypto is never low risk |
| Analyzers | pytest | an unknown stays UNKNOWN; no market size, price, supplier, margin or willingness to pay is ever invented |
| Golden vectors | pytest + JSON | the scoring engine is a pure function whose behaviour cannot drift unnoticed |
| Database | pytest + SQLAlchemy | constraints, unique indexes, immutability mixin |
| API | pytest + httpx ASGI client | status codes, auth, RBAC, validation, pagination, CSV upload, credential handling |
| HTTP policy | pytest + RecordedTransport | robots.txt, rate limits, per-run ceiling, conditional requests, 429/4xx/5xx handling, SSRF |
| Source adapters | pytest + recorded fixtures | every adapter's parsing, series chaining, proxy flags, partial failure |
| Security | pytest | prompt-injection corpus, SSRF guard, permission matrix, credential masking |
| Grounding | pytest | a report citing nonexistent evidence is rejected |
| E2E | Playwright | not part of the committed suite; each phase is driven through a real browser (login, dashboard, trend detail, entity review, Arabic/RTL, dark mode) with the console watched for errors |

## Rules

* **No test may make a network call.** `conftest.py` installs a socket guard that
  raises on `connect`, and every network adapter is driven by `RecordedTransport`
  reading `backend/tests/fixtures/*.json`. A request with no matching fixture
  raises, so a test can never pass by silently reaching the internet.
* **No test may call an LLM provider.** The default is `EchoProvider`.
* Fixtures reproduce each API's awkward real-world cases on purpose: FRED's `"."`
  placeholder, SEC restatements repeating a period, GitHub's 404, an RSS publisher
  returning an HTML error page, a robots.txt that disallows everything.
* Changing the scoring formula requires bumping `FORMULA_VERSION` and regenerating
  `tests/data/scoring_vectors.json` in the same commit; a test asserts this. The
  trend engine has its own `TREND_FORMULA_VERSION`, stamped on every snapshot.
* Migrations are exercised against **PostgreSQL**, not only the SQLite used by the
  test suite, on an `upgrade → downgrade → upgrade` round trip. SQLite does not
  enforce foreign keys by default and will happily run a migration that PostgreSQL
  rejects; that difference has already caught one real ordering bug.
* Coverage target is meaningful, not maximal. Analytics and security modules should
  stay high; glue code is not padded with trivial tests.

## Running

Current state: **442 tests pass**, `ruff` clean, **87% statement coverage**.

### The tests that encode the product's judgement

Three assertions are worth calling out, because they are the ones that would let
the product become dishonest if they were ever deleted:

* `test_a_high_trend_score_does_not_imply_a_high_opportunity_score` — a trend of
  90 in a saturated, expensive, undefensible market must score below 40.
* `test_the_skeptic_can_only_reduce_confidence` — parametrised across the whole
  0–100 range and three input shapes.
* `test_no_stored_text_ever_tells_anyone_to_buy` — a blunt sweep over every title,
  thesis, warning, risk rationale and report body for `guaranteed`, `sure thing`,
  `100x`, `next bitcoin`, `buy now` and `risk-free`.

**Live data is validated by a separate script, never by the suite.**
`python -m scripts.live_validation` is the only thing in the repository that
deliberately touches the internet; see `docs/live-validation.md`.

```bash
make test            # full backend suite
make test-fast       # unit only
cd backend && ENV=test pytest -q --cov=app
docker compose run --rm api pytest -q
```
