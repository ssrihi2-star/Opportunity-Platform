# Scheduling: alerts and digests

This is the periodic half of the system — the part that decides *when* the
monitor runs, when alerts are dispatched and when digests are written. The
decisions themselves (who is told, how often, what counts as a change) live in
`app/services/monitoring.py` and `app/services/alerts.py` and are documented
where they are implemented. Nothing here changes them.

## The gap this closes

Before this, two things were true:

* `alerts.dispatch()` was reachable in production from exactly one place:
  `POST /api/v1/monitoring/run`, which requires an administrator and only
  happens when somebody presses the button.
* `alerts.run_digests()` had **no production caller at all**. A user could set
  `digest_frequency = daily`, and the only digest they would ever receive was
  one they generated themselves with `POST /api/v1/me/digests`.

A deployment that collected data every night and told nobody about it is the
failure mode this document exists to prevent.

## What is scheduled, and when

Scheduling is **off by default**. `SCHEDULER_ENABLED=false` produces exactly the
schedule this system has always had:

| Entry | Task | Cadence (default) |
|---|---|---|
| `collect-daily` | `ois.run_all_sources` | `COLLECT_HOUR:COLLECT_MINUTE` → 03:00 |
| `reap-stale-runs` | `ois.reap_stale_runs` | every `REAP_EVERY_MINUTES` → 15 minutes |

With `SCHEDULER_ENABLED=true`, `collect-daily` is **replaced** — not joined — by
one ordered run:

| Entry | Task | Cadence (default) |
|---|---|---|
| `nightly-pipeline` | `ois.run_nightly_pipeline` | `COLLECT_HOUR:COLLECT_MINUTE` → 03:00 |
| `reap-stale-runs` | `ois.reap_stale_runs` | every `REAP_EVERY_MINUTES` → 15 minutes |

It replaces the collection entry rather than being added next to it because two
entries that both collect would crawl every enabled source twice a night and
spend the rate limits the adapters are careful about. Collection still happens,
at the same hour, as the first phase of the run.

### Timezone semantics

`SCHEDULE_TIMEZONE` is an IANA name (`UTC` by default). It is passed to Celery as
`timezone`, with `enable_utc` left on, which means:

* **crontabs fire on the configured local wall clock.** `COLLECT_HOUR=3` with
  `SCHEDULE_TIMEZONE=Africa/Tripoli` means 03:00 in Tripoli, and it keeps meaning
  that across a DST change because the IANA database, not this code, owns the
  offset.
* **everything stored stays UTC.** `enable_utc` is never turned off, so
  `eta`/`countdown` arithmetic, `AlertDelivery.sent_at`, `Digest.period_*` and
  every `occurred_at` are UTC and comparable with each other.
* **the weekly digest day is judged locally too.** `DIGEST_WEEKLY_DAY` is a day
  *name* (`monday`…`sunday`), not a number: cron numbers days from Sunday and
  Python numbers them from Monday, and a configuration value readable both ways
  is a digest that arrives on the wrong day. 22:00 UTC on a Sunday is already
  Monday in Auckland, and the name is resolved in `SCHEDULE_TIMEZONE`.
* A timezone name the system cannot resolve, or a day name that is not a day,
  raises when beat and the worker start. It does not silently mean UTC.

### Ordering — why one task and not three cron times

Digests summarise what monitoring found; monitoring reads what collection
stored. Expressing that as "collection 03:00, monitoring 04:00, digests 05:00"
is a guess about how long the earlier jobs take, and on the night collection
runs long the later jobs quietly summarise yesterday's data with no error
anywhere. So `ois.run_nightly_pipeline` runs the phases itself, in order, and
each one runs only if the one it depends on completed:

```
collect (ois.run_all_sources' own coroutine)
   └─ phase failed (database down, soft time limit) → ABORT, nothing after it runs
   └─ individual sources failed → continue, and report the run as "degraded"
monitor + dispatch alerts (monitor_all → recent_events → dispatch → audit → commit)
   └─ failed, or skipped because another run holds the lock → digests do NOT run
digests (alerts.run_digests, one transaction per frequency)
   └─ failed → alerts already delivered stay delivered; run reported as "degraded"
```

Individual source failures do not abort the run: ingestion already isolates and
records them per source, and monitoring works on whatever measurements are
stored. A phase-level failure does abort, because there would be nothing new to
monitor and no honest digest to write.

