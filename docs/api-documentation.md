# API Documentation

Base path `/api/v1`. Interactive docs at `/docs` and `/redoc`; machine-readable
schema at `/api/v1/openapi.json`.

Auth: `POST /auth/login` returns a 15-minute access token and sets an httpOnly
refresh cookie. `POST /auth/refresh` rotates it. `POST /auth/logout` denylists the
access token's `jti`.

## Implemented

| Method | Path | Role | Notes |
|---|---|---|---|
| POST | `/auth/login` | public | rate-limited per IP |
| POST | `/auth/refresh` | cookie | rotating refresh |
| POST | `/auth/logout` | any | denylists the jti |
| GET | `/auth/me` | any | current user |
| GET | `/preferences` | any | creates defaults on first read |
| PATCH | `/preferences` | any | partial update, validates the capital range |
| GET | `/sources` | any | list |
| GET | `/sources/adapters` | any | catalogue: what each adapter needs and its documented upstream limit |
| POST | `/sources` | admin | register a source |
| PATCH | `/sources/{id}` | admin | enable/disable, schedule, limits, config |
| GET | `/sources/{id}/credentials` | admin | keys and masked hints only — never a value |
| PUT | `/sources/{id}/credentials` | admin | create or rotate; stores Fernet-encrypted |
| DELETE | `/sources/{id}/credentials/{key}` | admin | remove |
| POST | `/sources/{id}/upload-csv` | admin | multipart; validates the header before storing |
| POST | `/sources/{id}/run` | admin | collect now (sync in dev, Celery in prod) |
| GET | `/sources/{id}/health` | any | freshness, missing credentials, documented limit; `?probe=true` also calls the adapter's live health check |
| GET | `/sources/{id}/runs` | any | run history with counts and errors |
| GET | `/raw-records` | any | paginated, filterable by source |
| GET | `/signals` | any | filter by entity, type, class, geo, source |
| GET | `/signals/{id}` | any | definition + observation series + deterministic statistics |
| GET | `/entities` | any | search with alias resolution |
| GET | `/trends` | any | filter by category, stage, state, geo, subject kind, min score, min confidence; `include_spikes` / `include_seasonal`; sort by score, confidence, newest, updated, acceleration |
| GET | `/trends/{id}` | any | full breakdown: components, penalties, metrics, supporting signals, series, snapshot history, evidence timeline, related entities |
| POST | `/trends/evaluate` | admin | re-cluster topics and re-evaluate every trend; updates existing rows, never duplicates them |
| POST | `/trends/{id}/explain` | any | plain-English narration of the stored numbers; returns `null` if the grounding verifier rejects the draft |
| GET | `/topics` | any | deterministic clusters with keywords and member entities |
| GET | `/entity-review` | any | pending or decided name-match candidates |
| POST | `/entity-review/{id}` | analyst | record `confirm` / `reject` / `keep_separate` with an optional note; `409` if already decided |
| GET | `/opportunities` | any | filter by type, country, industry, state, risk, validation status, min score/confidence/relevance; sort by score, confidence, relevance, lowest risk, fastest-rising trend, newest |
| GET | `/opportunities/{id}` | any | full case: components, penalties, confidence and relevance breakdowns, evidence, risks, conditions, participation paths, skeptic review, decision log, score history |
| GET | `/opportunities/{id}/report` | any | the evidence-backed research note; `?narrate_sections=true` asks a model to phrase three sections, and drops the draft if it fails the grounding check |
| POST | `/opportunities/generate` | admin | run the pipeline over qualifying trends; returns created/updated/refused **with the reason for every refusal** |
| GET | `/opportunities/{id}/decisions` | any | the human decision log |
| POST | `/opportunities/{id}/decisions` | analyst | record INTERESTED / NOT_INTERESTED / RESEARCHING / REJECTED / ACTED_ON / WATCHING with a note; append-only, and freezes the score, confidence, risk and relevance as they stood |
| GET | `/overview` | any | dashboard aggregate |
| GET | `/health`, `/health/ready` | public | liveness / readiness |
| GET | `/metrics` | admin | Prometheus text format |

## Not yet implemented

`/reports*`, `/watchlists*`, `/alerts*`, `/backtesting`, `/analytics`. These
routes do not exist — there are no `501` placeholders pretending otherwise.

`POST /opportunities/generate` returning `created: 0` is a normal, successful
response, not an error: on most days nothing new clears the evidence bar.

## Conventions

* Pagination: `?limit=` (max 200) `&offset=`; responses carry `total`.
* Errors: `{ "detail": ..., "code": ..., "request_id": ... }`. Codes include
  `immutable_row`, `ssrf_blocked`, `robots_disallowed`, `source_misconfigured`,
  `source_rate_limited`, `ai_budget_exceeded`, `ungrounded_report`.
* Every response carries `X-Request-ID`, echoed into structured logs.
* Timestamps are ISO-8601 UTC.
* All list endpoints are read-only and safe to poll.

## Worked example: bringing a real source online

```bash
TOKEN=$(curl -s -X POST localhost:8000/api/v1/auth/login \
  -H 'content-type: application/json' \
  -d '{"email":"admin@example.com","password":"..."}' | jq -r .access_token)

# 1. see what the adapter needs
curl -s localhost:8000/api/v1/sources/adapters -H "Authorization: Bearer $TOKEN" | jq '.[] | select(.adapter_key=="fred")'

# 2. store the credential (encrypted; only a hint is ever returned)
curl -s -X PUT localhost:8000/api/v1/sources/$ID/credentials \
  -H "Authorization: Bearer $TOKEN" -H 'content-type: application/json' \
  -d '{"key":"api_key","value":"YOUR_FRED_KEY"}'

# 3. check the connection before trusting a schedule with it
curl -s "localhost:8000/api/v1/sources/$ID/health?probe=true" -H "Authorization: Bearer $TOKEN"

# 4. enable and collect
curl -s -X PATCH localhost:8000/api/v1/sources/$ID -H "Authorization: Bearer $TOKEN" \
  -H 'content-type: application/json' -d '{"enabled":true}'
curl -s -X POST localhost:8000/api/v1/sources/$ID/run -H "Authorization: Bearer $TOKEN"
```
