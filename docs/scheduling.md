# Scheduling: alerts and digests

This is the periodic half of the system — the part that decides *when* the
monitor runs, when alerts are dispatched and when digests are written. The
decisions themselves (who is told, how often, what counts as a change) live in
`app/services/monitoring.py` and `app/services/alerts.py` and are documented
where they are implemented. Nothing here changes scoring, event identity, alert
cooldowns or any API contract.

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

**What is still missing, stated up front:** a digest is a **row in the
database** that the user reads inside the application. Nothing in this system
sends a digest to Telegram or to email. Daily Telegram digest delivery is **not
implemented** and is a separate piece of work (see "Follow-ups"). Enabling the
schedule gives users a nightly/weekly summary they can open in the product; it
does not push it to them.

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

`ois.run_digests` is a registered task but is **not** a beat entry. It exists so
the pipeline can delegate a bounded retry of the digest phase without re-running
collection or re-sending an alert, and so an operator can run one period by hand.

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
* **digest periods are judged on the same local wall clock** (next section).
* A timezone name the system cannot resolve, or a day name that is not a day,
  raises when beat and the worker start. It does not silently mean UTC.

### Digest periods: what a digest covers

A digest covers a **canonical period**, and the period it covers is decided by
`alerts.digest_period()`, not by the instant the task happened to run:

| Frequency | Period covered | `period_key` |
|---|---|---|
| `daily` | the local calendar day that closed before the run, `[D-1 00:00, D 00:00)` | `daily:2026-09-06` |
| `weekly` | the ISO week that closed before the run, Monday 00:00 → the next Monday 00:00 | `weekly:2026-W36` |

Both are the most recently **completed** period, which matters twice over:

* A closed period has the same boundaries for every attempt that writes it, so
  the key and the content window agree across retries. A period still in
  progress would be accumulating while it was summarised, and two attempts would
  legitimately disagree about its contents.
* It is what a reader means by "yesterday's digest": a run at 03:00 on Tuesday
  summarises all of Monday, not the three hours since midnight.

The **weekly key names a week, not a run day.** Every run inside the week that
follows a closed one — Monday's scheduled attempt, a retry on Tuesday, a
catch-up run on Friday — resolves to the same key and the same boundaries, so
they collide on the unique index instead of writing three weekly digests. A run
*before* that Monday names the week before it, which is why `DIGEST_WEEKLY_DAY`
defaults to **`monday`**: a Monday run summarises the week that closed hours
earlier, while a Sunday run summarises the week that closed six days earlier.
Changing the day is still exactly one weekly digest per week; it only changes
how fresh the week is.

`DIGEST_WEEKLY_DAY` is a day *name* (`monday`…`sunday`), not a number: cron
numbers days from Sunday and Python numbers them from Monday, and a value
readable both ways is a digest that arrives on the wrong day. 22:00 UTC on a
Sunday is already Monday in Auckland, and the name is resolved in
`SCHEDULE_TIMEZONE`.

Local midnights are constructed as wall-clock times in `SCHEDULE_TIMEZONE`, so a
DST change moves the instant a period starts without moving the day it covers: a
23-hour day is still one calendar day, and a 167-hour week is still one calendar
week. A local midnight that does not exist resolves the way `zoneinfo` resolves
it, and the period is still exactly one day or one week.

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
digests (alerts.run_digests, one transaction per period)
   └─ a period failed → alerts already delivered stay delivered; the failed
      periods are handed to ois.run_digests for a bounded retry, and the run is
      reported as "degraded"
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
  phases. The soft limit raises inside the task and is **never** treated as a
  unit failure (see "What is not a unit failure"), so the run stops instead of
  skipping one source and carrying on past its limit.
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
* `ois.digests:<period key>` — e.g. `ois.digests:daily:2026-09-06` — one per
  period, so a retry of an old period cannot block the night's current one and
  two runs of the same period cannot both write it.

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
independent transactions, one of them refused, and three runs racing for one
digest period — is asserted in `tests/test_worker_concurrency_postgres.py`,
which is skipped unless `TEST_POSTGRES_URL` points at a scratch PostgreSQL.
SQLite proves nothing about PostgreSQL concurrency and is not claimed to.

The lock is a guard against overlap, **not** the digest deduplication mechanism.
It is advisory and best-effort; the guarantee that a period is written once per
user comes from the database (next section).

