"""Report — and optionally remove — demo evidence from a working database.

Defaults to a **dry run**. Nothing is deleted unless `--apply` is passed, and
even then the whole purge runs in one transaction that is rolled back on any
error or on any refusal.

Why the evidence layer and not the results
------------------------------------------
It is tempting to delete `analysis_mode = 'demo_inclusive'` rows and call it
done. That would be wrong. A demo-inclusive trend is computed from live *and*
demo evidence together, so deleting it discards conclusions partly derived from
real collections. Instead this purges the *evidence* whose provenance is demo —
sources, their runs, raw records, signals and observations — and leaves the
derived rows to be recomputed from whatever survives.

Approved targets, and why unknown adapters stop the run
-------------------------------------------------------
The set of adapters this installation is allowed to purge is an **explicit
allow-list**: `demo_mock`, `scenario`, and the seeded `manual_csv` source.
Nothing else is ever a deletion target.

`app.sources.provenance` is still consulted, but its role here is narrower than
it looks. That function *fails closed* — it answers "not live" for an adapter it
cannot recognise, which is right for deciding what to **show** and dangerous for
deciding what to **delete**. "I do not know what this is" is a reason to stop,
not a reason to erase. So an unregistered or unrecognised adapter is a **hard
refusal**: the run aborts and names it, and no deletion happens anywhere until a
human has classified it.

Protected user content is never deleted
---------------------------------------
`user_notes` and `user_decisions` are things a person wrote. They are never
deleted, and they are never quietly detached either. If any of them reference a
row that this purge would remove, the run **stops and reports them** so the
operator can decide. The same applies to `opportunity_decisions`.

Everything else user-owned — accounts, profiles, preferences, Telegram links,
alert rules, watchlists — hangs off `users`, which this script never deletes
from. Row counts are asserted before and after; the transaction aborts if any
moves.

Watchlist entries
-----------------
Entries pointing at a purged opportunity **or at a purged trend** are removed;
the watchlists themselves, and all their surviving entries, are preserved. Both
directions are counted and reported separately, because a list can lose entries
through either.

Digest bodies are left alone
----------------------------
`digests.sections` is a denormalised JSON snapshot containing demo titles as
plain text, copied at generation time. No row deletion cleans it. It is
reported, never rewritten: a digest records what was sent to somebody on a date,
and editing it would falsify that record.
"""

from __future__ import annotations

import argparse
import asyncio
import sys
import uuid
from collections import OrderedDict
from dataclasses import dataclass, field

import sqlalchemy as sa
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

from app.core.config import get_settings
from app.sources.provenance import live_eligibility

#: The only adapters this installation may purge. An adapter outside this set is
#: never deleted, whatever `provenance` thinks of it — including adapters that
#: are merely unregistered, which are a refusal rather than a target.
APPROVED_DEMO_ADAPTERS: frozenset[str] = frozenset({"demo_mock", "scenario"})

#: The single seeded CSV source approved for removal, matched by slug rather
#: than adapter: `csv_import` is also how a real user-uploaded file would arrive,
#: and those must not be swept up with the fixture.
APPROVED_DEMO_SLUGS: frozenset[str] = frozenset({"manual_csv"})

#: Row counts asserted identical before and after. Not a comment — checked in
#: `--apply` mode, and the transaction aborts if any of them moves.
PRESERVE_TABLES: tuple[str, ...] = (
    "users",
    "user_profiles",
    "user_preferences",
    "notification_channel_links",
    "alert_rules",
    "watchlists",
    "user_notes",
    "user_decisions",
    "opportunity_decisions",
    "digests",
)


class PurgeRefused(RuntimeError):
    """Raised when the purge must not proceed. Carries an operator-facing report."""


@dataclass
class Plan:
    """What the purge would do. Built read-only; printed either way."""

    demo_sources: list[tuple[str, str, str, str]] = field(default_factory=list)
    live_sources: list[tuple[str, str, str]] = field(default_factory=list)
    unknown_sources: list[tuple[str, str, str, str]] = field(default_factory=list)
    counts: OrderedDict[str, int] = field(default_factory=OrderedDict)
    cascades: OrderedDict[str, int] = field(default_factory=OrderedDict)
    preserved: OrderedDict[str, int] = field(default_factory=OrderedDict)
    mixed_trends: int = 0
    mixed_opportunities: int = 0
    stale_snapshots: int = 0
    dirty_digests: list[tuple[str, str, int]] = field(default_factory=list)
    watchlist_by_opp: list[tuple[str, str, str]] = field(default_factory=list)
    watchlist_by_trend: list[tuple[str, str, str]] = field(default_factory=list)
    protected_blocks: OrderedDict[str, list[str]] = field(default_factory=OrderedDict)


