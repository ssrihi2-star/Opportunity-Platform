# Opportunity Intelligence System (OIS)

An evidence-first research instrument. It collects public data, measures how fast
things are changing, and helps you investigate what looks early and real.

**It is not financial advice, it does not trade, and it never tells you to buy
anything.** Its job is to rank what deserves your research time and to be honest
about what it does not know.

> **Status: Phases 1–4 complete and tested against demo data.** Phases 5–6 are
> specified in `docs/implementation-roadmap.md` and listed as not implemented in
> `docs/changelog.md`. Nothing in this repository is a silent stub.
>
> **The live-data validation gate has NOT been passed.** The build environment has
> no outbound network access, so every opportunity candidate is labelled `DEMO` in
> the database and on screen. `docs/live-validation.md` explains exactly what was
> attempted, what was refused, and the one command to run on a machine with
> internet access.

---

## What this build does

| Capability | State |
|---|---|
| Docker Compose stack (Postgres+pgvector, Redis, API, worker, beat, web) | working (config-validated; not run in the build environment) |
| 42-table schema + Alembic migrations (full chain round-tripped on PostgreSQL 16) | working |
| Auth (Argon2id, JWT + rotating refresh, revocation) and RBAC | working, tested |
| User preferences | working, tested |
| Source registry, adapter plugin system, adapter catalogue | working, tested |
| **Polite HTTP layer**: robots.txt, per-source rate limits, per-run ceiling, SSRF guard, ETag/If-Modified-Since, typed errors | working, tested |
| **Nine adapters**: demo, CSV, GitHub, Hacker News, RSS/Atom, Wikipedia pageviews, FRED, SEC EDGAR, UN Comtrade | working against recorded fixtures |
| **Encrypted credential management** (never returned; masked hints only) | working, tested |
| **CSV upload** with header/encoding/size validation | working, tested |
| **Source health probe + run history** | working, tested |
| Ingestion: validate → normalise → hash-dedup → immutable raw store → entity resolution → signals + observations | working, tested, idempotent |
| Deterministic statistics (pct change, MA, z-score, EWMA, acceleration, changepoint) | working, tested |
| **Entity resolution on official identifiers only** (CIK, repo id, ticker+exchange, ISO country, contract+chain) — never on a similar name | working, tested |
| **Human review queue** for uncertain name matches, with permanent decisions | working, tested |
| **Deterministic topic clustering**; a model may name a cluster, never invent one | working, tested |
| **Gap-aware observations** (`missing` / `failed` are never stored as `0`) and preserved currency | working, tested |
| **Growth, momentum, acceleration, persistence, spike and seasonality detection** | working, tested |
| **One-day-spike rejection** distinguishing `9 → 300` from `700 → 1200` | working, tested |
| **Syndication detection** — five outlets on one wire story count as one source | working, tested |
| **Trend Score + separate Confidence Score, with the whole calculation shown** | working, tested |
| **Seven-stage ladder and six-state lifecycle** (same trend updated, not re-created) | working, tested |
| **Trends dashboard + trend detail page + entity review screen** | working |
| **Grounded plain-English trend narration** (dropped if it fails verification) | working, tested |
| Scoring engine + eligibility gate + golden vectors | working, tested |
| Prompt-injection sanitiser, hardened XML parsing, SSRF, rate limiting, audit log | working, tested |
| Report-grounding verifier (rejects fabricated citations) | working, tested |
| AI provider abstraction + budget ledger (`echo` by default: no keys needed) | working |
| Dashboard: overview, signals, signal detail with charts, source management, preferences | working |
| English + Arabic with full RTL, light + dark mode | working |
| **Live-data validation harness** (`scripts/live_validation.py`) | written and lint-clean; **not yet run against the live internet** |
| **Opportunity gate**: 3 signal types, 2 evidence classes, 2 independent source families, plus quality and confidence floors | working, tested |
| **Four analyzers**: business, import/distribution, public company, crypto — each recording UNKNOWN rather than guessing | working, tested |
| **Opportunity Score** (8 components, 20 penalties) with the whole calculation shown | working, tested |
| **Confidence and risk scored separately**; critical risks dominate rather than average | working, tested |
| **Skeptic agent** that can only *reduce* confidence | working, tested |
| **Crypto risk floor** — never low risk, at any evidence level | working, tested |
| **Confirmation and invalidation conditions** stored as checkable rows | working, tested |
| **Personal Relevance** from stored preferences, never hardcoded | working, tested |
| **Geographic Opportunity Gap** and **Adoption-to-Attention Ratio** (experimental, excluded from the score) | working, tested |
| **Human decision log** freezing the numbers as they stood | working, tested |
| **Evidence-backed report**, complete with no language model configured | working, tested |
| **Opportunities dashboard + detail page**, EN/AR + RTL + dark | working |
| **Watchlists, alerts, Telegram, backtesting** | **not implemented — Phases 5–6** |

## Quick start

```bash
cp environment.example .env

# Fill in at minimum:
python -c "import secrets; print(secrets.token_urlsafe(48))"                               # SECRET_KEY
python -c "from cryptography.fernet import Fernet; print(Fernet.generate_key().decode())"  # SECRET_ENCRYPTION_KEY
# POSTGRES_PASSWORD=<something>   (also update DATABASE_URL to match)
# ADMIN_PASSWORD=<at least 10 characters>

docker compose up --build
```

* Dashboard: <http://localhost:3000>
* API docs: <http://localhost:8000/docs>
* Sign in with `ADMIN_EMAIL` / `ADMIN_PASSWORD`

