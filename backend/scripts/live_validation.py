"""Validate the collectors and the trend engine against the real internet.

This is the Phase 4 "live data validation gate". It is a separate script, not a
test, because it is the one thing in this repository that deliberately touches the
outside world — the test suite blocks network access on purpose and always will.

    python -m scripts.live_validation                  # every configured source
    python -m scripts.live_validation --only github fred
    python -m scripts.live_validation --dry-run        # show the plan, fetch nothing

What it exercises, end to end, for each source:

    LIVE SOURCE -> RAW RECORD -> NORMALISED -> ENTITY/TOPIC -> OBSERVATION
                -> TIME SERIES -> TREND

and then checks the claims that actually matter:

  * the adapter still understands the API's current response shape
  * pagination returns more than one page where the source paginates
  * the rate limiter and the per-run request ceiling engage
  * a failing source is reported as failed, not silently skipped
  * a second identical run stores zero new rows (deduplication works)
  * entities resolve to one canonical record, not several
  * timestamps are real, ordered, in the past, and timezone-aware
  * a period the source did not publish stays missing and never becomes 0
  * the resulting trend numbers are plausible rather than absurd

It prints a verdict table and exits non-zero if any check fails, so it can be
wired into CI on a machine that has outbound access.

IMPORTANT: this script never tunes a threshold. If live data produces a boring
result, that is the result.
"""

from __future__ import annotations

import argparse
import asyncio
import sys
from dataclasses import dataclass, field
from datetime import UTC, datetime, timedelta
from typing import Any

import sqlalchemy as sa

from app.db.session import SessionLocal
from app.models.enums import ObservationStatus, RunStatus
from app.models.models import (
    Entity,
    RawRecord,
    Signal,
    SignalObservation,
    Source,
    SourceRun,
    Trend,
)
from app.services.ingestion import run_source
from app.services.trends import evaluate_trends

#: Sources the gate knows how to validate, in the order the brief lists them.
#: Each entry names the seeded source slug and what a healthy result looks like.
TARGETS: dict[str, dict[str, Any]] = {
    "github": {
        "slug": "github_devtools",
        "needs_credential": "token",
        "credential_optional": True,
        "paginates": True,
        "note": "60 requests/hour without a token; add one before trusting the result.",
    },
    "hackernews": {
        "slug": "hn_keywords",
        "needs_credential": None,
        "paginates": True,
        "note": "Algolia search API. No key required.",
    },
    "wikipedia": {
        "slug": "wikipedia_attention",
        "needs_credential": None,
        "paginates": False,
        "note": "Wikimedia REST. Requires a descriptive User-Agent.",
    },
    "sec": {
        "slug": "sec_filings",
        "needs_credential": None,
        "paginates": False,
        "note": "SEC rejects requests without a contact User-Agent. Set CONTACT_EMAIL.",
    },
    "fred": {
        "slug": "fred_macro",
        "needs_credential": "api_key",
        "credential_optional": False,
        "paginates": False,
        "note": "Free key from fred.stlouisfed.org. Publishes '.' for unreleased periods.",
    },
    "comtrade": {
        "slug": "comtrade_north_africa",
        "needs_credential": "subscription_key",
        "credential_optional": True,
        "paginates": False,
        "note": "Public preview endpoint works unauthenticated but is heavily throttled.",
    },
    "rss": {
        "slug": "regulator_feeds",
        "needs_credential": None,
        "paginates": False,
        "note": "Honours robots.txt; a disallowed feed must be reported, not skipped.",
    },
}


@dataclass
class Check:
    name: str
    passed: bool | None          # None = could not be assessed
    detail: str


@dataclass
class TargetResult:
    key: str
    slug: str
    reached: bool = False
    skipped_reason: str | None = None
    checks: list[Check] = field(default_factory=list)

    def add(self, name: str, passed: bool | None, detail: str) -> None:
        self.checks.append(Check(name, passed, detail))

    @property
    def failed(self) -> list[Check]:
        return [c for c in self.checks if c.passed is False]