async def _scalar(session: AsyncSession, stmt) -> int:
    return int((await session.execute(stmt)).scalar() or 0)


def _uuid_list(name: str, values: list[str]) -> sa.BindParameter:
    """An expanding bind parameter explicitly typed as UUID.

    PostgreSQL will not compare `uuid` to `varchar` and raises
    `operator does not exist: uuid <> character varying`. SQLite coerces
    silently, so an untyped bind passes there and fails on the real database —
    which is exactly the class of bug a SQLite-only rehearsal cannot catch.
    Empty lists still need the type, hence the explicit `type_`.
    """
    return sa.bindparam(
        name, value=[uuid.UUID(str(v)) for v in values], expanding=True, type_=sa.Uuid()
    )


async def classify_sources(session: AsyncSession, plan: Plan) -> list[str]:
    """Split stored sources into live, approved-demo and unknown.

    Returns the ids approved for deletion. Reads `sources` only — no adapter is
    invoked and nothing is fetched.

    A source is a deletion target only when it is on the allow-list *and*
    `provenance` independently agrees it is not live. Requiring both means a
    mistake in either one fails safe: the allow-list cannot delete something the
    provenance rule considers live, and the provenance rule cannot delete
    something the operator has not approved.
    """
    rows = (
        await session.execute(
            sa.text("SELECT id, slug, adapter_key, source_class FROM sources ORDER BY slug")
        )
    ).all()

    demo_ids: list[str] = []
    for sid, slug, adapter_key, source_class in rows:
        adapter = adapter_key or ""
        approved = adapter in APPROVED_DEMO_ADAPTERS or slug in APPROVED_DEMO_SLUGS
        eligible, reason = live_eligibility(adapter, source_class)

        if approved and not eligible:
            demo_ids.append(str(sid))
            plan.demo_sources.append((str(slug), adapter, str(source_class), reason))
        elif eligible:
            plan.live_sources.append((str(slug), adapter, str(source_class)))
        else:
            # Not live, but not approved either: unknown provenance. This is the
            # case that must stop the run rather than be deleted.
            plan.unknown_sources.append((str(slug), adapter, str(source_class), reason))
    return demo_ids


async def _all_demo_trend_ids(session: AsyncSession, ids) -> list[str]:
    """Trends whose every signal is demo, resolved to concrete ids **first**.

    Resolved up front rather than left as a subquery, because the deletes below
    empty `trend_signals`, and the predicate that identifies these trends reads
    `trend_signals`. Evaluated late it matches nothing and silently leaves the
    trends behind.
    """
    rows = (
        await session.execute(
            sa.text(
                "SELECT t.id FROM trends t WHERE EXISTS ("
                "  SELECT 1 FROM trend_signals ts WHERE ts.trend_id = t.id"
                ") AND NOT EXISTS ("
                "  SELECT 1 FROM trend_signals ts WHERE ts.trend_id = t.id AND ts.source_id NOT IN :ids"
                ")"
            ).bindparams(ids)
        )
    ).all()
    return [str(r[0]) for r in rows]


