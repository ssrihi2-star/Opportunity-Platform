"""The eligibility gate: nothing becomes an opportunity on a single signal."""

from __future__ import annotations

from dataclasses import dataclass

from app.core.config import settings


@dataclass(slots=True)
class GateResult:
    passed: bool
    reasons: list[str]


def check_gate(
    *,
    distinct_signal_types: int,
    independent_sources: int,
    confidence: float,
    newest_evidence_age_days: int,
    maturity_stage: str,
    blocking_flags: list[str],
) -> GateResult:
    reasons: list[str] = []
    if distinct_signal_types < settings.MIN_SIGNAL_TYPES:
        reasons.append(
            f"only {distinct_signal_types} distinct signal type(s), need {settings.MIN_SIGNAL_TYPES}"
        )
    if independent_sources < settings.MIN_INDEPENDENT_SOURCES:
        reasons.append(
            f"only {independent_sources} independent source(s), need {settings.MIN_INDEPENDENT_SOURCES}"
        )
    if confidence < settings.MIN_CONFIDENCE:
        reasons.append(f"confidence {confidence:.2f} below {settings.MIN_CONFIDENCE:.2f}")
    if newest_evidence_age_days > settings.MAX_EVIDENCE_AGE_DAYS:
        reasons.append(
            f"newest evidence is {newest_evidence_age_days} days old, limit is "
            f"{settings.MAX_EVIDENCE_AGE_DAYS}"
        )
    if maturity_stage in {"mature", "declining"}:
        reasons.append(f"maturity stage '{maturity_stage}' is already priced in")
    if blocking_flags:
        reasons.append("blocking risk flag(s): " + ", ".join(sorted(blocking_flags)))
    return GateResult(passed=not reasons, reasons=reasons)