Weekly digests are part of the same ordered run, on the local day named by
`DIGEST_WEEKLY_DAY`, rather than a second cron entry — for the same reason: a
weekly digest generated before that night's monitoring would summarise a week
that is missing its last day.

### Overruns and overlaps

* An overrun is bounded: the pipeline carries `soft_time_limit=6900` and
  `time_limit=7200`, larger than the app-wide 1800/1500 because it spans three
  phases. The soft limit raises inside the task, is caught at a transaction
  boundary, and is reported as a failed phase — so the run stops cleanly and the
  phases after it do not run.
* If a run is still going when beat fires the next one, the second run is
  refused by the advisory lock (below) and reports `skipped`. It does not queue
  up behind the first and then repeat its work.
* Beat must be a **singleton** (`docker compose` runs one `beat` service). Two
  beat processes means two of every entry; the lock protects dispatch and
  digests, but it does not protect two collectors from crawling the same source
  at the same moment.

## Concurrency guard

`app/workers/locks.py` takes a **PostgreSQL transaction-scoped advisory lock**
(`pg_try_advisory_xact_lock`) at the start of each guarded phase:

* `ois.monitoring` for the monitoring/dispatch transaction;
* `ois.digests:daily` and `ois.digests:weekly`, separately, so one period cannot
  block the other.

Why that mechanism:

* it is taken **inside the transaction that does the work**, so the commit or
  rollback that ends the phase releases it. There is nothing to clean up and no
  TTL to choose;
* a worker killed mid-phase loses its connection, PostgreSQL aborts the
  transaction, and the lock goes with it — a crash cannot lock the schedule out
  permanently;
* `pg_try_...` never blocks: the second run is told "no" immediately and skips,
  rather than waiting and then doing the same work anyway.

Session-scoped locks (`pg_try_advisory_lock`) are deliberately not used. With a
connection pool the session's connection returns to the pool at every commit, so
a lock taken before one commit is not held after it — and can leak to whoever
borrows that connection next. That is also why every guarded phase is exactly
one transaction, which is the transaction discipline the monitoring endpoint
already used.

Collection is **not** lock-guarded, and cannot be with this mechanism: it
commits per source, so no transaction-scoped lock survives it. What protects it
is that ingestion is idempotent — `raw_records` is unique on
`(source_id, content_hash)`, `signal_observations` on `(signal_id, observed_at)`
— so an overlapping collection duplicates HTTP traffic, not data.

**On SQLite the guard reports "acquired" and steps aside**, because the function
does not exist there. The default test suite therefore checks the wiring around
the guard with the guard patched; the real contention behaviour — two
independent transactions, one of them refused — is asserted in
`tests/test_worker_concurrency_postgres.py`, which is skipped unless
`TEST_POSTGRES_URL` points at a scratch PostgreSQL. SQLite proves nothing about
PostgreSQL concurrency and is not claimed to.

## Delivery reliability, stated honestly

### What the uniqueness constraint does and does not guarantee

`ux_alert_dedupe` on `(user_id, dedupe_key)` guarantees that the same fact never
produces a second **row** for the same person. It says nothing about Telegram or
SMTP, because `alerts.dispatch` sends *before* the transaction that records the
send commits:

```
INSERT AlertDelivery (status=pending)  ← savepoint, so a duplicate rolls back alone
provider.send(...)                     ← the message leaves the machine here
UPDATE status = sent | suppressed
...
COMMIT                                 ← only now is the send recorded
```

That ordering is the service's, not the scheduler's, and it is unchanged. Its
consequence is the **crash window**: if the worker dies between `provider.send()`
and `COMMIT`, the user has the message and the database does not know. The
transaction rolls back, so the next scheduled run sees the same event as
undelivered and sends it again. The window is one batch — one scheduled run —
wide, and it cannot be closed from the worker side. Closing it needs the
delivery row committed *before* the send, which is a change inside
`app/services/alerts.py` (see "Limitations").

So the honest guarantee is:

* **committed runs:** a fact reaches a person at most once. Proven by the
  constraint, and by tests that run the same facts through the scheduled path
  three times and count one delivery.
* **crashed or killed runs:** at-least-once. Duplicates are possible for the
  batch in flight; nothing is silently dropped.
* Zero duplicates *and* zero lost deliveries is not offered, because a
  send-before-commit design cannot offer both.

### What happens when workers race

Two pipeline runs at once: the second finds `ois.monitoring` held, reports
`skipped`, and — because digests depend on monitoring — stops there. No provider
is called twice, no digest is written twice. This is the case the advisory lock
exists for; the alternative is two workers each sending the same alert before
either commits the row that would have deduplicated it.