## Digest identity: why a period can be written once

`digests` had no uniqueness at all, so the period key is what makes repetition
safe. Migration `0007_digest_period_identity` adds:

* `digests.period_key` — nullable `VARCHAR(40)`, the canonical key above;
* `ux_digest_period` — a **unique index** on `(user_id, frequency, period_key)`.
  A unique index rather than a table constraint because it is identical in
  effect on PostgreSQL and is the only form SQLite can add to an existing table,
  so one migration behaves the same everywhere.

Consequences, all of them deliberate:

* **Repeated, retried, redelivered or overlapping runs write the period once.**
  The second attempt gets an `IntegrityError`, which `run_digests` counts as
  `duplicates` rather than raising. The count is reported per period
  (`written` / `duplicates` / `users_failed`), so an operator can see a retry
  that found the work already done.
* **Historical rows are untouched.** The column is added nullable and is **not**
  backfilled. Rows written before it existed were built over a rolling window
  whose boundaries are real instants, not canonical local days, so any key
  invented for them would be a guess — and two digests generated on the same day
  would then collide, which could only be resolved by deleting one of a user's
  existing rows.
* **On-demand digests are unchanged.** `POST /api/v1/me/digests` still builds a
  rolling window ending now, still stores `period_key = NULL`, and is still
  repeatable: NULLs are distinct in a unique index on both PostgreSQL and
  SQLite. Giving that endpoint a canonical key would have turned a second click
  into a database error, which is an API behaviour change this work did not
  make. The unavoidable change is additive only — the column exists, and the
  response model is unchanged.
* **Only rows written by the schedule carry a key**, so only those are
  deduplicated.

### The retry: what it carries, and how often it happens

The digest phase is the only phase that retries, because a digest is a stored
row and not an external send. The retry is deliberately narrow:

* **It carries the original period identity.** `run_digest_phase` reports the
  periods that failed as `{frequency, key, start, end, timezone}`, and every
  retry is given exactly those dicts. A retry that crossed midnight, crossed a
  week boundary, or landed after an operator changed `SCHEDULE_TIMEZONE` still
  writes the period it was scheduled for, over the same content window — the
  window is never recomputed from the retry's own clock.
* **It never re-runs collection or alert dispatch.** The pipeline delegates to
  `ois.run_digests` with `kwargs={"periods": [...]}`; it does not retry itself.
  Retrying the pipeline would re-crawl every source and, worse, re-run the phase
  that sends messages.
* **Total attempts: 3 per period** — the scheduled attempt plus
  `DIGEST_MAX_RETRIES = 2` retries, 120s and 240s later
  (`DIGEST_RETRY_BASE_SECONDS`, doubling, capped at `DIGEST_RETRY_MAX_SECONDS`).
  After that the task raises `DigestPhaseError` naming the unresolved period keys
  and stays failed. Nothing retries indefinitely, and a permanent error — an
  unsupported frequency — raises before any work starts and is not retried at
  all.
* **Completed users are skipped by the database, not by memory.** The retry
  re-attempts the whole period, because the worker running it may be a different
  process with no idea who succeeded. Users already written come back as counted
  duplicates through `ux_digest_period`.
* **Publishing the retry can fail, and that is reported.** Neither `apply_async`
  nor Celery's own `self.retry` removes broker-publish failure: if the broker is
  down, the retry is dropped. When that happens the run's result carries
  `{"published": false, "reason_code": "broker_publish_failed", "error_type": …}`,
  the run is reported `degraded`, and `digest_retry_publish_failed` is logged.
  The recovery is the next scheduled run, which writes whatever period is then
  due — the dropped period is *not* silently made up later.

## Delivery reliability, stated honestly

> **Best-effort delivery. Duplicate database records are constrained for the
> same dedupe identity, but external delivery can be lost or repeated after
> failures. Equivalent regenerated events may have different identities.**

That is the whole guarantee, and it is worth being precise about why nothing
stronger is offered — because "at-least-once" and "at-most-once" are both easy to
claim here and both false.

**Why duplicates happen.** `alerts.dispatch` sends *before* the transaction that
records the send commits:

```
INSERT AlertDelivery (status=pending)  ← savepoint, so a duplicate rolls back alone
provider.send(...)                     ← the message leaves the machine here
UPDATE status = sent | suppressed
...
COMMIT                                 ← only now is the send recorded
```

