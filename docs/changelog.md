# Changelog

## [0.4.0] — Phase 4: turning trends into opportunity candidates

The system now answers a second question: given that something is changing, is
there a realistic way to benefit, why might it matter, and what would prove us
wrong? It still tells nobody to buy, invest, import or start anything; the most
confident label it can reach is `strong_evidence`, meaning "go and check this
yourself".

### The live-data gate — attempted, blocked, honestly reported
* **Live validation was attempted and could not be performed.** The build
  environment sits behind an egress allowlist that permits package registries and
  nothing else; GitHub, Hacker News, Wikimedia, SEC, FRED and UN Comtrade were all
  refused at the proxy, not by the code. The exact responses are recorded in
  `docs/live-validation.md`.
* Consequently **every opportunity in this build carries `validation_status =
  DEMO`**, the dashboard shows a DEMO DATA badge on every card, and the detail page
  carries a banner. No documentation claims the adapters were proven against live
  APIs — only against recorded fixtures, which is a weaker and different statement.
* `scripts/live_validation.py` is the harness that closes the gate on a machine
  with network access. It runs the whole path (live source → raw → entity →
  observation → series → trend), fetches each source **twice** to prove
  deduplication, and checks response shape, pagination, the rate limiter and the
  per-run ceiling, legible failures, entity resolution, timestamp sanity, that gaps
  stay missing, that real zeros stay zero, and that resulting scores are plausible.
  It exits non-zero on any failure and never tunes a threshold.

### Added
* **Four opportunity types** — business, import/distribution, public investment,
  crypto — each judged by its own criteria. A crypto asset is examined *only* as a
  crypto opportunity, because re-labelling is how a risk floor gets dodged.
* **An evidence gate** (`opportunity_config.GATE`, versioned): ≥3 distinct signal
  *types*, ≥2 distinct *classes*, ≥2 independent source families, trend score ≥30
  and confidence ≥35, ≥20 observations over ≥45 days, ≤35% missing, ≤75% proxy,
  and an `active`/`confirmed`/`weakening` trend that is neither a spike nor
  seasonal. Three news outlets are one type; three attention metrics are one class.
* **Opportunity Score (0–100)** over eight components — real adoption 20, market
  potential 15, evidence diversity 15, early entry 15, accessibility 10,
  defensibility 10, catalyst 10, timing 5 — with twenty penalties capped at 65.
  Every component, its maximum, its sentence, every penalty and the pre-penalty
  subtotal are printed in the UI.
* **Confidence scored separately**, over evidence volume, quality, independent
  confirmation, freshness, source agreement and real commercial data, minus
  penalties for missing evidence, assumptions and staleness.
* **Four analyzers** (`app/analytics/analyzers.py`). Every one records an unknown
  as `UNKNOWN` and scores it zero. Willingness to pay is never inferred from
  interest; a market size is never estimated; and a valuation is never formed from
  a price older than five days — no valuation is better than a misleading one.
* **A skeptic agent** that asks all seventeen questions from the brief, records
  which were asked, and outputs the strongest counterargument, missing evidence,
  red flags, alternative explanations, a manipulation probability, and reasons the
  candidate may be too late or out of reach. **It can only reduce confidence**, and
  a parametrised test asserts that across the full input range. Two objections are
  raised for every candidate: survivorship bias, and the possibility that the trend
  succeeds while this particular way of participating fails.
* **A risk engine** over eleven categories, where the overall level is **dominated
  by the worst finding rather than averaged**, with severity, our confidence that
  the risk is real, evidence, rationale and a mitigation where one honestly exists.
  Crypto carries a hard `high` floor that is reported, not applied silently.
* **Confirmation and invalidation conditions**, stored as checkable rows with a
  `measurable` payload rather than as prose. No candidate is stored without both.
* **Personal Relevance**, a separate 0–100 score computed entirely from stored
  preferences. Nothing about any person or country is in the domain model — a test
  asserts that changing the preference row changes the answer.
* **Two experimental measures**, displayed on their own and contributing **nothing**
  to the score until Phase 6 can backtest them: the Geographic Opportunity Gap
  (adoption, attention, supply, competitor and time-lag differences, with prices
  compared only in matching currencies) and the Adoption-to-Attention Ratio.
* **A human decision log**: INTERESTED / NOT_INTERESTED / RESEARCHING / REJECTED /
  ACTED_ON / WATCHING with a note, append-only, freezing the score, confidence,
  risk, relevance, state and algorithm version as they stood at the moment of the
  decision — which is what makes Phase 6 backtesting possible at all.