### What happens after a partial failure or a restart

* **Monitoring/dispatch failed** → the whole phase rolls back, the run reports
  `failed`, nothing after it runs. The task is **not** retried: re-running a
  batch that had already handed messages to providers before it failed would
  send those again. Recovery is the next scheduled run, which re-presents the
  events in its lookback window (`MONITOR_LOOKBACK_HOURS`, default 26 — wider
  than the daily gap on purpose) and is deduplicated by the constraint for
  anything already delivered.
* **Worker killed (SIGKILL, hard time limit, OOM)** → the message was already
  acked (`acks_late=False` on both notification tasks), so it is *not*
  redelivered; that is deliberate, because redelivery is exactly the repeated
  send above. PostgreSQL rolls back the open transaction. The next scheduled run
  recovers the events; anything already sent inside the crash window may be sent
  once more.
* **Digests failed** → that frequency's transaction rolls back and *is* retried,
  with a bounded exponential backoff (120s, 240s, then the task fails and says
  so). Retrying is safe here because a digest is a stored row, not an external
  send — and the retry carries **only the frequencies that rolled back**, never
  one that committed, because `digests` has no uniqueness constraint and a
  second row for one period is the user reading the same summary twice.
* **Suppressed deliveries are not retried.** A suppression is a decision, not a
  failure: already delivered, inside a cooldown window, below a relevance floor,
  off the watchlist, no provider registered, or a provider that reported it
  could not deliver (unconfigured Telegram/SMTP). The reason is stored on the
  row. A provider failure is *not* retried either — `dispatch` has no
  per-delivery retry queue, and adding one from the worker would mean
  re-implementing the service. The in-app row is still written, so the alert is
  readable in the product even when every external channel failed.

### One user's failure and the next user's

Delivery failures are isolated today, and the scheduler relies on that: a
provider reports a refused send as data (`ProviderResult(delivered=False)`)
instead of raising, so `dispatch` records the suppression and carries on to the
next rule and the next person. Both Telegram and SMTP catch their own exceptions
and return `delivered=False`.

What is **not** isolated is an exception raised inside `dispatch` itself — a
database error, or a failure in relevance computation for one user. That
propagates out of the single `dispatch()` call, rolls back the phase, and costs
every other user in the batch their alert for that run (they are recovered by
the next run, since nothing was committed). Isolating it needs a change inside
`app/services/alerts.py`, which the scope of this work excludes; see
"Limitations" for the smallest such change.

### Logging

Phases log counts and error types: `scheduled_monitoring_finished`
(opportunities, checks, events, sent, suppressed), `scheduled_digests_written`
(frequency, digest count), `pipeline_finished` (status). The audit row written
per scheduled monitoring run (`action = monitoring.run.scheduled`,
`actor_label = system`, no actor user) stores the same counts. No log line or
audit row carries a notification body, a Telegram chat id, a `/link` code or a
credential.

## Shutdown and recovery

Celery's supported behaviour, which is what the `worker` service uses:

* **SIGTERM (warm shutdown)** — the worker stops consuming, lets the running
  task finish, then exits. A phase that finishes commits normally. This is the
  shutdown to use for deploys; `docker compose stop` sends SIGTERM and waits
  before escalating.
* **A second SIGTERM / SIGKILL (cold shutdown)** — the child process is
  terminated mid-task. The open transaction is rolled back by PostgreSQL, so the
  database is never left half-written. The limitation, stated plainly: any
  message already handed to Telegram or SMTP in that batch is not recorded and
  will be sent again by the next run. There is no way to recover the fact that
  it was sent, because the only record of it was in the transaction that died.
* **Soft time limit** — raises inside the task, is caught by the phase, rolls
  back, and is reported as a failed phase rather than a silent one.

## Operator setup

1. Configure, in `.env` (see `environment.example`):

   ```dotenv
   SCHEDULER_ENABLED=true
   SCHEDULE_TIMEZONE=Africa/Tripoli   # any IANA name
   COLLECT_HOUR=3                     # local hour of the ordered run
   COLLECT_MINUTE=0
   REAP_EVERY_MINUTES=15
   MONITOR_LOOKBACK_HOURS=26          # ≥ the gap between two runs
   MONITOR_EVENT_LIMIT=500
   DIGEST_WEEKLY_DAY=sunday           # monday..sunday, local
   ```

   Notification credentials are separate and unchanged: `TELEGRAM_BOT_TOKEN`,
   `SMTP_HOST`/`SMTP_FROM`. A blank one means that channel is off, deliveries on
   it are recorded as suppressed with the reason, and the in-app row is still
   written. Enabling the schedule does not enable a provider.