If the worker dies between `provider.send()` and `COMMIT`, the user has the
message and the database does not know. The transaction rolls back, the next
scheduled run sees the same event as undelivered, and sends it again. A savepoint
cannot help: it contains database work, and the send is not database work. This
ordering is the service's, not the scheduler's, and it is unchanged.

A second, quieter source of duplicates is event identity. `dedupe_key` embeds
`event.id`, and events are *derived* from stored measurements. A run that rolled
back can re-derive an equivalent event on the next run, and the re-derived event
has a **new id**, so the dedupe constraint does not recognise it as the same
fact. Committed re-runs are safe — `detect_changes` is transition-based, so a
fact already recorded is not recorded again — but a score-delta event can be
re-derived by a later run, and the only thing limiting repeats is the rule's
cooldown. A rule with `cooldown_hours = 0` can therefore alert on equivalent
facts on consecutive nights. Event identity is recorded as a follow-up; it was
explicitly out of scope here.

**Why losses happen.** Both notification tasks run with `acks_late=False`, so a
message is acked as soon as the worker takes it. A worker killed after the ack
and before the commit loses that run's work, and Celery will not redeliver it.
That is deliberate — redelivery is exactly the repeated send above — and the
recovery is the lookback window, not the broker: the next run re-presents events
within `MONITOR_LOOKBACK_HOURS` (default 26, wider than the daily gap on purpose)
and the dedupe constraint stops anything already delivered. The lookback is a
*second chance*, not a queue. Anything older than the window is never
re-presented, and a rule whose cooldown or relevance floor suppressed an alert
is not retried at all — a suppression is a decision, and the row records the
reason.

So: early acknowledgement plus a lookback window is **neither** at-least-once
**nor** at-most-once per trigger. Both loss and duplication are possible, and
what the database constrains is *rows* for one dedupe identity — not messages.

### What happens when workers race

Two pipeline runs at once: the second finds `ois.monitoring` held, reports
`skipped`, and — because digests depend on monitoring — stops there. No provider
is called twice, no digest is written twice. Two runs of one *digest period*
that somehow both get past the lock (a manual run against a different database
role, a lock that was not taken because the deployment is on SQLite) are still
stopped by `ux_digest_period`: one writes, the other counts a duplicate.

### What happens after a partial failure or a restart

* **Monitoring/dispatch failed** → the whole phase rolls back, the run reports
  `failed`, nothing after it runs. The task is **not** retried: re-running a
  batch that had already handed messages to providers before it failed would
  send those again. Recovery is the next scheduled run's lookback window.
* **Worker killed (SIGKILL, hard time limit, OOM)** → the message was already
  acked, so it is not redelivered; PostgreSQL rolls back the open transaction.
  The next scheduled run recovers the events inside the lookback; anything
  already sent inside the crash window may be sent once more.
* **Digests failed** → that period's transaction rolls back and the period is
  retried as described above, carrying its identity. A period that committed is
  never rewritten, and a period whose users partly succeeded is retried without
  duplicating the successful ones.
* **Suppressed deliveries are not retried.** Already delivered, inside a cooldown
  window, below a relevance floor, off the watchlist, no provider registered, or
  a provider that reported it could not deliver (unconfigured Telegram/SMTP):
  the reason is stored on the row. `dispatch` has no per-delivery retry queue,
  and adding one from the worker would mean re-implementing the service. The
  in-app row is still written, so the alert is readable in the product even when
  every external channel failed.

## Failure isolation: whose failure is whose

A batch has a property an interactive request does not: when one unit blows up,
something has to decide what happens to the other ninety-nine. Three units are
isolated, each inside its own `SAVEPOINT`:

| Unit | Failure means | Counted as |
|---|---|---|
| one user's relevance preparation | that user's rules are **not evaluated at all** | `failed` (+ one suppression reason per rule) |
| one alert rule | that rule is skipped for this run; the rest continue | `failed` |
| one user's digest | that user gets no digest for the period; the rest do | `users_failed` |

A relevance failure skips the user's dispatch **entirely** rather than carrying
on with a relevance that is missing. That distinction is the whole point: a rule
with a `min_user_relevance` floor evaluated against an absent relevance would
read the floor as "unknown", and could deliver exactly what the floor was set to
prevent. Skipping is counted and the reason says the rule "was not evaluated",
so a silently unsent alert is still visible.