async def check_protected_content(session: AsyncSession, ids, tids) -> OrderedDict[str, list[str]]:
    """Find protected user content that the purge would otherwise destroy.

    Returns a mapping of table -> human-readable descriptions. A non-empty result
    means the run must stop: these rows are things a person wrote, and neither
    deleting them nor silently detaching them is a decision this script may take.
    """
    blocks: OrderedDict[str, list[str]] = OrderedDict()
    doomed_opps = (
        "SELECT id FROM opportunities WHERE primary_trend_id IN "
        "(SELECT id FROM trends WHERE id IN :tids)"
    )

    for table, label_sql in (
        (
            "user_notes",
            "SELECT u.email, o.title, substr(n.body, 1, 60) FROM user_notes n "
            "JOIN users u ON u.id = n.user_id JOIN opportunities o ON o.id = n.opportunity_id "
            f"WHERE n.opportunity_id IN ({doomed_opps})",
        ),
        (
            "user_decisions",
            "SELECT u.email, o.title, d.decision FROM user_decisions d "
            "JOIN users u ON u.id = d.user_id JOIN opportunities o ON o.id = d.opportunity_id "
            f"WHERE d.opportunity_id IN ({doomed_opps})",
        ),
        (
            "opportunity_decisions",
            "SELECT u.email, o.title, d.interest FROM opportunity_decisions d "
            "JOIN users u ON u.id = d.user_id JOIN opportunities o ON o.id = d.opportunity_id "
            f"WHERE d.opportunity_id IN ({doomed_opps})",
        ),
    ):
        try:
            rows = (await session.execute(sa.text(label_sql).bindparams(tids))).all()
        except Exception:  # noqa: BLE001 - a missing optional column must not mask the check
            rows = (
                await session.execute(
                    sa.text(
                        f"SELECT '?', '?', '?' FROM {table} "  # noqa: S608 - fixed identifier
                        f"WHERE opportunity_id IN ({doomed_opps})"
                    ).bindparams(tids)
                )
            ).all()
        if rows:
            blocks[table] = [f"{a} — {b} — {str(c)[:60]}" for a, b, c in rows]
    return blocks