* **An evidence-backed report** with all thirteen required sections, complete with
  no language model configured. A model may optionally re-phrase three sections;
  its output is verified against a closed fact set at density 0.9 and scanned for
  forbidden phrasing, and a failing draft is dropped rather than repaired.
* **`/opportunities` dashboard and detail page**, EN/AR with RTL and light/dark,
  with six sort orders and five filters, showing the full calculation, the case
  against, the risks, both condition sets, ways to take part, and the decision log.
* **Six new demo scenarios** (A–F) covering a real buildable opportunity, sustained
  hype with no substance, a real trend with a bad investment attached, a geographic
  import gap, a real trend with nowhere to stand, and a scam-shaped token.
* New docs: `docs/opportunity-methodology.md`, `docs/live-validation.md`.

### Fixed / hardened
* **Topic clustering merged unrelated things through a chain.** Co-occurrence
  within a single collection run was treated as a topical relation, so a manual
  spreadsheet containing a water heater and an AI tool glued them into one cluster
  labelled "AI Water". Clustering now builds one group per distinctive token and
  merges two groups only when half their combined membership overlaps; co-occurrence
  may only *reinforce* a relationship the names already imply. The demo now produces
  "AI Agents" and "Water" as separate, coherent topics.
* **A full `alembic downgrade base` failed** on a missing index: migration 0002
  rebuilt the Phase 1 `trends` table without restoring the index that 0001's
  downgrade drops. The whole chain now round-trips on PostgreSQL.
* **Migration 0003's autogenerated downgrade could not run**: the foreign key from
  `opportunities` to `trends` was created anonymously and therefore could not be
  dropped. It is now named explicitly.
* `SystemAuditLog.object_id` is a string column; the decision endpoint was passing a
  UUID, which PostgreSQL rejected at insert time.
* Import/distribution was being proposed for anything measured in a single country,
  producing nonsense candidates such as "a listed company, imported". It now
  requires actual trade-class evidence on a product or commodity.
* Analyzer growth figures were rendered at full float precision (`10.33362442926142`)
  in the UI. These are averages of noisy series; the extra digits were false accuracy.
* The application imported demo data from `scripts/`; the scenario context moved into
  `app/sources/adapters/` so the API never depends on a maintenance script.

### Deliberate design decisions worth knowing about
* **A weak candidate is normally not stored at all** — three meaningful candidates
  beat a hundred exciting ones. The one exception is a *considered* negative: when
  the underlying trend is strong **and** the analyzer had enough evidence to judge
  it, a low-scoring candidate is kept and flagged, because "this is real and there
  is still nothing good here" is a finding, and hiding it would let a reader assume
  the trend implies the chance.
* **A trend with no accessible way to take part is refused outright**, not scored
  low. It is a fact about the world, not an opportunity.
* `POST /opportunities/generate` returning `created: 0` is a successful response.

### Not implemented (deliberate, scheduled)
* Checking confirmation and invalidation conditions against arriving data over time
  — Phase 5/6. The conditions are stored in checkable form; nothing checks them yet.
* Currency conversion. Amounts keep their original currency, and the geographic gap
  refuses to compare prices across currencies rather than converting them.
* Watchlists, alerts, Telegram — Phase 5. Backtesting — Phase 6.
* Learning source reliability from observed precision — needs Phase 6 outcomes.

### Verified / not verified
* **Verified by execution**: 442 tests pass, zero failures; 87% statement coverage; `ruff` clean; the
  frontend type-checks, lints and builds (12 routes); the full migration chain
  round-trips on PostgreSQL 16 (`upgrade → downgrade base → upgrade`) creating 42
  tables; the seed runs and produces the verdicts below; running the generator twice
  creates nothing new and appends score snapshots; the UI was driven through a real
  browser — the opportunities list, three detail pages, the report, and a recorded
  decision.
* **Verified numbers** (PostgreSQL, opportunity formula 1.0.0):

  | Scenario | Trend | Opportunity | Confidence | Risk | Relevance | Verdict |
  |---|---|---|---|---|---|---|
  | A — edge inference tooling | 45 | 56 | 69 | high | 54 | promising |
  | D — solar water pumps (import into LY) | 53 | 39 | 77 | high | 60 | watchlist |
  | C — ThermaCore Industries (equity) | 44 | 13 | 54 | very high | 50 | candidate |
  | B — quantum wellness devices | 45 | 11 | 42 | high | 65 | candidate |
  | F — Luna9 Token (crypto) | 60 | 0 | 31 | very high | 50 | candidate |
  | E — EUV lithography capacity | 48 | — | — | — | — | refused: no accessible way in |

  22 trends produced 8 candidates and 17 explained refusals.