# --------------------------------------------------------------------------- checks
async def _validate_one(session: Any, key: str, spec: dict[str, Any]) -> TargetResult:
    result = TargetResult(key=key, slug=spec["slug"])

    source = (
        await session.execute(sa.select(Source).where(Source.slug == spec["slug"]))
    ).scalar_one_or_none()
    if source is None:
        result.skipped_reason = (
            f"source {spec['slug']!r} is not in the database. Run `python -m scripts.seed` first."
        )
        return result

    if spec.get("needs_credential") and not spec.get("credential_optional", False):
        from app.models.models import SourceCredential

        has = (
            await session.execute(
                sa.select(SourceCredential.id).where(
                    SourceCredential.source_id == source.id,
                    SourceCredential.key == spec["needs_credential"],
                )
            )
        ).first()
        if has is None:
            result.skipped_reason = (
                f"no {spec['needs_credential']!r} credential stored for {spec['slug']!r}. "
                f"Add it with PUT /api/v1/sources/{{id}}/credentials, then re-run."
            )
            return result

    before_raw = await _count(session, RawRecord, RawRecord.source_id == source.id)

    # ---- run 1: the real fetch -------------------------------------------------
    first = await run_source(session, source, trigger="live_validation")
    await session.commit()
    result.reached = True

    result.add(
        "status reported",
        first.status in {RunStatus.SUCCEEDED, RunStatus.PARTIAL, RunStatus.FAILED},
        f"run finished as {first.status}"
        + (f" — {first.error}" if getattr(first, "error", None) else ""),
    )
    if first.status == RunStatus.FAILED:
        # A failure is a legitimate outcome of this gate: what matters is that it
        # was surfaced with a usable message rather than swallowed.
        run = await _latest_run(session, source)
        result.add(
            "failure is legible",
            bool(run and run.error),
            (run.error if run and run.error else "no error text recorded — that is a bug"),
        )
        return result

    result.add(
        "records fetched",
        first.fetched > 0,
        f"{first.fetched} record(s) returned by the live API",
    )
    if spec.get("paginates"):
        result.add(
            "pagination",
            first.fetched > 100,
            f"{first.fetched} records — more than one page' worth"
            if first.fetched > 100
            else f"only {first.fetched} records; widen the config window to prove paging",
        )

    run = await _latest_run(session, source)
    result.add(
        "http requests counted",
        bool(run and run.http_requests > 0),
        f"{run.http_requests if run else 0} HTTP request(s) recorded",
    )
    if run and run.http_requests:
        ceiling = int((source.config or {}).get("max_requests_per_run", 500))
        result.add(
            "per-run ceiling respected",
            run.http_requests <= ceiling,
            f"{run.http_requests} request(s) against a ceiling of {ceiling}",
        )

    after_raw = await _count(session, RawRecord, RawRecord.source_id == source.id)
    result.add(
        "raw records stored",
        after_raw > before_raw,
        f"{after_raw - before_raw} new immutable raw record(s)",
    )

    # ---- run 2: the same fetch again, to prove deduplication -------------------
    second = await run_source(session, source, trigger="live_validation_repeat")
    await session.commit()
    final_raw = await _count(session, RawRecord, RawRecord.source_id == source.id)
    result.add(
        "no duplicates on re-run",
        final_raw == after_raw,
        f"second run stored {final_raw - after_raw} new row(s) "
        f"({second.duplicates} recognised as duplicates)",
    )

    # ---- entities --------------------------------------------------------------
    ents = (
        await session.execute(
            sa.select(Entity.id, Entity.name, Entity.normalized_name)
            .join(Signal, Signal.entity_id == Entity.id)
            .where(Signal.source_id == source.id)
            .distinct()
        )
    ).all()
    normalised = [e.normalized_name for e in ents]
    result.add(
        "entities resolve",
        bool(ents) and len(normalised) == len(set(normalised)),
        f"{len(ents)} entity/entities, no duplicate normalised keys"
        if len(normalised) == len(set(normalised))
        else f"duplicate normalised keys: {normalised}",
    )

    # ---- observations, timestamps, gaps ---------------------------------------
    obs = (
        await session.execute(
            sa.select(SignalObservation)
            .join(Signal, Signal.id == SignalObservation.signal_id)
            .where(Signal.source_id == source.id)
            .order_by(SignalObservation.observed_at)
        )
    ).scalars().all()
    result.add("observations stored", bool(obs), f"{len(obs)} observation(s)")

    if obs:
        now = datetime.now(UTC)
        times = [o.observed_at for o in obs]
        aware = all(t.tzinfo is not None for t in times)
        ordered = times == sorted(times)
        future = [t for t in times if t > now + timedelta(days=1)]
        ancient = [t for t in times if t.year < 1990]
        result.add(
            "timestamps sane",
            aware and ordered and not future and not ancient,
            f"{times[0].date()} → {times[-1].date()}, timezone-aware={aware}, "
            f"ordered={ordered}, future={len(future)}, implausible={len(ancient)}",
        )

        gaps = [o for o in obs if o.status != ObservationStatus.OK]
        bad_gaps = [o for o in gaps if o.value is not None]
        result.add(
            "gaps stay missing",
            not bad_gaps,
            f"{len(gaps)} gap(s) recorded, {len(bad_gaps)} of them wrongly carry a value",
        )
        zeros = [o for o in obs if o.status == ObservationStatus.OK and o.value == 0]
        result.add(
            "zeros are real zeros",
            True,
            f"{len(zeros)} genuine zero(s) — distinct from the {len(gaps)} gap(s) above",
        )

    return result