async def build_plan(session: AsyncSession) -> Plan:
    """Count everything the purge would remove. Pure reads, no mutation."""
    plan = Plan()
    demo_ids = await classify_sources(session, plan)

    async def preserved() -> None:
        for table in PRESERVE_TABLES:
            plan.preserved[table] = await _scalar(
                session, sa.text(f"SELECT count(*) FROM {table}")  # noqa: S608 - fixed literals
            )

    if plan.unknown_sources or not demo_ids:
        await preserved()
        return plan

    ids = _uuid_list("ids", demo_ids)
    demo_trend_ids = await _all_demo_trend_ids(session, ids)
    tids = _uuid_list("tids", demo_trend_ids)

    plan.protected_blocks = await check_protected_content(session, ids, tids)

    doomed_opps = (
        "SELECT id FROM opportunities WHERE primary_trend_id IN "
        "(SELECT id FROM trends WHERE id IN :tids)"
    )
    demo_signals = "SELECT id FROM signals WHERE source_id IN :ids"

    # ---- directly deleted, keyed by the demo source -------------------------
    for table, where, params in (
        ("source_runs", "source_id IN :ids", (ids,)),
        ("raw_records", "source_id IN :ids", (ids,)),
        ("signals", "source_id IN :ids", (ids,)),
        ("trend_signals", "source_id IN :ids", (ids,)),
        ("source_validations", "source_id IN :ids", (ids,)),
        ("source_credentials", "source_id IN :ids", (ids,)),
        ("http_cache_entries", "source_id IN :ids", (ids,)),
        ("country_facts", "source_id IN :ids", (ids,)),
        ("entity_match_candidates", "source_id IN :ids", (ids,)),
    ):
        plan.counts[table] = await _scalar(
            session,
            sa.text(f"SELECT count(*) FROM {table} WHERE {where}").bindparams(*params),  # noqa: S608
        )
    plan.counts["signal_observations"] = await _scalar(
        session,
        sa.text(
            f"SELECT count(*) FROM signal_observations WHERE signal_id IN ({demo_signals})"
        ).bindparams(ids),
    )
    plan.counts["sources"] = len(demo_ids)
    plan.counts["trends (all-demo)"] = len(demo_trend_ids)
    plan.counts["opportunities (from all-demo trends)"] = await _scalar(
        session, sa.text(f"SELECT count(*) FROM ({doomed_opps}) q").bindparams(tids)
    )

    # ---- references to purged evidence held by SURVIVING rows ---------------
    # These are the joins that make a naive purge fail on PostgreSQL. Both are
    # NO ACTION, so the database refuses the delete rather than cascading, and
    # both can belong to a *mixed* opportunity that is itself being kept.
    plan.counts["opportunity_signals (-> purged signals)"] = await _scalar(
        session,
        sa.text(
            f"SELECT count(*) FROM opportunity_signals WHERE signal_id IN ({demo_signals})"
        ).bindparams(ids),
    )
    plan.counts["evidence_items (-> purged evidence)"] = await _scalar(
        session,
        sa.text(
            "SELECT count(*) FROM evidence_items WHERE source_id IN :ids "
            "OR raw_record_id IN (SELECT id FROM raw_records WHERE source_id IN :ids) "
            "OR signal_observation_id IN "
            f"(SELECT id FROM signal_observations WHERE signal_id IN ({demo_signals}))"
        ).bindparams(ids),
    )
    # How many of those belong to opportunities that SURVIVE — the mixed case.
    plan.mixed_opportunities = await _scalar(
        session,
        sa.text(
            "SELECT count(DISTINCT opportunity_id) FROM ("
            f"  SELECT opportunity_id FROM opportunity_signals WHERE signal_id IN ({demo_signals})"
            "  UNION"
            "  SELECT opportunity_id FROM evidence_items WHERE source_id IN :ids"
            ") q WHERE opportunity_id IS NOT NULL "
            f"AND opportunity_id NOT IN ({doomed_opps})"
        ).bindparams(ids, tids),
    )

    # ---- the model_runs cycle ----------------------------------------------
    # trends -> model_runs -> opportunities -> trends. All three FKs are NO
    # ACTION and nullable, so the cycle is broken by detaching, not deleting.
    plan.counts["model_runs (detached from purged opportunities)"] = await _scalar(
        session,
        sa.text(
            f"SELECT count(*) FROM model_runs WHERE opportunity_id IN ({doomed_opps})"
        ).bindparams(tids),
    )
    plan.cascades["trends.explanation_model_run_id (nulled)"] = await _scalar(
        session,
        sa.text(
            "SELECT count(*) FROM trends WHERE explanation_model_run_id IN "
            f"(SELECT id FROM model_runs WHERE opportunity_id IN ({doomed_opps}))"
        ).bindparams(tids),
    )

    # ---- cascade-affected children of purged opportunities/trends -----------
    for table in (
        "opportunity_scores",
        "opportunity_participation",
        "opportunity_conditions",
        "opportunity_topics",
        "opportunity_trends",
        "opportunity_entities",
        "opportunity_risks",
        "opportunity_change_events",
        "user_opportunity_relevance",
        "user_opportunity_feedback",
        "alert_deliveries",
        "reports",
        "skeptic_reviews",
        "predictions",
        "alerts",
    ):
        plan.cascades[table] = await _scalar(
            session,
            sa.text(
                f"SELECT count(*) FROM {table} WHERE opportunity_id IN ({doomed_opps})"  # noqa: S608
            ).bindparams(tids),
        )
    plan.cascades["condition_checks"] = await _scalar(
        session,
        sa.text(
            "SELECT count(*) FROM condition_checks WHERE condition_id IN "
            f"(SELECT id FROM opportunity_conditions WHERE opportunity_id IN ({doomed_opps}))"
        ).bindparams(tids),
    )
    plan.cascades["trend_snapshots (of purged trends)"] = await _scalar(
        session,
        sa.text("SELECT count(*) FROM trend_snapshots WHERE trend_id IN :tids").bindparams(tids),
    )

    # ---- watchlist entries, both directions ---------------------------------
    plan.watchlist_by_opp = [
        (str(a), str(b), str(c))
        for a, b, c in (
            await session.execute(
                sa.text(
                    "SELECT w.name, o.title, u.email FROM watchlist_items wi "
                    "JOIN watchlists w ON w.id = wi.watchlist_id "
                    "JOIN users u ON u.id = w.user_id "
                    "JOIN opportunities o ON o.id = wi.opportunity_id "
                    f"WHERE wi.opportunity_id IN ({doomed_opps}) ORDER BY u.email, w.name"
                ).bindparams(tids)
            )
        ).all()
    ]
    plan.watchlist_by_trend = [
        (str(a), str(b), str(c))
        for a, b, c in (
            await session.execute(
                sa.text(
                    "SELECT w.name, t.name, u.email FROM watchlist_items wi "
                    "JOIN watchlists w ON w.id = wi.watchlist_id "
                    "JOIN users u ON u.id = w.user_id "
                    "JOIN trends t ON t.id = wi.trend_id "
                    "WHERE wi.trend_id IN :tids ORDER BY u.email, w.name"
                ).bindparams(tids)
            )
        ).all()
    ]
    plan.counts["watchlist_items (via opportunity)"] = len(plan.watchlist_by_opp)
    plan.counts["watchlist_items (via trend)"] = len(plan.watchlist_by_trend)

    # ---- what survives but is now stale -------------------------------------
    plan.mixed_trends = await _scalar(
        session,
        sa.text(
            "SELECT count(*) FROM trends t WHERE EXISTS ("
            "  SELECT 1 FROM trend_signals ts WHERE ts.trend_id = t.id AND ts.source_id IN :ids"
            ") AND EXISTS ("
            "  SELECT 1 FROM trend_signals ts WHERE ts.trend_id = t.id AND ts.source_id NOT IN :ids"
            ")"
        ).bindparams(ids),
    )
    plan.stale_snapshots = await _scalar(
        session,
        sa.text(
            "SELECT count(*) FROM trend_snapshots WHERE trend_id IN ("
            "  SELECT t.id FROM trends t WHERE EXISTS ("
            "    SELECT 1 FROM trend_signals ts WHERE ts.trend_id = t.id AND ts.source_id IN :ids"
            "  ) AND EXISTS ("
            "    SELECT 1 FROM trend_signals ts WHERE ts.trend_id = t.id AND ts.source_id NOT IN :ids"
            "  )"
            ")"
        ).bindparams(ids),
    )

    for did, freq, sections in (
        await session.execute(sa.text("SELECT id, frequency, sections FROM digests"))
    ).all():
        titles = _titles_in(sections)
        if not titles:
            continue
        hits = await _scalar(
            session,
            sa.text(
                f"SELECT count(*) FROM opportunities o WHERE o.title IN :titles "
                f"AND o.id IN ({doomed_opps})"
            ).bindparams(tids, sa.bindparam("titles", value=list(titles), expanding=True)),
        )
        if hits:
            plan.dirty_digests.append((str(did), str(freq), hits))

    await preserved()
    return plan