* **NOT verified**: anything against live internet data. See the gate section above
  and `docs/live-validation.md`.
* **NOT verified**: the AI narration against a real language model. No provider is
  configured, so the report is fully deterministic and the narration path returns
  nothing. The verifier and the forbidden-phrase scan are both tested against
  deliberately bad input.
* **NOT verified**: `docker compose up` — still no Docker daemon in the environment.
  The stack was run natively (PostgreSQL 16 + pgvector, Redis, uvicorn, `next start`).

---

## [0.3.0] — Phase 3: understanding trends

The system now answers five questions about a subject: what is it, is activity
increasing, is the increase unusual, do independent sources agree, and what stage
does it look like. It still says nothing about buying, importing or starting
anything — that is Phase 4.

### Added
* **Identifier-based entity resolution** (`app/services/entities.py`). Records merge
  only on a shared official identifier — SEC CIK, GitHub repo id, ticker+exchange,
  ISO country code, contract address+chain, HS code, Wikipedia page id — or an exact
  alias, or a decision a human already made. There is no automatic name-similarity
  merge at any threshold. New entity types: `software_project`, `crypto_asset`,
  `commodity`, `skill`.
* **Human review queue** (`entity_match_candidates`, `/entity-review`, `/review`):
  a resembling name creates a *new* entity plus a review row. A person chooses
  CONFIRM, REJECT or KEEP SEPARATE; the decision is stored permanently with the
  actor, timestamp and note, and re-deciding returns `409`.
* **Deterministic topic clustering** (`app/services/topics.py`): union-find over
  distinctive shared tokens plus co-occurrence. A model may name or describe a
  cluster it did not create; `topics.label_is_ai_generated` records when it did.
* **Gap-aware time series**: `signal_observations.value` is nullable and carries
  `status` (`ok` / `missing` / `failed`). A period a source did not publish stores
  `NULL`, never `0.0`. `build_gap()` in the source layer, and FRED's `"."` rows now
  produce gaps instead of being dropped.
* **Currency is preserved**: observations store `amount` + `currency`, in the unit
  the source published. No destructive conversion to USD.
* **Growth analytics** (`app/analytics/growth.py`): 7/30/90/365-day windowed growth,
  regression momentum, acceleration in percentage points, persistence across blocks,
  spike detection and seasonality (only over ≥ 2 full cycles).
* **Spike vs acceleration**: a peak counts as a one-day spike only if it exceeds both
  the series median *and its immediate neighbours* by 5×, is at most 2 points wide,
  and either reverted or is the newest point. `10, 11, 9, 300, 12` is a spike;
  `700 → 1200` is growth.
* **Independent confirmation** (`app/analytics/confirmation.py`, `dedup.py`): sources
  grouped by `source_group`; near-duplicate headlines merged by an order-independent
  fingerprint and Jaccard ≥ 0.7, so five outlets running one wire story count once.
* **Trend Score (0–100)** with seven components and nine penalties, capped at 60
  penalty points, `TREND_FORMULA_VERSION = 1.0.0`. The full calculation — every
  component, its maximum, its rationale, every penalty, the pre-penalty subtotal —
  is printed on the trend page.
* **Confidence Score (0–100)** computed and displayed separately.
* **Seven stages** (`weak_signal` → `declining`) from a deterministic ladder; no
  model participates in the decision.
* **Six-state lifecycle** (`candidate`, `active`, `confirmed`, `weakening`, `ended`,
  `invalidated`) on a single row per subject, enforced by `ux_trend_subject`, with an
  immutable `trend_snapshots` row appended per evaluation. Re-running the engine
  updates the same trends rather than inventing new ones.
* **Frontend**: `/trends` dashboard (five filters, five sort orders, spike and
  seasonal toggles) and `/trends/{id}` detail page (score tiles, warnings, series
  chart with gap count, why-it-was-flagged, the full score and confidence tables,
  supporting measurements, related entities, score history, evidence timeline),
  plus `/review`. All EN/AR with RTL and light/dark, including translated stage and
  lifecycle vocabulary.
* **Grounded narration**: `POST /trends/{id}/explain` builds a closed fact set from
  stored numbers, wraps it in the Phase 1 untrusted-content envelope and runs the
  result through the grounding verifier at density 0.9. A draft that fails is
  dropped, never repaired.
* **Six demo scenarios** driven by a `scenario` adapter, seeded and evaluated end to
  end.
* New docs: `docs/trend-methodology.md`.

### Fixed / hardened
* **Migration ordering bug caught on PostgreSQL**: `0002` created `trend_signals` and
  `trend_snapshots` before dropping and rebuilding `trends`. SQLite does not enforce
  foreign keys and ran it happily; PostgreSQL refused. The rebuild now happens first,
  and the round trip `upgrade → downgrade → upgrade` is verified on PostgreSQL.
