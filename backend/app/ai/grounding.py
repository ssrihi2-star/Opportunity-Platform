"""Report grounding: a claim without evidence is not allowed to look like a fact."""

from __future__ import annotations

import re
from dataclasses import dataclass

from app.core.config import settings
from app.core.errors import UngroundedReportError

HEDGE_PHRASES = {
    "unknown",
    "insufficient evidence",
    "estimate",
    "unverified",
    "requires additional research",
}

# A sentence is "factual" if it asserts something checkable: a number, a date, a
# proper noun, a currency amount or a percentage.
_FACTUAL_RE = re.compile(r"(\d|\b[A-Z][a-z]{2,}\b|%|\$|€)")


@dataclass(slots=True)
class GroundingResult:
    citation_density: float
    uncited_claims: list[str]
    unknown_evidence_ids: list[str]


def is_factual(sentence: str) -> bool:
    lowered = sentence.lower().strip()
    if any(h in lowered for h in HEDGE_PHRASES):
        return False
    return bool(_FACTUAL_RE.search(sentence))


def verify_report(
    claims: list[dict], allowed_evidence_ids: set[str], *, min_density: float | None = None
) -> GroundingResult:
    """Validate a generated report's claims.

    `claims` is a list of {"text": str, "evidence_ids": [str], "claim_type": str}.
    Raises UngroundedReportError if any citation is fabricated or if too few
    factual sentences carry evidence. The report is discarded, never patched.
    """
    threshold = settings.MIN_CITATION_DENSITY if min_density is None else min_density

    unknown: list[str] = []
    uncited: list[str] = []
    factual_total = 0
    factual_cited = 0

    for claim in claims:
        text = str(claim.get("text", ""))
        ids = [str(i) for i in claim.get("evidence_ids", [])]
        for evidence_id in ids:
            if evidence_id not in allowed_evidence_ids:
                unknown.append(evidence_id)
        if claim.get("claim_type") == "unverified" or not is_factual(text):
            continue
        factual_total += 1
        if ids:
            factual_cited += 1
        else:
            uncited.append(text)

    if unknown:
        raise UngroundedReportError(
            "The generated report cited evidence that does not exist for this opportunity: "
            + ", ".join(sorted(set(unknown)))
            + ". The report has been discarded. Regenerate it after checking the evidence set."
        )

    density = 1.0 if factual_total == 0 else round(factual_cited / factual_total, 4)
    if density < threshold:
        raise UngroundedReportError(
            f"Only {density:.0%} of factual statements carry evidence (minimum "
            f"{threshold:.0%}). Uncited: " + " | ".join(uncited[:5])
        )
    return GroundingResult(density, uncited, unknown)