def _titles_in(sections: object) -> set[str]:
    """Every item title inside a stored digest snapshot."""
    found: set[str] = set()
    if not isinstance(sections, dict):
        return found
    for value in sections.values():
        if isinstance(value, list):
            for item in value:
                if isinstance(item, dict) and isinstance(item.get("title"), str):
                    found.add(item["title"])
    return found


async def apply_purge(session: AsyncSession, demo_ids: list[str]) -> None:
    """Delete, in an order PostgreSQL accepts. Caller owns the transaction.

    Ordering constraints that are not obvious, each of which breaks the run on
    PostgreSQL if got wrong (SQLite is laxer and hides several of them):

    * The all-demo trend set is resolved to ids *before* anything is deleted,
      because the predicate identifying it reads `trend_signals`.
    * `opportunity_signals` and `evidence_items` are `NO ACTION` references to
      signals, observations, raw records and sources. They must go before their
      targets — including rows owned by *surviving* mixed opportunities, which
      no cascade will ever reach.
    * The `trends -> model_runs -> opportunities -> trends` cycle is broken by
      nulling the two nullable NO ACTION edges before deleting either end.
    * `raw_records` precedes `source_runs`, because `raw_records.source_run_id`
      is NO ACTION.
    """
    ids = _uuid_list("ids", demo_ids)
    demo_trend_ids = await _all_demo_trend_ids(session, ids)
    tids = _uuid_list("tids", demo_trend_ids)

    doomed_opps = (
        "SELECT id FROM opportunities WHERE primary_trend_id IN "
        "(SELECT id FROM trends WHERE id IN :tids)"
    )
    demo_signals = "SELECT id FROM signals WHERE source_id IN :ids"
    demo_obs = f"SELECT id FROM signal_observations WHERE signal_id IN ({demo_signals})"
    demo_raw = "SELECT id FROM raw_records WHERE source_id IN :ids"

    statements = (
        # 1. Break the model_runs cycle by detaching, never deleting a user-
        #    facing explanation from a trend that survives.
        "UPDATE trends SET explanation_model_run_id = NULL WHERE explanation_model_run_id IN "
        f"(SELECT id FROM model_runs WHERE opportunity_id IN ({doomed_opps}))",
        "UPDATE reports SET model_run_id = NULL WHERE model_run_id IN "
        f"(SELECT id FROM model_runs WHERE opportunity_id IN ({doomed_opps}))",
        "UPDATE skeptic_reviews SET model_run_id = NULL WHERE model_run_id IN "
        f"(SELECT id FROM model_runs WHERE opportunity_id IN ({doomed_opps}))",
        f"DELETE FROM model_runs WHERE opportunity_id IN ({doomed_opps})",
        # 2. NO ACTION references to purged evidence, from any owner — including
        #    surviving mixed opportunities.
        f"DELETE FROM opportunity_signals WHERE signal_id IN ({demo_signals})",
        "DELETE FROM evidence_items WHERE source_id IN :ids "
        f"OR raw_record_id IN ({demo_raw}) OR signal_observation_id IN ({demo_obs})",
        # 3. NO ACTION children of the opportunities being removed.
        f"DELETE FROM watchlist_items WHERE opportunity_id IN ({doomed_opps})",
        "DELETE FROM watchlist_items WHERE trend_id IN :tids",
        f"DELETE FROM alerts WHERE opportunity_id IN ({doomed_opps})",
        f"DELETE FROM predictions WHERE opportunity_id IN ({doomed_opps})",
        # 4. The opportunities themselves; their CASCADE children follow.
        "DELETE FROM opportunities WHERE primary_trend_id IN "
        "(SELECT id FROM trends WHERE id IN :tids)",
        # 5. Trend-level evidence links, then the trends.
        "DELETE FROM trend_signals WHERE source_id IN :ids",
        "DELETE FROM trends WHERE id IN :tids",
        # 6. The evidence itself.
        f"DELETE FROM signal_observations WHERE signal_id IN ({demo_signals})",
        "DELETE FROM signals WHERE source_id IN :ids",
        "DELETE FROM raw_records WHERE source_id IN :ids",
        "DELETE FROM raw_records WHERE source_run_id IN "
        "(SELECT id FROM source_runs WHERE source_id IN :ids)",
        "DELETE FROM source_runs WHERE source_id IN :ids",
        "DELETE FROM source_validations WHERE source_id IN :ids",
        "DELETE FROM source_credentials WHERE source_id IN :ids",
        "DELETE FROM http_cache_entries WHERE source_id IN :ids",
        "DELETE FROM country_facts WHERE source_id IN :ids",
        "DELETE FROM entity_match_candidates WHERE source_id IN :ids",
        "DELETE FROM sources WHERE id IN :ids",
    )

    for stmt in statements:
        clause = sa.text(stmt)
        wanted = [p for p, present in ((ids, ":ids" in stmt), (tids, ":tids" in stmt)) if present]
        await session.execute(clause.bindparams(*wanted) if wanted else clause)


