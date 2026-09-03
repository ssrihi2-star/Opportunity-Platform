# Product Requirements Review

## 1. What was asked for, and what it really means

The brief describes a research instrument, not a trading system. The hard part is
not collecting data — it is refusing to be fooled. Three requirements carry most of
the product's value and most of its risk:

1. **Multi-signal gate.** Nothing becomes an opportunity on one signal. This is the
   strongest defence against hype and is implemented as a hard gate before scoring,
   not as a soft weight.
2. **Deterministic-first analysis.** Language models are unreliable arithmetic
   engines and confident fabricators. All numbers come from statistics; the model
   only writes prose about numbers it was handed.
3. **Permanent accountability.** Failed predictions are kept. Without this the
   system cannot be evaluated and quietly becomes an opinion generator.

## 2. Assumptions made (please correct any that are wrong)

| # | Assumption | Impact if wrong |
|---|---|---|
| A1 | Single private user first; multi-tenant later. Schema carries `user_id` but no row-level security yet. | Multi-user launch needs an RLS pass. |
| A2 | No paid data subscriptions initially, so "search interest" is a **proxy** (Wikipedia pageviews), not Google Trends. | Weaker attention signal than the brief implies. |
| A3 | Self-hosted single node via Docker Compose. | No HA; managed Postgres would change `deployment.md`. |
| A4 | Arabic UI is Modern Standard Arabic; Tunisian/Libyan dialect not required. | Copy rewrite. |
| A5 | Telegram is the primary push channel; email is SMTP-based. | Needs an SMTP provider. |
| A6 | Currency for capital ranges is USD, with TND/LYD display deferred. | FX layer needed. |
| A7 | No brokerage, no execution, ever, in this codebase. | — |
| A8 | Import cost figures are user-supplied or clearly labelled estimates; no fabricated landed-cost data. | The import analyser is a framework, not an oracle. |
| A9 | Documented JSON APIs are governed by their terms of service rather than robots.txt; robots.txt is honoured for feeds and ordinary web pages. | If you disagree, set `respect_robots = True` on those adapters. |

## 3. Requirements deliberately deferred (with reason)

| Requirement | Phase | Reason |
|---|---|---|
| pgvector topic clustering | 3 | Not needed until there is enough text volume for clustering to beat keyword grouping. Extension and column are provisioned now. |
| Trend persistence and cross-source confirmation scoring | 3 | The statistics exist and are tested; writing `trends` rows needs the multi-source corpus that Phase 2 has only just started collecting. |
| Skeptic + report LLM calls | 4 | Requires the trend layer to have something to be skeptical about. |
| Telegram bot, alerts, watchlist UI | 5 | Depends on opportunities existing. |
| Backtesting outcomes | 6 | Needs 30+ days of stored predictions before it can report anything honest. |
| Import / business / equity / crypto analysers | 4–5 | Category-specific; built on the generic opportunity object. |

Nothing from the brief was silently dropped. Every unbuilt item appears in
`docs/changelog.md` under "Not implemented".

## 4. Definition of success for the MVP

The user opens the dashboard daily and finds it worth reading; the backtesting page
can eventually tell them whether it was. A system that produces five well-evidenced
candidates a week beats one producing fifty unfiltered ones.

## 5. Explicit non-goals

No trade execution. No brokerage connection. No "guaranteed" language. No urgency
theatre. No collection of private or unlawfully obtained data. No automatic buy
recommendation — the report offers *ways to participate*, including "add to
watchlist and do nothing yet".


## Phase 4 assumptions, stated

Three things in Phase 4 are judgements rather than facts, and they are recorded
here so a later reader can disagree with them deliberately:

1. **A weak candidate is not stored at all** (score below 22), with one exception:
   a strong trend that we had enough evidence to judge and found wanting is kept
   and flagged, because "this is real and there is still nothing good here" is a
   finding rather than an absence. The alternative — storing everything and
   filtering in the UI — was rejected because a long list trains a reader to skim.
2. **Analyzer facts that no time series can supply** — supplier counts, filings,
   token distribution — are carried in a context table
   (`app/sources/adapters/scenario_context.py` for the demo). In production these
   come from filings, registries and manual research. Anything absent stays
   `UNKNOWN` and scores nothing, which is the same behaviour real missing data
   produces.
3. **The experimental measures are excluded from the score on purpose.** The
   Geographic Opportunity Gap and the Adoption-to-Attention Ratio both sound
   right and neither has been backtested. They are displayed beside the score,
   never folded into it, until Phase 6 can say whether they predicted anything.