Counters describe **successful and failed units only**. A digest is counted as
written after its savepoint is released, never before; a rule that raised is
never counted as sent; a duplicate is counted as a duplicate and not as work
done. `AlertOutcome` therefore carries `sent`, `suppressed` (decisions) and
`failed` (contained failures) as three separate numbers, and the audit row for a
scheduled monitoring run stores all three.

### What is not a unit failure

Two classes of error are re-raised out of every isolation point, because they
mean "stop the run" rather than "this unit failed":

* **the transaction or connection is no longer usable** — `PendingRollbackError`,
  `InterfaceError`, `OperationalError`, any `DBAPIError` with
  `connection_invalidated`. Continuing would issue statements on a connection
  that is gone, and on PostgreSQL a failed statement aborts the whole
  transaction until it is rolled back.
* **the supervisor said stop** — Celery's `SoftTimeLimitExceeded` /
  `TimeLimitExceeded` / shutdown signals, plus `KeyboardInterrupt` and
  `SystemExit`. Containing one of these would keep a worker running past the
  limit its operator set, and Celery's hard kill would then land mid-transaction.

`app/core/errors.py::is_unrecoverable` recognises the Celery signals **by class
name**, not by import, so a module the API imports never pulls a worker library
into the request path.

### The limits of a savepoint, stated plainly

* **It cannot un-send anything.** A rule that fails *after* `provider.send()`
  leaves the user with a message and the database with no record of it — and
  because there is no record, the next run sends again. This is measured, not
  asserted, in `tests/test_alert_isolation.py`.
* **It contains database work only.** Files written, HTTP calls made and metrics
  emitted inside a unit are not rolled back.
* **On SQLite it is weaker than on PostgreSQL.** The pysqlite driver executes
  `SAVEPOINT` outside an explicit transaction, so a savepoint that was *released*
  and whose outer transaction is later rolled back can leave its rows behind. A
  savepoint that *fails* is still discarded correctly there, and that is the case
  the isolation above depends on. Production runs PostgreSQL, where an outer
  rollback discards everything the phase wrote; both properties are asserted in
  `tests/test_worker_concurrency_postgres.py`, and the SQLite limitation is
  pinned in `tests/test_digest_periods.py` rather than papered over.

## Logging and stored failure text

Failure logging uses **fixed outcome codes plus an exception class name**, and
nothing else. No log line, audit row or stored failure detail carries exception
text — not truncated, not from a whitelist of "safe" exception types.

The reason is what these exceptions actually contain:

* an httpx exception carries the request URL, and the Telegram send URL contains
  the **bot token**;
* an smtplib exception carries the server's reply and the recipient address;
* a SQLAlchemy `DBAPIError` renders its statement **and its bound parameters**,
  which for this schema means chat ids and alert bodies.

`app/core/errors.py::failure_code(exc)` returns `type(exc).__name__` and is the
only thing derived from an exception anywhere in these paths. Where a caller
needs to say more, it adds a code it chose itself.

The events, all of them carrying `outcome=` and, on failure, `error_type=`:

| Event | Where | Outcome codes |
|---|---|---|
| `scheduled_monitoring_finished` / `scheduled_monitoring_failed` | monitoring phase | `phase_failed` |
| `alerts.dispatched` | dispatch summary | — (counts only) |
| `alerts.relevance_failed` | one user's relevance | `user_skipped` |
| `alerts.rule_failed` | one rule | `rule_skipped` |
| `scheduled_digests_written` / `scheduled_digests_failed` | one period | `period_failed` |
| `digests.generated` / `digest.user_failed` | one user's digest | `user_skipped` |
| `scheduled_digests_retrying` | bounded retry | `retry_scheduled` (period keys + attempt + countdown) |
| `digest_retry_queued` / `digest_retry_publish_failed` | delegation | `retry_not_queued` |
| `source_failed` / `task_run_source_failed` | ingestion worker | `source_skipped` |
| `notification.telegram_failed` / `notification.email_failed` | providers | `delivery_failed` |
| `pipeline_stopped_before_digests` / `pipeline_finished` | the ordered run | — |