RECOMPUTE_PROCEDURE = """\
RECOMPUTING MIXED RESULTS
=========================
A mixed trend drew on both live and demo evidence. Its demo signals are gone,
but its stored numbers were computed while they were present, so until it is
re-evaluated it shows a score derived from evidence that no longer exists.

The commands below are for PowerShell. Each uses a here-string (@' ... '@) piped
into `python -`, so nothing needs quoting or line-continuation escapes. The
closing '@ must sit at the very start of its own line -- PowerShell requires it.
Note the -T flag: without it docker compose will not forward stdin.

STEP 1 - clear the monotonic peaks BEFORE recomputing (required)
-----------------------------------------------------------------
`trends.peak_score` and `opportunities.peak_score` are MONOTONIC: the upsert
paths only ever raise them, via `max(...)`. `next_state()` then compares the
fresh score against the stored peak and returns "weakening" once the gap reaches
the drop threshold (20 for trends, 18 for opportunities). A peak inflated by
purged demo evidence therefore survives the purge and pushes a correctly-scored
trend into a decline it never entered.

Reset to ZERO, not to `trend_score`. At reset time `trend_score` is *itself* the
demo-contaminated number -- it is only overwritten later, during evaluation --
so `peak_score = trend_score` just relocates the inflated value instead of
clearing it, and still yields a spurious "weakening" whenever the contamination
exceeds the threshold. Zero is unconditionally safe: the first evaluation
immediately raises the peak to the newly computed score.

@'
import asyncio, sqlalchemy as sa
from app.db.session import SessionLocal
async def main():
    async with SessionLocal() as s:
        t = await s.execute(sa.text('UPDATE trends SET peak_score = 0, peak_score_at = NULL'))
        o = await s.execute(sa.text('UPDATE opportunities SET peak_score = 0'))
        await s.commit()
        print('trends reset:', t.rowcount, 'opportunities reset:', o.rowcount)
asyncio.run(main())
'@ | docker compose exec -T api python -

This resets every row, which is the conservative and correct choice. A peak is a
historical high-water mark carrying no record of which evidence produced it, so
there is no reliable way to reset "only the contaminated ones". Zeroing all of
them costs nothing -- each is re-established from live evidence on the next
evaluation -- whereas missing one leaves a false decline on the board.

STEP 2 - re-evaluate trends from surviving evidence
----------------------------------------------------
@'
import asyncio
from app.db.session import SessionLocal
from app.services.trends import evaluate_trends
async def main():
    async with SessionLocal() as s:
        trends = await evaluate_trends(s)
        await s.commit()
        print('trends evaluated:', len(trends))
asyncio.run(main())
'@ | docker compose exec -T api python -

STEP 3 - regenerate opportunities
-----------------------------------
@'
import asyncio
from app.db.session import SessionLocal
from app.services.opportunities import generate_opportunities
async def main():
    async with SessionLocal() as s:
        result = await generate_opportunities(s)
        await s.commit()
        print('created:', len(result.created), 'updated:', len(result.updated))
asyncio.run(main())
'@ | docker compose exec -T api python -

There is no `app.db.session.session_scope` in this codebase. The session factory
is `app.db.session.SessionLocal`, and it does NOT commit on exit -- only the
FastAPI dependency `get_session` does. The explicit `await s.commit()` in each
step is therefore required; without it the work silently rolls back.

WHAT CANNOT CONTAMINATE THE REBUILD
------------------------------------
* `trend_snapshots` and `opportunity_scores` are append-only history, one row per
  evaluation. Rows written while demo evidence was present are NOT deleted for
  surviving trends: they record what the system genuinely reported at the time.
  Neither table is read back during evaluation, so they cannot influence a
  rebuild. Their counts are reported above so the staleness is visible.
* Every other stored field on a trend or opportunity is overwritten
  unconditionally on re-evaluation.

VERIFY
-------
docker compose exec -T postgres psql -U ois -d ois -c "SELECT count(*) AS dangling FROM trend_signals ts LEFT JOIN sources s ON s.id = ts.source_id WHERE s.id IS NULL;"

Expect 0. Then confirm live evidence survived and no inflated peak remains:

docker compose exec -T postgres psql -U ois -d ois -c "SELECT s.slug, count(o.id) AS observations FROM sources s LEFT JOIN signals g ON g.source_id = s.id LEFT JOIN signal_observations o ON o.signal_id = g.id GROUP BY s.slug ORDER BY s.slug;"

docker compose exec -T postgres psql -U ois -d ois -c "SELECT count(*) AS still_inflated FROM trends WHERE peak_score > trend_score;"

The observation counts should still show your live sources (for example
wikipedia and hackernews) at their pre-purge volumes.
"""


