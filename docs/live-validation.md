# Live Data Validation

## Status: NOT DONE. Here is exactly why, and exactly how to do it.

The Phase 4 brief opens with a gate: before building opportunity generation on
top of the pipeline, validate the Phase 2 collectors and the Phase 3 trend engine
against real internet data.

**That validation has not been performed.** The build environment sits behind an
egress allowlist that permits package registries and nothing else. Every attempt
was refused at the proxy, not by the code:

```
502  https://api.github.com/rate_limit          builtin injection failed (github)
403  http://hn.algolia.com/api/v1/search        Host not in allowlist: hn.algolia.com
000  https://wikimedia.org/api/rest_v1/...      no route
000  https://data.sec.gov/submissions/...       no route
000  https://api.stlouisfed.org/fred/series     no route
000  https://comtradeapi.un.org/public/v1/...   no route
200  https://pypi.org/simple/                   (package registry — allowed)
```

So:

* every Phase 4 opportunity carries `validation_status = DEMO`;
* the dashboard shows a **DEMO DATA** badge on every card, and the detail page
  carries a banner saying the evidence has not been validated against live
  sources;
* nothing in the documentation claims the adapters have been proven against the
  live APIs. They are proven against **recorded fixtures** of those APIs, which
  is a weaker and different statement.

## What to run on a machine with internet access

`backend/scripts/live_validation.py` performs the whole gate and prints a verdict
table. It is the only thing in the repository that deliberately touches the
outside world; the test suite blocks network access on purpose and always will.

### 1. Bring the stack up

```bash
cp environment.example .env
# Fill in SECRET_KEY, SECRET_ENCRYPTION_KEY, POSTGRES_PASSWORD, ADMIN_PASSWORD.
# Set USER_AGENT and CONTACT_EMAIL — the SEC rejects requests without a contact.
docker compose up --build -d
docker compose exec api python -m scripts.seed
```

### 2. See the plan without fetching anything

```bash
docker compose exec api python -m scripts.live_validation --dry-run
```

### 3. Add the two credentials that are genuinely required

| Source | Credential | Where to get it | Needed? |
|---|---|---|---|
| FRED | `api_key` | fred.stlouisfed.org — free, instant | **yes** |
| GitHub | `token` | a fine-grained PAT, public read only | strongly recommended (60 req/hour without one) |
| UN Comtrade | `subscription_key` | comtrade.un.org | optional; the public preview works but throttles hard |
| Hacker News, Wikipedia, SEC, RSS | — | none | no |

```bash
# find the source id
curl -s localhost:8000/api/v1/sources -H "Authorization: Bearer $TOKEN" | jq '.items[] | {id, slug}'

curl -s -X PUT localhost:8000/api/v1/sources/$FRED_ID/credentials \
  -H "Authorization: Bearer $TOKEN" -H 'content-type: application/json' \
  -d '{"key":"api_key","value":"YOUR_FRED_KEY"}'
```

### 4. Run the gate

```bash
# everything
docker compose exec api python -m scripts.live_validation

# or one source at a time, which is gentler on the upstream APIs
docker compose exec api python -m scripts.live_validation --only github
docker compose exec api python -m scripts.live_validation --only wikipedia sec
```

It exits non-zero if any check fails, so it can be wired into CI.

## What the harness actually checks

For each source, it runs the full path —

```
LIVE SOURCE → RAW RECORD → NORMALISED → ENTITY/TOPIC → OBSERVATION
            → TIME SERIES → TREND
```

— and then asserts the things that would actually break in production:

| Check | What a failure means |
|---|---|
| status reported | the run finished as succeeded / partial / failed, not silently |
| failure is legible | a failed source carries a usable error message |
| records fetched | the adapter still understands the API's current response shape |
| pagination | more than one page came back where the source paginates |
| http requests counted | the request counter is wired up |
| per-run ceiling respected | the ceiling engaged and was not exceeded |
| raw records stored | the immutable raw store received them |
| **no duplicates on re-run** | the same fetch twice stores zero new rows |
| entities resolve | one canonical record per thing, not several |
| timestamps sane | timezone-aware, ordered, not in the future, not implausibly old |
| **gaps stay missing** | a period the source did not publish is `NULL`, never `0` |
| zeros are real zeros | a measured zero is kept and is distinct from a gap |
| scores in range | the resulting trend numbers are 0–100, not absurd |
| calculation is shown | every trend carries its full component breakdown |

Each source is fetched **twice**: once for real, once to prove deduplication.

## What to do with the result

1. If everything passes, the collectors and the trend engine are validated
   against the live internet on that date. Record the date.
2. Re-run `POST /api/v1/opportunities/generate` so candidates are rebuilt from
   live observations, and change their `validation_status` to `live_validated`.
3. If something fails, **do not tune a threshold to make it pass.** The harness
   deliberately never adjusts anything. A boring live result is the result; a
   parsing failure is a bug in the adapter, and a rate-limit failure is a config
   problem.

## Honest expectations

Some checks will probably fail on the first run, and that is what the gate is
for:

* **Pagination on Wikipedia and FRED** is marked not-applicable, so those show
  `--` rather than a pass.
* **SEC EDGAR** returns 403 to any request without a descriptive `User-Agent`
  containing a contact address. If `CONTACT_EMAIL` is unset, expect a legible
  failure — which is itself a passing result for "failure is legible".
* **UN Comtrade** throttles the public preview endpoint aggressively; a `partial`
  run there is normal.
* **Real trend scores will be lower and duller than the demo ones.** The demo
  scenarios have clean, deliberately-shaped curves. Real series are noisy, and a
  live trend scoring 40 where a scenario scored 67 is the system working, not
  failing.