2. Run the processes. Both already exist in `docker-compose.yml`; nothing new is
   added:

   ```bash
   docker compose up -d worker beat      # exactly one beat
   ```

   The worker must consume the queues the tasks are routed to —
   `ingest` (collection, the ordered run) and `analyze` (the two phases when
   dispatched on their own). The compose `worker` command already listens on
   `ingest,analyze,ai`.

3. Confirm it is running:

   ```bash
   docker compose logs beat | grep -E "nightly-pipeline|Scheduler"
   docker compose logs worker | grep -E "pipeline_finished|scheduled_monitoring_finished|scheduled_digests_written"
   ```

   and in the database, after a run:

   ```sql
   SELECT action, actor_label, after, created_at
     FROM system_audit_logs
    WHERE action = 'monitoring.run.scheduled'
    ORDER BY created_at DESC LIMIT 5;

   SELECT frequency, count(*), max(generated_at) FROM digests GROUP BY frequency;
   SELECT channel, status, count(*) FROM alert_deliveries GROUP BY channel, status;
   ```

   The audit row's `after` carries `opportunities`, `checks`, `events`,
   `events_considered`, `alerts_sent` and `alerts_suppressed` — enough to tell a
   quiet night from a broken one. A digest count of zero for a frequency that
   has subscribers means the digest phase did not run or failed; `digests` are
   never silently skipped for a user who asked for one.

4. To run a phase by hand, without waiting for the schedule:

   ```bash
   docker compose exec worker celery -A app.workers.celery_app.celery_app call ois.run_monitoring
   docker compose exec worker celery -A app.workers.celery_app.celery_app call ois.run_digests --args='[["daily"]]'
   ```

   Both take the same advisory locks, so a manual run cannot collide with a
   scheduled one; whichever arrives second reports `skipped`.

## Tests

| File | What it proves | How |
|---|---|---|
| `tests/test_worker_scheduling.py` | schedule contents and defaults, timezone and day-name validation, phase ordering, deduplication across repeated runs, digest eligibility, unverified/unconfigured channels, per-user delivery-failure isolation, retry bounds and backoff, that importing starts no scheduler | SQLite, providers faked or left unconfigured, clock passed explicitly, broker never contacted (Celery eager mode for the retry paths) |
| `tests/test_worker_concurrency_postgres.py` | the advisory lock really refuses a second transaction, really dies with a rollback, and really stops an overlapping run from dispatching | PostgreSQL, `poolclass=None` so each session is its own connection, gated on `TEST_POSTGRES_URL` |

The mocked concurrency checks in the first file are labelled as such where they
appear. Neither file may open a socket: `conftest.py` blocks it, so no test can
reach Telegram or SMTP.

## Limitations

1. **The crash window is real and is not closed.** Send-before-commit means a
   killed worker can cause one duplicate external delivery for the batch in
   flight. Closing it requires committing the `pending` delivery row before
   `provider.send()` and updating its status afterwards — a change inside
   `app/services/alerts.py::_deliver`, outside this scope.
2. **Per-user failure isolation inside `dispatch` is not achievable from the
   worker.** `dispatch()` loops over every enabled rule in one call and one
   transaction, and offers no per-user error boundary and no way to partition
   the rule set. An unexpected exception for one user therefore costs the batch.
   The smallest change that would fix it is a `try/except` around the per-rule
   body of the loop in `app/services/alerts.py` (roughly the `for rule in rules:`
   block) that logs, counts the rule as failed and continues, plus the same
   around the per-user loop in `run_digests`. Until then the recovery is the next
   scheduled run.
3. **Suppressed and failed external deliveries are never retried.** There is no
   delivery queue to retry from; a suppression row is terminal. Users still see
   the alert in-app.
4. **Collection has no cross-process lock** (it commits per source), so two
   overlapping collections duplicate HTTP requests but not data.
5. **`MONITOR_EVENT_LIMIT` bounds a run.** If more events than the limit occur
   inside the lookback window, the oldest are not considered by that run and are
   picked up by the next one only while they remain inside the window. Raise the
   limit rather than widening the lookback if a deployment is event-heavy.