def render(plan: Plan, *, applied: bool) -> None:
    head = "PURGE APPLIED" if applied else "DRY RUN — nothing was deleted"
    print(f"\n{'=' * 72}\n{head}\n{'=' * 72}")

    print(f"\nLIVE sources kept ({len(plan.live_sources)}):")
    for slug, adapter, cls in plan.live_sources:
        print(f"  KEEP    {slug:<28} {adapter:<20} {cls}")

    if plan.unknown_sources:
        print(f"\n!! UNKNOWN sources — RUN REFUSED ({len(plan.unknown_sources)}):")
        for slug, adapter, cls, reason in plan.unknown_sources:
            print(f"  BLOCK   {slug:<28} {adapter:<20} {cls}")
            print(f"          {reason}")
        print(
            "\n  These are not live, but they are not on the approved list either, so\n"
            "  their provenance is unverified. Deleting data on a guess is not\n"
            "  something this script will do. Approved for this installation:\n"
            f"    adapters {sorted(APPROVED_DEMO_ADAPTERS)}, slugs {sorted(APPROVED_DEMO_SLUGS)}\n"
            "  Classify each source above, then re-run."
        )
        return

    print(f"\nDEMO sources to remove ({len(plan.demo_sources)}):")
    for slug, adapter, cls, reason in plan.demo_sources:
        print(f"  REMOVE  {slug:<28} {adapter:<20} {cls}")
        print(f"          reason: {reason}")

    if plan.protected_blocks:
        print("\n!! PROTECTED USER CONTENT — RUN REFUSED:")
        for table, entries in plan.protected_blocks.items():
            print(f"\n  {table} ({len(entries)}):")
            for entry in entries[:20]:
                print(f"    - {entry}")
            if len(entries) > 20:
                print(f"    ... and {len(entries) - 20} more")
        print(
            "\n  These rows are content a person wrote about an opportunity this purge\n"
            "  would delete. They are never deleted and never silently detached, so\n"
            "  the run stops here. Move or remove them deliberately, then re-run."
        )
        return

    print("\nDirectly deleted:")
    for table, count in plan.counts.items():
        print(f"  {count:>8}  {table}")

    print("\nCascade-affected (removed by the database with their parent):")
    for table, count in plan.cascades.items():
        print(f"  {count:>8}  {table}")

    if plan.mixed_opportunities:
        print(
            f"\n  {plan.mixed_opportunities} SURVIVING opportunity(ies) hold references to purged\n"
            "  evidence via opportunity_signals / evidence_items. Those links are removed;\n"
            "  the opportunities themselves are kept and must be recomputed."
        )

    if plan.watchlist_by_opp or plan.watchlist_by_trend:
        print("\nWatchlist entries removed — the lists themselves are kept:")
        for wl, title, email in plan.watchlist_by_opp:
            print(f"  [opportunity] {email:<26} {wl:<20} -> {title}")
        for wl, name, email in plan.watchlist_by_trend:
            print(f"  [trend]       {email:<26} {wl:<20} -> {name}")

    if plan.dirty_digests:
        print(f"\nDigests naming purged opportunities in stored JSON ({len(plan.dirty_digests)}):")
        for did, freq, hits in plan.dirty_digests:
            print(f"  {freq:<8} {did}  ({hits} demo title(s))")
        print(
            "  Historical snapshots of messages already sent. Left exactly as they are:\n"
            "  rewriting them would falsify the delivery record."
        )

    print("\nPreserved (asserted unchanged when applying):")
    for table, count in plan.preserved.items():
        print(f"  {count:>8}  {table}")

    if plan.mixed_trends or plan.stale_snapshots:
        print(
            f"\n  {plan.mixed_trends} trend(s) drew on BOTH live and demo evidence and are NOT\n"
            f"  deleted. {plan.stale_snapshots} snapshot(s) of them predate the purge.\n"
            "  Their stored scores still reflect demo evidence until recomputed."
        )
        print(f"\n{RECOMPUTE_PROCEDURE}")


