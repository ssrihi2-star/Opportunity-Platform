"""One cross-process guard, using the database the workers already talk to.

Beat is supposed to be a singleton, and `worker_prefetch_multiplier=1` keeps a
worker from grabbing tomorrow's job today. Neither is enough on its own: a run
that overruns its slot is still executing when beat queues the next one, an
operator can dispatch the same task by hand, and a message can be redelivered
after a broker hiccup. Two monitoring runs at once would each hand the same
facts to the same providers before either had committed the rows that
deduplicate them, and the uniqueness constraint would then be checked too late
to help — it stops a second *row*, not a second Telegram message.

The guard is a PostgreSQL **transaction-scoped** advisory lock:

* it is taken inside the same transaction that does the work, so the commit or
  rollback that ends the phase releases it. There is nothing to remember to
  clean up and no TTL to guess at;
* a worker that is killed mid-phase loses its database connection, PostgreSQL
  aborts the transaction, and the lock goes with it — a crash cannot leave the
  schedule permanently locked out;
* `pg_try_advisory_xact_lock` never blocks. The second run is told "no"
  immediately and skips, instead of queueing behind the first and then doing the
  same work again anyway.

Session-scoped locks (`pg_try_advisory_lock`) are deliberately not used: with a
connection pool the session's connection returns to the pool at every commit, so
a lock taken before one commit is not held after it, and can leak to whoever
borrows that connection next. Every guarded phase here is therefore exactly one
transaction — which is also the transaction discipline the monitoring endpoint
already uses.

On any other dialect the guard reports "acquired" and gets out of the way.
SQLite, which the default test suite runs on, has no such function, so a SQLite
test can check the *wiring* around the guard but cannot prove concurrency. The
real behaviour — two independent transactions, one of them refused — is asserted
in `tests/test_worker_concurrency_postgres.py`, which is skipped unless
`TEST_POSTGRES_URL` points at a scratch PostgreSQL.
"""

from __future__ import annotations

import zlib

import sqlalchemy as sa
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.logging import get_logger

log = get_logger("worker.locks")


def lock_key(name: str) -> int:
    """Fold a stable name into the bigint PostgreSQL wants.

    Deterministic and dependency-free: two processes naming the same phase
    compute the same key without consulting a table, which is the point — the
    guard has to work before either of them has written anything.
    """
    return zlib.crc32(name.encode("utf-8"))


def is_postgresql(session: AsyncSession) -> bool:
    bind = session.get_bind()
    return bool(bind is not None and bind.dialect.name == "postgresql")


async def try_advisory_xact_lock(session: AsyncSession, name: str) -> bool:
    """True when this transaction now owns `name`, until it commits or rolls back.

    Never waits. False means somebody else is doing this work right now, and the
    caller should skip rather than duplicate it.
    """
    if not is_postgresql(session):
        # Not a claim that concurrency is safe here; a claim that this dialect
        # has no way to ask. See the module docstring.
        return True
    acquired = (
        await session.execute(
            sa.text("SELECT pg_try_advisory_xact_lock(:key)"),
            {"key": lock_key(name)},
        )
    ).scalar_one()
    if not acquired:
        log.info("worker_phase_already_running", lock=name)
    return bool(acquired)
