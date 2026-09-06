"""Report — and optionally remove — demo evidence from a working database.

Defaults to a **dry run**. Nothing is deleted unless `--apply` is passed, and
even then the whole purge runs in one transaction that is rolled back on any
error.

Why the evidence layer and not the results
------------------------------------------
It is tempting to delete `analysis_mode = 'demo_inclusive'` rows and call it
done. That would be wrong. A demo-inclusive trend is computed from live *and*
demo evidence together, so deleting it discards conclusions partly derived from
real collections. Instead this purges the *evidence* whose provenance is demo —
sources, their runs, raw records, signals and observations — and leaves the
derived rows to be recomputed from whatever survives.

The eligibility rule is not reinvented here. It is `app.sources.provenance`,
the same function the live-only analysis mode uses, so "what gets purged" and
"what live-only mode already excludes" cannot drift apart. Under that rule a
source is demo when its class is `demo`, when its adapter generates its own
data (`scenario`, `demo_mock`), when its adapter never contacts the network
(`csv_import` — the seeded manual CSV), or when its adapter is unregistered and
therefore of unknown provenance.

What is never touched
---------------------
Users, profiles, preferences, notification channel links (Telegram bindings),
alert rules, watchlists and notes. Everything user-owned hangs off `users`,
which this script does not delete from. They are counted before and after in
`--apply` mode and the transaction aborts if any count moves.

Two consequences worth reading before you run it
------------------------------------------------
**Watchlist entries pointing at demo opportunities are removed explicitly.**
`watchlist_items.opportunity_id` is `NO ACTION`, so the database would refuse
the delete rather than cascade. Per the operator's decision the entry is
removed and the watchlist itself — with all its live entries — is preserved.

**Digest bodies are denormalised JSON snapshots.** `digests.sections` contains
demo titles as plain text, copied at generation time. No row deletion cleans
them. They are reported, never rewritten: a digest is a record of what was sent
to somebody on a date, and editing it would falsify that record. Deleting the
underlying opportunity does not alter the snapshot.
"""

from __future__ import annotations

import argparse
import asyncio
import sys
from collections import OrderedDict
from dataclasses import dataclass, field

import sqlalchemy as sa
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

from app.core.config import get_settings
from app.sources.provenance import live_eligibility

#: Tables whose row counts must be identical before and after. Not a comment —
#: asserted in `--apply` mode, and the transaction aborts if any of them moves.
PRESERVE_TABLES: tuple[str, ...] = (
    "users",
    "user_profiles",
    "user_preferences",
    "notification_channel_links",
    "alert_rules",
    "watchlists",
    "user_notes",
    "user_decisions",
    "digests",
)


@dataclass
class Plan:
    """What the purge would do. Built read-only; printed either way."""

    demo_sources: list[tuple[str, str, str, str]] = field(default_factory=list)
    live_sources: list[tuple[str, str, str]] = field(default_factory=list)
    counts: OrderedDict[str, int] = field(default_factory=OrderedDict)
    preserved: OrderedDict[str, int] = field(default_factory=OrderedDict)
    mixed_trends: int = 0
    dirty_digests: list[tuple[str, str, int]] = field(default_factory=list)
    watchlist_hits: list[tuple[str, str, str]] = field(default_factory=list)
    unregistered: list[str] = field(default_factory=list)


async def _scalar(session: AsyncSession, stmt) -> int:
    return int((await session.execute(stmt)).scalar() or 0)


async def classify_sources(session: AsyncSession, plan: Plan) -> list[str]:
    """Split stored sources into demo and live using the live-only rule.

    Returns the demo source ids. Reads `sources` only — no adapter is invoked
    and nothing is fetched.
    """
    rows = (
        await session.execute(
            sa.text("SELECT id, slug, adapter_key, source_class FROM sources ORDER BY slug")
        )
    ).all()

    demo_ids: list[str] = []
    for sid, slug, adapter_key, source_class in rows:
        eligible, reason = live_eligibility(adapter_key or "", source_class)
        if eligible:
            plan.live_sources.append((str(slug), str(adapter_key), str(source_class)))
        else:
            demo_ids.append(str(sid))
            plan.demo_sources.append((str(slug), str(adapter_key), str(source_class), reason))
            if "not registered" in reason:
                plan.unregistered.append(str(slug))
    return demo_ids