* Spike detector was flagging genuine acceleration, because a growing series' maximum
  is always its newest point — fixed by the neighbour-ratio requirement.
* Composite identifiers (`chain_address`, `ticker_exchange`) were lost when
  re-canonicalised, so a stored crypto entity stopped matching itself.
* The duplication ratio was measured over machine-generated series titles, which
  penalised real trends 12 points and hid genuine syndication. It now looks only at
  records that carry a URL.
* A single source could reach `active`; it now stays `candidate` until something
  independent confirms it.
* Spike and seasonal trends were promoted and then invalidated on the next run; they
  now never rise above `candidate` in the first place.
* CSV `currency` / `status` columns were validated during fetch but not on upload, so
  a bad upload returned `200`. Validation moved into `parse_rows`.
* `persistence` returned nothing for short series; block count is now adaptive.

### Not implemented (deliberate, scheduled)
* Currency conversion (display-time, over the preserved original) — later phase.
* pgvector embedding-based clustering: the schema column exists, but Phase 3 clusters
  deterministically on tokens, which is what the brief asked for.
* Source-quality scoring learned from observed precision — needs Phase 6 outcomes.
* Anything that ranks, recommends, or says buy / invest / import — Phase 4.

### Verified / not verified
* **Verified by execution**: 270 tests pass; `ruff` clean; the frontend type-checks,
  lints and builds (10 routes); migrations run on PostgreSQL 16 through a full
  `upgrade → downgrade → upgrade` round trip creating 37 tables; the seed runs and
  produces the six scenario verdicts below; running `evaluate_trends` twice keeps the
  same trend rows and appends snapshots; the UI was driven through a real browser —
  trends list, trend detail (including the hype case), entity review, Arabic/RTL and
  dark mode — with an empty console.
* **Verified numbers** (PostgreSQL, formula 1.0.0):

  | Scenario | Score | Confidence | Stage | State |
  |---|---|---|---|---|
  | AI coding agents | 67 | 87 | accelerating | confirmed |
  | AI Agents (topic) | 63 | 92 | early adoption | confirmed |
  | mycelium packaging | 43 | 84 | emerging | active |
  | ceramic floor tiles | 36 | 80 | mature | candidate (seasonal) |
  | DVD authoring software | 14 | 67 | declining | weakening |
  | ZephyrCoin | 0 | 70 | weak signal | candidate (one-day spike) |

* **Not verified**: the trend engine against *live* internet data. The scenarios are
  generated series with known shapes, which is what makes the expected results
  checkable; the Phase 2 adapters that would feed real series remain fixture-tested
  only, because the build environment has no outbound access to those hosts.
* **Not verified**: the AI explanation against a real language model. No provider is
  configured, so `EchoProvider` is used and the endpoint returns nothing rather than
  prose. The grounding verifier itself is tested against fabricated citations.
* **Not verified**: `docker compose up` — still no Docker daemon in the environment.
  The stack was run natively (PostgreSQL 16 + pgvector, Redis, uvicorn, `next start`).

---

## [0.2.0] — Phase 2: real data sources

### Added
* **Injectable HTTP layer** (`app/sources/http.py`): a `Fetcher` that owns SSRF
  guarding, robots.txt (fetched, parsed, cached 24h), per-source token-bucket rate
  limiting, a per-run request ceiling, conditional requests (ETag /
  If-Modified-Since), `Retry-After` handling and typed errors. Adapters never see
  an HTTP client, so the policy cannot be bypassed.
* `RecordedTransport`: replays JSON fixtures, and raises on an unrecorded URL — the
  mechanism that makes "no test touches the network" enforceable rather than aspirational.
* **Seven network adapters**: `github`, `hackernews` (Algolia search API), `rss`
  (RSS 2.0 + Atom), `wikipedia_pageviews`, `fred`, `sec_edgar`, `un_comtrade`
  (including mirror statistics for non-reporting countries).
* `http_cache_entries` table + migration column set, so ETags survive between runs.
* **Credential management**: `GET/PUT/DELETE /sources/{id}/credentials`,
  Fernet-encrypted at rest, masked hints only over the API, rotation timestamps,
  audit-logged without the secret.
* **CSV upload**: `POST /sources/{id}/upload-csv`, validating header, encoding,
  size and row count *before* storing, and clearing the watermark so the new rows
  are ingested.
* **Source health probe**: `GET /sources/{id}/health?probe=true` runs the adapter's
  own health check and reports missing credentials and the documented upstream limit.