**Persisted text gets the same treatment**, because it is read back through the
API. `ProviderResult.detail` for a failed Telegram or SMTP send is now
`"Telegram delivery failed (ConnectError). The alert is still readable in the
application."` — a class name, not the exception — and that string is what lands
in `alert_deliveries.suppressed_reason`, a column users and administrators can
query. The same applies to `AlertOutcome.reasons`, which the monitoring endpoint
returns, and to the ingestion task's result dict, which the result backend
persists (`error` is now a class name).

Two things this did **not** change, recorded so nobody assumes they were:
`SourceRun.error` — the durable, user-visible ingestion history written by the
ingestion service — still stores what it stored before; and the suppression
reasons for *decisions* (cooldown, relevance floor, unconfigured provider) are
unchanged prose, because they never contained exception text.

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
  will be sent again by the next run. There is no way to recover the fact that it
  was sent, because the only record of it was in the transaction that died.
* **Soft time limit** — raises inside the task, is re-raised out of every
  isolation point, and stops the run rather than being counted as one more
  skipped unit.

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
   DIGEST_WEEKLY_DAY=monday           # monday..sunday, local; monday = the week
                                      # that closed hours ago
   ```

   Notification credentials are separate and unchanged: `TELEGRAM_BOT_TOKEN`,
   `SMTP_HOST`/`SMTP_FROM`. A blank one means that channel is off, deliveries on
   it are recorded as suppressed with the reason, and the in-app row is still
   written. Enabling the schedule does not enable a provider, and no provider is
   involved in digests at all.

2. Run the migration and the processes. Both services already exist in
   `docker-compose.yml`; nothing new is added:

   ```bash
   alembic upgrade head               # 0007 adds digests.period_key + ux_digest_period
   docker compose up -d worker beat   # exactly one beat
   ```

   `0007` is additive and safe to re-run: it guards on the table, the column and
   the index existing, adds a nullable column, creates the unique index, and
   touches no rows.

   The worker must consume the queues the tasks are routed to —
   `ingest` (collection, the ordered run) and `analyze` (the two phases when
   dispatched on their own, **including the digest retry the pipeline
   delegates**). The compose `worker` command already listens on
   `ingest,analyze,ai`, so a worker that is not consuming `analyze` is the first
   thing to check if delegated retries never appear.

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

   SELECT frequency, period_key, count(*), max(generated_at)
     FROM digests GROUP BY frequency, period_key ORDER BY 4 DESC;
   SELECT channel, status, count(*) FROM alert_deliveries GROUP BY channel, status;
   ```

   The audit row's `after` carries `opportunities`, `checks`, `events`,
   `events_considered`, `alerts_sent`, `alerts_suppressed` and `alerts_failed` —
   enough to tell a quiet night from a broken one. A digest count of zero for a
   period that has subscribers means the digest phase did not run or failed;
   `duplicates` in `scheduled_digests_written` means a retry found the work
   already done, which is the design working.

4. To run a phase by hand, without waiting for the schedule:

   ```bash
   docker compose exec worker celery -A app.workers.celery_app.celery_app call ois.run_monitoring

   # the period due right now, computed from the configured timezone
   docker compose exec worker celery -A app.workers.celery_app.celery_app \
     call ois.run_digests --kwargs='{"frequencies": ["daily"]}'

   # one specific period, e.g. to make up a night that failed — the boundaries
   # travel with the key, so this writes that period and not "the last 24 hours"
   docker compose exec worker celery -A app.workers.celery_app.celery_app \
     call ois.run_digests --kwargs='{"periods": [{"frequency": "daily", "key": "daily:2026-09-06", "start": "2026-09-06T00:00:00+00:00", "end": "2026-09-07T00:00:00+00:00", "timezone": "UTC"}]}'
   ```

   Both take the same advisory locks, so a manual run cannot collide with a
   scheduled one; whichever arrives second reports `skipped`. A manual digest run
   for a period that already exists writes nothing new and reports duplicates —
   which is what makes the "make up a missed night" command above safe to run
   twice.

## Tests