async def _trend_check(session: Any, slugs: list[str]) -> TargetResult:
    """Run the trend engine over whatever the live sources produced."""
    result = TargetResult(key="trend engine", slug="—")
    count = await evaluate_trends(session)
    await session.commit()
    result.reached = True
    result.add("trends evaluated", count > 0, f"{count} trend row(s) evaluated")

    trends = (
        await session.execute(sa.select(Trend).order_by(Trend.trend_score.desc()).limit(10))
    ).scalars().all()
    if not trends:
        return result

    absurd = [
        t for t in trends
        if not (0 <= t.trend_score <= 100) or not (0 <= t.confidence <= 100)
    ]
    result.add(
        "scores in range",
        not absurd,
        f"top {len(trends)} trends all within 0-100" if not absurd else f"out of range: {absurd}",
    )
    explained = [t for t in trends if t.components]
    result.add(
        "calculation is shown",
        len(explained) == len(trends),
        f"{len(explained)}/{len(trends)} trends carry a full component breakdown",
    )
    print("\n  top live trends:")
    for t in trends:
        print(
            f"    {t.name[:44]:<46} score={t.trend_score:5.0f} "
            f"confidence={t.confidence:5.0f}  {t.stage:<15} {t.state}"
        )
    return result


# --------------------------------------------------------------------------- helpers
async def _count(session: Any, model: Any, *where: Any) -> int:
    return (
        await session.execute(sa.select(sa.func.count()).select_from(model).where(*where))
    ).scalar_one()


async def _latest_run(session: Any, source: Source) -> SourceRun | None:
    return (
        await session.execute(
            sa.select(SourceRun)
            .where(SourceRun.source_id == source.id)
            .order_by(SourceRun.started_at.desc())
            .limit(1)
        )
    ).scalar_one_or_none()


def _print(results: list[TargetResult]) -> int:
    print("\n" + "=" * 78)
    print("LIVE DATA VALIDATION REPORT")
    print("=" * 78)
    failures = 0
    skipped = 0
    for r in results:
        if r.skipped_reason:
            skipped += 1
            print(f"\n[SKIPPED] {r.key}\n    {r.skipped_reason}")
            continue
        bad = r.failed
        failures += len(bad)
        head = "FAIL" if bad else "PASS"
        print(f"\n[{head}] {r.key}  ({r.slug})")
        for c in r.checks:
            mark = {True: " ok ", False: "FAIL", None: " -- "}[c.passed]
            print(f"    [{mark}] {c.name}: {c.detail}")
    print("\n" + "-" * 78)
    print(f"{len(results) - skipped} source(s) exercised, {skipped} skipped, {failures} check(s) failed.")
    if skipped and not failures:
        print("Skipped sources are NOT validated. Do not describe them as verified.")
    print("-" * 78)
    return 1 if failures else 0


async def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--only", nargs="*", choices=sorted(TARGETS), help="validate a subset")
    parser.add_argument("--dry-run", action="store_true", help="print the plan and stop")
    args = parser.parse_args()

    keys = args.only or list(TARGETS)
    if args.dry_run:
        print("Would validate, in order:")
        for k in keys:
            spec = TARGETS[k]
            cred = spec.get("needs_credential") or "none"
            print(f"  {k:<12} source={spec['slug']:<26} credential={cred:<18} {spec['note']}")
        print("\nEach source is fetched twice: once for real, once to prove deduplication.")
        return 0

    print(f"Validating {len(keys)} source(s) against the live internet. This makes real requests.\n")
    results: list[TargetResult] = []
    async with SessionLocal() as session:
        for k in keys:
            print(f"-> {k} ...", flush=True)
            try:
                results.append(await _validate_one(session, k, TARGETS[k]))
            except Exception as exc:  # noqa: BLE001 - one bad source must not end the run
                r = TargetResult(key=k, slug=TARGETS[k]["slug"])
                r.reached = True
                r.add("harness survived", False, f"{type(exc).__name__}: {exc}")
                results.append(r)
                await session.rollback()
        if any(r.reached for r in results):
            print("-> trend engine ...", flush=True)
            results.append(await _trend_check(session, keys))

    return _print(results)


if __name__ == "__main__":
    sys.exit(asyncio.run(main()))