* **Adapter catalogue**: `GET /sources/adapters` — what each adapter needs and what
  its upstream allows, surfaced in the UI.
* Run bookkeeping extended with `records_rejected` and `http_requests`; metrics
  endpoint gained `ois_http_requests_total` and `ois_source_runs_failed_total`.
* Signal taxonomy extended with `macro` and `filing` classes and a `PROXY_SIGNALS` set.
* Dashboard: expandable source cards with run history, credential editor, CSV
  upload, connection test and enable/disable; signals list gained source and class
  columns; overview shows the source and HTTP-request count per run.
* Seed now registers all nine sources — offline ones enabled, every network source
  **disabled** and pre-configured, so nothing reaches the internet unbidden.
* 161 tests (was 105), including 37 covering the HTTP policy layer and adapters.

### Fixed / hardened
* A 4xx from a configured resource is now an error naming the URL, instead of
  silently producing zero records. Endpoints where "absent" is legitimate opt in
  with `allow_status={404}`.
* RSS: an HTML error page is valid XML but is not a feed; the parser now rejects a
  root element that is not `rss`/`feed`/`rdf` rather than reporting "no items".
* XML denial-of-service: feeds are parsed with `defusedxml` when available;
  otherwise a DOCTYPE is refused and bodies are capped at 8 MB.
* Transport errors no longer escape as opaque `ExceptionGroup`s from the async stack.
* `CORS_ORIGINS` is parsed as a comma-separated string; pydantic-settings
  JSON-parses complex env values before validators run, so the previous `list[str]`
  annotation made the documented `.env` format crash at startup.

### Not implemented (deliberate, scheduled)
* Topic clustering with pgvector embeddings — Phase 3.
* Persisted `trends` rows and cross-source confirmation scoring — Phase 3.
* Opportunity generator, skeptic agent, report generator endpoints — Phase 4.
* Import / business / public-equity / crypto analysers — Phase 4–5.
* Watchlists, alerts, notifications, Telegram bot — Phase 5 (tables exist).
* Backtesting outcome jobs and dashboard — Phase 6 (tables exist).
* Vendor LLM adapters are guards that fail loudly, not integrations.
* MFA, DB-level immutability triggers, KMS-backed secrets, automated backups,
  credential re-encryption script, row-level security — see `docs/security.md` §3.
* Playwright end-to-end tests — Phase 4.

### Verified / not verified
* Verified by execution: 161 tests pass; lint clean; migration creates 33 tables;
  seed is idempotent (second run stores 0, reports duplicates); the API and
  dashboard were driven through a real browser — login, overview, signal detail,
  source management, credential storage (masked to `dem...890`), and a live
  connection probe whose failure surfaced as a clear typed error.
* **Not verified**: live collection from GitHub, HN, RSS, FRED, SEC, Comtrade or
  Wikimedia. The build environment has no outbound access to those hosts. The
  adapters are proven against recorded fixtures only.
* **Not verified**: `docker compose up`. The compose file is config-validated
  (`docker compose config`) but no Docker daemon was available to run it.

---

## [0.1.0] — Phase 1: foundation

### Added
* Docker Compose stack: Postgres 16 + pgvector, Redis 7, FastAPI API, Celery worker,
  Celery beat, Next.js web.
* Full SQLAlchemy 2.0 model set with UUID PKs, timestamps, unique constraints and
  an `ImmutableMixin` enforcing append-only rows.
* Alembic migration `0001_initial`.
* Argon2id password hashing, JWT access + rotating refresh tokens, jti denylist,
  RBAC (`admin`/`analyst`/`viewer`).
* User preferences.
* Source registry with the `DataSource` protocol, adapter registration decorator,
  reliability tracking, `source_runs` bookkeeping and stale-run reaping.
* Offline adapters: `demo_mock` (deterministic) and `csv_import`.
* Ingestion pipeline: validate -> normalise -> content-hash dedup -> immutable
  `raw_records` -> entity/alias resolution -> `signals` + `signal_observations`.
* Deterministic statistics: pct change, moving average, z-score, EWMA,
  acceleration, changepoint.
* Scoring engine, eligibility gate and golden vectors.
* Security: SSRF guard, prompt-injection sanitiser with envelope wrapping, rate
  limiting, security headers, audit-log middleware, Fernet-encrypted credentials.
* AI provider abstraction with `EchoProvider`, budget guard and cost ledger.
* Grounding verifier rejecting reports that cite unknown evidence.
* REST API + OpenAPI; Next.js dashboard in English and Arabic with RTL and dark mode.
* Seed script with demo user, sources, ~2,200 raw records and observations.
* 105 tests.