| File | What it proves | How |
|---|---|---|
| `tests/test_worker_scheduling.py` | schedule contents and defaults, timezone and day-name validation, phase ordering, deduplication across repeated runs, digest eligibility, unverified/unconfigured channels, per-user delivery-failure isolation, bounded retry wiring and backoff, retry delegation from the pipeline, broker-publish failure being reported, sanitized worker logs, that importing starts no scheduler | SQLite, providers faked or left unconfigured, clock passed explicitly, broker never contacted (Celery eager mode for the retry paths) |
| `tests/test_digest_periods.py` | period math (daily/weekly boundaries, local wall clock, 23- and 25-hour days, a 167-hour week), the key surviving the broker, repeated and legacy-key idempotency, retry identity after midnight and after a timezone change, a partial period finished without repeating its users, on-demand digests staying repeatable, migration `0007` against a genuinely pre-0007 table | SQLite; migration driven through the revision module's own `upgrade()`/`downgrade()` |
| `tests/test_alert_isolation.py` | relevance/rule/digest isolation, that a relevance failure skips a user rather than guessing, that a savepoint holds database work but not a message already sent (and the duplicate that follows), stop signals never being contained, `failure_code`/`is_unrecoverable` semantics, and sanitized logs *and* persisted provider details | SQLite, providers replaced by fakes for one test, sockets blocked by `conftest.py` |
| `tests/test_worker_concurrency_postgres.py` | the advisory lock really refuses a second transaction, really dies with a rollback, and really stops an overlapping run from dispatching; three runs racing for one period write one digest; an attempt that died before committing reserved nothing; an outer rollback discards a released savepoint; a real database error for one user does not cost the others | PostgreSQL, `poolclass=None` so each session is its own connection, gated on `TEST_POSTGRES_URL` |

The mocked concurrency checks in the first file are labelled as such where they
appear. No test may open a socket: `conftest.py` blocks it, so nothing can reach
Telegram or SMTP.

## Limitations

1. **Digests are not delivered anywhere.** They are rows the user reads in the
   application. Daily Telegram digest delivery is **not implemented**; nothing in
   this schedule sends a digest by any channel.
2. **The crash window is real and is not closed.** Send-before-commit means a
   killed worker can cause one duplicate external delivery for the batch in
   flight, and a rule that fails after sending leaves no record of a message that
   went out. Closing it requires committing the `pending` delivery row before
   `provider.send()` and updating its status afterwards, plus a sweep of rows
   left `pending` — a change inside `app/services/alerts.py::_deliver`.
3. **Loss is possible.** `acks_late=False` plus a kill loses the run; the
   lookback window is a second chance bounded by `MONITOR_LOOKBACK_HOURS`, not a
   queue. An event older than the window is never re-presented.
4. **Equivalent regenerated events may have different identities**, so the
   dedupe constraint does not always recognise a repeated fact; cooldown is the
   only limiter, and a `cooldown_hours = 0` rule can alert on equivalent facts on
   consecutive nights.
5. **A dropped retry is not made up later.** If the broker refuses the delegated
   digest retry, that period stays unwritten until an operator runs it by hand
   (command above). The failure is reported in the run's result and logged, not
   swallowed.
6. **Suppressed and failed external deliveries are never retried.** There is no
   delivery queue to retry from; a suppression row is terminal. Users still see
   the alert in-app.
7. **Collection has no cross-process lock** (it commits per source), so two
   overlapping collections duplicate HTTP requests but not data.
8. **`MONITOR_EVENT_LIMIT` bounds a run.** If more events than the limit occur
   inside the lookback window, the oldest are not considered by that run and are
   picked up by the next one only while they remain inside the window. Raise the
   limit rather than widening the lookback if a deployment is event-heavy.
9. **Savepoint isolation is weaker on SQLite** than on PostgreSQL, as described
   above. Development on SQLite can therefore show a phase reporting failure
   while some of its rows survived; production PostgreSQL does not.

## Follow-ups (deliberately not done here)

Each of these was considered and left out, because doing it would have changed
behaviour this task was not authorised to change:

* **Deliver digests** to a channel (Telegram/email), with per-channel
  verification and its own dedupe identity.
* **Stable event / dedupe identity** so a re-derived equivalent event is
  recognised as the same fact (limitation 4).
* **Commit-before-send** for deliveries, plus a `pending` sweep (limitation 2).
* **An outbox table** for external sends, which is what would make delivery
  genuinely at-least-once and is a much larger change than a scheduler.
* **Review cooldown semantics** for rules with `cooldown_hours = 0`.
* **Extend sanitization to `SourceRun.error`**, the user-visible ingestion
  history, which still stores what it stored before.