async def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--apply", action="store_true", help="Actually delete. Without this the script only reports."
    )
    parser.add_argument("--database-url", default=None, help="Override the configured DATABASE_URL.")
    args = parser.parse_args()

    url = args.database_url or get_settings().DATABASE_URL
    shown = url.split("@")[-1] if "@" in url else url
    print(f"database: ...@{shown}")

    engine = create_async_engine(url)
    maker = async_sessionmaker(engine, expire_on_commit=False)

    try:
        async with maker() as session:
            plan = await build_plan(session)

            if plan.unknown_sources or plan.protected_blocks:
                render(plan, applied=False)
                print("\nREFUSED. Nothing was deleted.\n")
                return 2

            if not args.apply:
                render(plan, applied=False)
                print(
                    "\nNothing was deleted. Re-run with --apply to perform the purge,\n"
                    "after taking a dump:\n"
                    "  docker compose exec -T postgres pg_dump -U ois ois > backup.sql\n"
                )
                return 0

            demo_ids = await classify_sources(session, Plan())
            before = dict(plan.preserved)

            try:
                await apply_purge(session, demo_ids)
                after = {
                    t: await _scalar(session, sa.text(f"SELECT count(*) FROM {t}"))  # noqa: S608
                    for t in PRESERVE_TABLES
                }
                moved = {t: (before[t], after[t]) for t in PRESERVE_TABLES if before[t] != after[t]}
                if moved:
                    raise PurgeRefused(f"preserved tables changed, rolling back: {moved}")
            except Exception:
                await session.rollback()
                raise
            await session.commit()

            final = await build_plan(session)
            render(final, applied=True)
            return 0
    finally:
        await engine.dispose()


if __name__ == "__main__":
    sys.exit(asyncio.run(main()))