async def build_plan(session: AsyncSession) -> Plan:
    """Count everything the purge would remove. Pure reads."""
    plan = Plan()
    demo_ids = await classify_sources(session, plan)

    if not demo_ids:
        for table in PRESERVE_TABLES:
            plan.preserved[table] = await _scalar(
                session, sa.text(f"SELECT count(*) FROM {table}")  # noqa: S608 - fixed literals
            )
        return plan

    ids = sa.bindparam("ids", value=demo_ids, expanding=True)

    # Evidence directly attributable to a demo source.
    plan.counts["source_runs"] = await _scalar(
        session, sa.text("SELECT count(*) FROM source_runs WHERE source_id IN :ids").bindparams(ids)
    )
    plan.counts["raw_records"] = await _scalar(
        session, sa.text("SELECT count(*) FROM raw_records WHERE source_id IN :ids").bindparams(ids)
    )
    plan.counts["signals"] = await _scalar(
        session, sa.text("SELECT count(*) FROM signals WHERE source_id IN :ids").bindparams(ids)
    )
    # Observations have no source column; they belong to a demo signal.
    plan.counts["signal_observations"] = await _scalar(
        session,
        sa.text(
            "SELECT count(*) FROM signal_observations o "
            "JOIN signals s ON s.id = o.signal_id WHERE s.source_id IN :ids"
        ).bindparams(ids),
    )
    plan.counts["trend_signals"] = await _scalar(
        session, sa.text("SELECT count(*) FROM trend_signals WHERE source_id IN :ids").bindparams(ids)
    )
    plan.counts["evidence_items"] = await _scalar(
        session, sa.text("SELECT count(*) FROM evidence_items WHERE source_id IN :ids").bindparams(ids)
    )
    plan.counts["country_facts"] = await _scalar(
        session, sa.text("SELECT count(*) FROM country_facts WHERE source_id IN :ids").bindparams(ids)
    )
    plan.counts["entity_match_candidates"] = await _scalar(
        session,
        sa.text("SELECT count(*) FROM entity_match_candidates WHERE source_id IN :ids").bindparams(ids),
    )
    plan.counts["http_cache_entries"] = await _scalar(
        session, sa.text("SELECT count(*) FROM http_cache_entries WHERE source_id IN :ids").bindparams(ids)
    )
    plan.counts["source_validations"] = await _scalar(
        session, sa.text("SELECT count(*) FROM source_validations WHERE source_id IN :ids").bindparams(ids)
    )
    plan.counts["source_credentials"] = await _scalar(
        session, sa.text("SELECT count(*) FROM source_credentials WHERE source_id IN :ids").bindparams(ids)
    )
    plan.counts["sources"] = len(demo_ids)

    # Derived results. A trend is purged when *every* signal behind it is demo;
    # one with any live signal is left for recomputation instead, because
    # deleting it would discard a conclusion partly built on real evidence.
    plan.counts["trends (all-demo)"] = await _scalar(
        session,
        sa.text(
            "SELECT count(*) FROM trends t WHERE EXISTS ("
            "  SELECT 1 FROM trend_signals ts WHERE ts.trend_id = t.id"
            ") AND NOT EXISTS ("
            "  SELECT 1 FROM trend_signals ts WHERE ts.trend_id = t.id AND ts.source_id NOT IN :ids"
            ")"
        ).bindparams(ids),
    )
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
    plan.counts["opportunities (from all-demo trends)"] = await _scalar(
        session,
        sa.text(
            "SELECT count(*) FROM opportunities o WHERE o.primary_trend_id IN ("
            "  SELECT t.id FROM trends t WHERE EXISTS ("
            "    SELECT 1 FROM trend_signals ts WHERE ts.trend_id = t.id"
            "  ) AND NOT EXISTS ("
            "    SELECT 1 FROM trend_signals ts WHERE ts.trend_id = t.id AND ts.source_id NOT IN :ids"
            "  )"
            ")"
        ).bindparams(ids),
    )

    # Watchlist entries that would block the delete (NO ACTION, not CASCADE).
    plan.watchlist_hits = [
        (str(a), str(b), str(c))
        for a, b, c in (
            await session.execute(
                sa.text(
                    "SELECT w.name, o.title, u.email FROM watchlist_items wi "
                    "JOIN watchlists w ON w.id = wi.watchlist_id "
                    "JOIN users u ON u.id = w.user_id "
                    "JOIN opportunities o ON o.id = wi.opportunity_id "
                    "WHERE o.primary_trend_id IN ("
                    "  SELECT t.id FROM trends t WHERE EXISTS ("
                    "    SELECT 1 FROM trend_signals ts WHERE ts.trend_id = t.id"
                    "  ) AND NOT EXISTS ("
                    "    SELECT 1 FROM trend_signals ts WHERE ts.trend_id=t.id AND ts.source_id NOT IN :ids"
                    "  )"
                    ") ORDER BY u.email, w.name"
                ).bindparams(ids)
            )
        ).all()
    ]
    plan.counts["watchlist_items (demo entries)"] = len(plan.watchlist_hits)

    # Digests whose stored JSON names a demo opportunity. Reported only.
    for did, freq, sections in (
        await session.execute(sa.text("SELECT id, frequency, sections FROM digests"))
    ).all():
        titles = _titles_in(sections)
        if not titles:
            continue
        hits = await _scalar(
            session,
            sa.text(
                "SELECT count(*) FROM opportunities o WHERE o.title IN :titles "
                "AND o.primary_trend_id IN ("
                "  SELECT t.id FROM trends t WHERE EXISTS ("
                "    SELECT 1 FROM trend_signals ts WHERE ts.trend_id = t.id"
                "  ) AND NOT EXISTS ("
                "    SELECT 1 FROM trend_signals ts WHERE ts.trend_id=t.id AND ts.source_id NOT IN :ids"
                "  )"
                ")"
            ).bindparams(ids, sa.bindparam("titles", value=list(titles), expanding=True)),
        )
        if hits:
            plan.dirty_digests.append((str(did), str(freq), hits))

    for table in PRESERVE_TABLES:
        plan.preserved[table] = await _scalar(
            session, sa.text(f"SELECT count(*) FROM {table}")  # noqa: S608 - fixed literals
        )
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