Migrations and the idempotent demo seed run automatically. The seed loads ~2,200
raw records across 15 signal series so the dashboard is useful immediately, and
registers all nine sources — **the offline ones enabled, every network source
disabled**. Nothing reaches the internet until you deliberately turn it on.

### Turning on a live source

1. Set `USER_AGENT` and `CONTACT_EMAIL` in `.env` to real values (SEC EDGAR
   requires them).
2. Open **Sources**, expand the card, add the credential if one is listed.
3. Press **Test connection** and read the result.
4. Press **Enable**, then **Collect now**, and check the run history:
   fetched / stored / duplicates / HTTP requests tells you if it is behaving.

Which adapters need what, and what each upstream allows, is in
`docs/data-source-guide.md` and on the Sources page itself.

## Repository layout

```
opportunity-intelligence/
├── backend/
│   ├── app/
│   │   ├── ai/           provider abstraction, budget ledger, grounding, prompts, report narration
│   │   ├── analytics/    statistics, growth, spikes, confirmation, dedup, trend + opportunity scoring,
│   │   │              analyzers, skeptic, risk engine, experimental measures
│   │   ├── api/v1/       auth, preferences, sources, signals, trends, opportunities, topics, review, health
│   │   ├── core/         config, security, sanitiser, SSRF, rate limit, middleware
│   │   ├── db/           declarative base, immutability guard, session
│   │   ├── models/       ORM models + enums (42 tables)
│   │   ├── schemas/      Pydantic request/response models
│   │   ├── services/     ingestion, entity resolution, topic clustering, trend engine, opportunity engine
│   │   ├── sources/      Fetcher, DataSource interface, registry, adapters/
│   │   └── workers/      Celery app, tasks, beat schedule
│   ├── alembic/          migrations
│   ├── scripts/          seed.py, live_validation.py, generate_scoring_vectors.py
│   └── tests/            442 tests + recorded HTTP fixtures
├── frontend/             Next.js 15 App Router, TS, Tailwind, EN/AR + RTL
├── docs/                 18 documents (see below)
├── docker-compose.yml
├── Makefile
└── environment.example
```

## Documentation

| Document | What it covers |
|---|---|
| `docs/product-requirements.md` | requirements review, **assumptions made**, deferrals |
| `docs/architecture.md` | components, data flow, trust boundaries, the collection policy layer, Mermaid diagrams |
| `docs/database-schema.md` | ER diagram, table-by-table notes, indexes, immutability |
| `docs/api-documentation.md` | endpoints, conventions, a worked "bring a source online" example |
| `docs/data-source-guide.md` | every adapter, **what has and has not been verified**, real API limits |
| `docs/signal-taxonomy.md` | signal classes, adoption vs attention, proxy handling |
| `docs/trend-methodology.md` | entity resolution, clustering, growth maths, spike vs acceleration, Trend Score, Confidence, stages, lifecycle |
| `docs/opportunity-methodology.md` | the gate, the four analyzers, Opportunity Score, the skeptic, risk, conditions, relevance, the experimental measures |
| `docs/live-validation.md` | **what was attempted, what was refused, and the exact command to close the gate** |
| `docs/scoring-methodology.md` | the opportunity formula, penalties, confidence, golden vectors |
| `docs/risk-methodology.md` | risk taxonomy, manipulation probability, crypto floor |
| `docs/ai-safety.md` | grounding rules, injection defence, model-use policy, cost ledger |
| `docs/security.md` | STRIDE threat model, what is implemented, **what is not** |
| `docs/testing.md` | strategy per layer, the no-network rule, how to run |
| `docs/deployment.md` | local and production, and what to do before enabling a live source |
| `docs/implementation-roadmap.md` | phase plan and exit criteria |
| `docs/contributing.md` | standards, setup, security reporting |
| `docs/changelog.md` | what shipped, what is deliberately missing, what was verified |

## Design commitments

1. **Nothing becomes an opportunity from one signal.** At least three distinct
   signal types from at least two independent sources, before a score exists.
   Sources sharing a `source_group` count once.
2. **Statistics are deterministic; language models only narrate.** No model
   produces a number, a threshold or a risk level.
3. **Every factual claim is bound to stored evidence.** A report citing an
   evidence id that does not exist is rejected, not repaired.
4. **External text is hostile.** Sanitised, envelope-wrapped, never in a system
   prompt; feeds parsed with a hardened XML reader; the report models have no tools.
5. **Collection is polite by construction.** Adapters cannot make an HTTP request
   except through the `Fetcher` that enforces robots, rate limits and SSRF rules.
6. **Failed predictions are kept.** `predictions` and `prediction_outcomes` are
   append-only, so the system can be graded rather than trusted.
7. **Crypto can never be low risk**, and meme assets are score-capped.
8. **Unknown is a valid answer.** Market size with no evidence scores zero rather
   than being estimated into existence; a missing FRED observation is skipped, not
   interpolated.

## Tests

```bash
cd backend && ENV=test python -m pytest -q
```

442 tests pass, lint is clean, 87% statement coverage. No test opens a network socket or calls a language
model — `conftest.py` blocks both, and every network adapter runs against recorded
fixtures that reproduce each API's awkward real cases.

## Legal and ethical

A research tool. It does not execute trades, connect to brokerages, or collect
private or unlawfully obtained data. It honours robots.txt for web and feed
sources, identifies itself with a contact address, respects per-source rate limits,
and stores only short excerpts of published articles alongside their links. It will
not implement an adapter for a source whose terms forbid programmatic access.

Opportunities may fail. Historical performance does not guarantee future results.
AI-generated analysis may contain errors. High-risk assets can result in total loss.
