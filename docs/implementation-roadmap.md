# Implementation Roadmap

| Phase | Scope | Exit criteria | Status |
|---|---|---|---|
| 1 | Foundation: Docker, Postgres+pgvector, Redis, auth, RBAC, preferences, source registry + `DataSource` interface, offline sources, raw storage, dedup, signal generation, dashboard shell (EN/AR/RTL, dark mode), tests, docs | `docker compose up` works; login works; a mock source ingests; signals render; tests pass | **DONE** |
| 2 | Real sources: GitHub, Hacker News, RSS/Atom, FRED, SEC EDGAR, UN Comtrade, Wikipedia pageviews; injectable HTTP layer with robots/rate-limit/SSRF/conditional requests; encrypted credential management; CSV upload; source health probe and run history in the UI | every adapter tested against recorded fixtures; credentials never returned; conditional requests demonstrably reduce refetching | **DONE** |
| 3 | Trend understanding: identifier-based entity resolution + human review queue, deterministic topic clustering, gap-aware time series with currency, growth/momentum/acceleration/persistence, spike and seasonality detection, syndication vs independent confirmation, Trend Score + separate Confidence Score with a visible calculation, seven-stage ladder, six-state lifecycle, `/trends` dashboard and detail page, grounded plain-English narration | 270 tests pass; the six demo scenarios each produce their expected verdict; the hype scenario scores 0 despite +197.7% growth | **DONE** |
| 4 | Opportunity intelligence: live-validation harness, evidence gate, four analyzers (business / import / public company / crypto), Opportunity Score with separate confidence, skeptic agent, eleven-category risk engine with a crypto floor, confirmation and invalidation conditions, personal relevance, experimental geographic gap and adoption-to-attention ratio, human decision log, evidence-grounded reports, opportunities dashboard and detail page | every claim resolves to a stored row; a high trend score demonstrably does not imply a high opportunity score; the engine refuses more trends than it accepts | **DONE (demo data; live validation pending — see `docs/live-validation.md`)** |
| 5 | Personalisation: watchlists, Tunisia/Libya relevance scoring, import analyser, business analyser, public-equity analyser, crypto risk analyser, Telegram bot + alerts, notes and decisions | daily Telegram digest; import analyser produces a labelled-assumption sheet | not started |
| 6 | Backtesting: prediction snapshots, +30/90/180/365 outcome jobs, precision and false-positive metrics, per-source and per-signal performance, performance dashboard | dashboard shows honest hit/miss counts including failures | not started |

## Sequencing rationale

Sources before statistics — there was nothing to analyse otherwise, which is why
Phase 2 came before the trend engine even though the brief lists them the other way
round for the signal layer. Statistics before opportunities, because the gate needs
real series from independent sources. Opportunities before alerts, because there is
nothing to alert on. Backtesting last only because it needs elapsed calendar time;
`predictions` rows are written from Phase 4 onward so the history exists when the
module lands.

## MVP definition (section 25 of the brief)

Single user, auth, EN/AR + RTL, five data sources, daily collection, topic and
entity extraction, signal storage, trend detection, scoring, skeptic review,
evidence-backed report, watchlist, Telegram summary, notes, prediction history,
basic backtesting dashboard.

Phase 2 delivers the "five data sources" and "daily collection" items — nine
adapters, seven of them live-capable. Phase 3 delivers "topic and entity
extraction", "signal storage" and "trend detection". Scoring, skeptic review,
evidence-backed reports and the rest land across Phases 4–6.

## Phase 4 exit checklist

| Requirement | State |
|---|---|
| Live-data validation attempted and truthfully reported | done — attempted, blocked by the environment's egress allowlist, harness and checklist shipped, every candidate labelled DEMO |
| Candidates only from qualifying evidence | done — 3 signal types, 2 classes, 2 source families, plus quality and confidence floors |
| Trends are not automatically opportunities | done — 22 trends produce 8 candidates and 17 explained refusals |
| Business analyzer | done — willingness to pay stays UNKNOWN when unevidenced |
| Import/distribution analyzer | done — MOQ, CBM, shipping, customs, certification, margin |
| Public-company analyzer | done — business quality separate from valuation; no valuation from a stale price |
| Crypto rules | done — nine automatic flags, hard risk floor |
| Opportunity Score transparent | done — every component, penalty and subtotal shown |
| Confidence separate | done |
| Risk separate, critical risks dominating | done — never averaged |
| Skeptic analysis | done — 17 questions, may only reduce confidence |
| Confirmation conditions | done — stored as checkable rows |
| Invalidation conditions | done — no candidate is stored without them |
| Geographic Opportunity Gap (experimental) | done — displayed separately, contributes nothing to the score |
| Adoption-to-Attention Ratio (experimental) | done — same treatment |
| Opportunities dashboard and detail page | done — EN/AR, RTL, light/dark |
| Human decision logging | done — append-only, freezes the numbers |
| Every report evidence-grounded | done — complete without a model; generated text verified or dropped |
| Demo scenarios produce expected results | done — all six |
| Golden tests pass | done — including trend 90 / opportunity < 40 |
| Migrations succeed | done — full chain round-trips on PostgreSQL 16 |
| Documentation updated | done |

## Phase 3 exit checklist

| Requirement | State |
|---|---|
| Entities matched on official identifiers only | done — `external_ids`, no auto-merge threshold |
| Uncertain matches queued, never guessed | done — `entity_match_candidates` + `/review` |
| Every human decision stored permanently | done — re-deciding returns 409 |
| Topics clustered deterministically; AI may name, not invent | done — union-find; `label_is_ai_generated` |
| Missing data stored as missing, never 0 | done — nullable value + `status` |
| Money keeps its original currency | done — `currency` column; no destructive conversion |
| Growth, momentum, acceleration, persistence | done — `analytics/growth.py` |
| One-day spikes distinguished from real acceleration | done — neighbour-ratio test |
| Syndicated coverage does not count as confirmation | done — `source_group` + headline fingerprints |
| Trend Score 0–100 with the calculation shown | done — full component and penalty table in the UI |
| Confidence scored separately | done |
| Seven stages decided without AI | done — deterministic ladder |
| Lifecycle: same trend updated, not re-created | done — `ux_trend_subject` + snapshots |
| `/trends` dashboard with sorting and filters | done |
| AI explanation grounded, never inventing evidence | done — verifier at density 0.9; failures dropped |
| No BUY / INVEST / IMPORT language anywhere | done — Phase 4 |