async def _all_demo_trend_ids(session: AsyncSession, ids) -> list[str]:
    """Trends whose every signal is demo, resolved to concrete ids **first**.

    Resolved up front rather than left as a subquery, because the deletes below
    empty `trend_signals`, and the predicate that identifies these trends reads
    `trend_signals`. Evaluated late it matches nothing and silently leaves the
    trends behind — which is exactly the bug this function exists to prevent.
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


async def apply_purge(session: AsyncSession, demo_ids: list[str]) -> None:
    """Delete, in FK-safe order. Caller owns the transaction.

    Ordering rules that are not obvious and were both got wrong once:

    * The all-demo trend set is resolved to ids *before* anything is deleted,
      because the predicate identifying it reads `trend_signals`.
    * `source_runs` is deleted explicitly. Postgres would cascade it from
      `sources`, but `raw_records.source_run_id` is `NO ACTION`, so the cascade
      can be refused; and SQLite does not enforce it at all, which is how a
      missing delete here passed unnoticed on a SQLite rehearsal.
    """
    ids = sa.bindparam("ids", value=demo_ids, expanding=True)

    demo_trend_ids = await _all_demo_trend_ids(session, ids)
    tids = sa.bindparam("tids", value=demo_trend_ids or [""], expanding=True)
    all_demo_trends = "SELECT id FROM trends WHERE id IN :tids"

    # Children with NO ACTION first: the database will not cascade these, so
    # they are removed explicitly or the delete is refused.
    for stmt in (
        f"DELETE FROM watchlist_items WHERE opportunity_id IN "
        f"(SELECT id FROM opportunities WHERE primary_trend_id IN ({all_demo_trends}))",
        f"DELETE FROM user_notes WHERE opportunity_id IN "
        f"(SELECT id FROM opportunities WHERE primary_trend_id IN ({all_demo_trends}))",
        f"DELETE FROM user_decisions WHERE opportunity_id IN "
        f"(SELECT id FROM opportunities WHERE primary_trend_id IN ({all_demo_trends}))",
        f"DELETE FROM alerts WHERE opportunity_id IN "
        f"(SELECT id FROM opportunities WHERE primary_trend_id IN ({all_demo_trends}))",
        f"DELETE FROM predictions WHERE opportunity_id IN "
        f"(SELECT id FROM opportunities WHERE primary_trend_id IN ({all_demo_trends}))",
        f"DELETE FROM model_runs WHERE opportunity_id IN "
        f"(SELECT id FROM opportunities WHERE primary_trend_id IN ({all_demo_trends}))",
        f"DELETE FROM opportunities WHERE primary_trend_id IN ({all_demo_trends})",
        f"DELETE FROM watchlist_items WHERE trend_id IN ({all_demo_trends})",
        "DELETE FROM evidence_items WHERE source_id IN :ids",
        "DELETE FROM trend_signals WHERE source_id IN :ids",
        f"DELETE FROM trends WHERE id IN ({all_demo_trends})",
        "DELETE FROM signal_observations WHERE signal_id IN "
        "(SELECT id FROM signals WHERE source_id IN :ids)",
        "DELETE FROM signals WHERE source_id IN :ids",
        # Before source_runs: raw_records.source_run_id is NO ACTION, so runs
        # cannot go first.
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
    ):
        # Each statement uses one or both placeholders; bind only what it names.
        clause = sa.text(stmt)
        wanted = [p for p, present in ((ids, ":ids" in stmt), (tids, ":tids" in stmt)) if present]
        await session.execute(clause.bindparams(*wanted) if wanted else clause)


def render(plan: Plan, *, applied: bool) -> None:
    head = "PURGE APPLIED" if applied else "DRY RUN — nothing was deleted"
    print(f"\n{'=' * 72}\n{head}\n{'=' * 72}")

    print(f"\nLIVE sources kept ({len(plan.live_sources)}):")
    for slug, adapter, cls in plan.live_sources:
        print(f"  KEEP    {slug:<28} {adapter:<20} {cls}")

    print(f"\nDEMO sources to remove ({len(plan.demo_sources)}):")
    for slug, adapter, cls, reason in plan.demo_sources:
        print(f"  REMOVE  {slug:<28} {adapter:<20} {cls}")
        print(f"          reason: {reason}")

    if plan.unregistered:
        print(
            "\n  NOTE: these were classed demo only because their adapter is not\n"
            "  registered, so their provenance cannot be verified. If any is a real\n"
            "  live source, stop and say so — the rule fails closed on purpose:\n"
            f"    {', '.join(plan.unregistered)}"
        )

    print("\nRows affected:")
    if not plan.counts:
        print("  (no demo sources found — nothing to do)")
    for table, count in plan.counts.items():
        print(f"  {count:>8}  {table}")

    if plan.mixed_trends:
        print(
            f"\n  {plan.mixed_trends} trend(s) draw on BOTH live and demo evidence and are NOT\n"
            "  deleted. Their stored numbers still reflect the demo evidence until you\n"
            "  re-run trend evaluation, which is the intended next step."
        )

    if plan.watchlist_hits:
        print(f"\nWatchlist entries removed ({len(plan.watchlist_hits)}) — lists themselves kept:")
        for wl, title, email in plan.watchlist_hits:
            print(f"  {email:<28} {wl:<20} -> {title}")

    if plan.dirty_digests:
        print(f"\nDigests naming demo opportunities in stored JSON ({len(plan.dirty_digests)}):")
        for did, freq, hits in plan.dirty_digests:
            print(f"  {freq:<8} {did}  ({hits} demo title(s))")
        print(
            "  These are historical snapshots of messages already sent. They are left\n"
            "  exactly as they are: rewriting them would falsify the delivery record."
        )

    print("\nPreserved (asserted unchanged when applying):")
    for table, count in plan.preserved.items():
        print(f"  {count:>8}  {table}")


async def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--apply",
        action="store_true",
        help="Actually delete. Without this the script only reports.",
    )
    parser.add_argument(
        "--database-url",
        default=None,
        help="Override the configured DATABASE_URL.",
    )
    args = parser.parse_args()

    url = args.database_url or get_settings().DATABASE_URL
    shown = url.split("@")[-1] if "@" in url else url
    print(f"database: ...@{shown}")

    engine = create_async_engine(url)
    maker = async_sessionmaker(engine, expire_on_commit=False)

    try:
        async with maker() as session:
            plan = await build_plan(session)

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

            # The planning reads above already opened a transaction on this
            # session; the deletes join it, and it is committed only if the
            # preservation check passes.
            try:
                await apply_purge(session, demo_ids)

                after = {
                    t: await _scalar(session, sa.text(f"SELECT count(*) FROM {t}"))  # noqa: S608
                    for t in PRESERVE_TABLES
                }
                moved = {t: (before[t], after[t]) for t in PRESERVE_TABLES if before[t] != after[t]}
                if moved:
                    # A purge that touched preserved data is a bug, not a result
                    # to be committed.
                    raise RuntimeError(f"preserved tables changed, rolling back: {moved}")
            except Exception:
                await session.rollback()
                raise
            await session.commit()

            final = await build_plan(session)
            render(final, applied=True)
            print("\nNext: re-run trend evaluation and opportunity generation to")
            print("recompute the mixed results from the surviving live evidence.\n")
            return 0
    finally:
        await engine.dispose()


if __name__ == "__main__":
    sys.exit(asyncio.run(main()))
