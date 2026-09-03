"""Risk assessment across eleven categories, with critical risks dominating.

The overall level is deliberately **not** an average. Averaging is how a fatal
flaw gets diluted by nine comfortable ones: a company with excellent margins, a
strong position and a pending fraud investigation is not "moderate risk".

Each risk carries severity (how bad), confidence (how sure we are it is real),
the evidence behind it, an explanation, and a mitigation where one honestly
exists. A risk we are not sure about is still listed — at lower confidence.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

from app.analytics.opportunity_config import CRYPTO_MIN_RISK, RISK_VERSION

#: Order matters: this is the ladder used for "at least this risky".
LEVELS: tuple[str, ...] = ("low", "moderate", "high", "very_high")

SEVERITY_RANK: dict[str, int] = {"info": 0, "low": 1, "medium": 2, "high": 3, "blocking": 4}


@dataclass(slots=True)
class Risk:
    code: str
    category: str
    severity: str
    confidence: float
    rationale: str
    evidence_ids: list[str] = field(default_factory=list)
    mitigation: str | None = None

    @property
    def is_blocking(self) -> bool:
        return self.severity == "blocking"


@dataclass(slots=True)
class RiskAssessment:
    version: str
    level: str
    risks: list[Risk]
    reasoning: str
    floor_applied: str | None = None


def _at_least(level: str, floor: str) -> str:
    return level if LEVELS.index(level) >= LEVELS.index(floor) else floor


def assess_risk(
    *,
    opportunity_type: str,
    hints: list[dict[str, Any]],
    flags: set[str],
    metrics: dict[str, Any],
    min_level: str | None = None,
) -> RiskAssessment:
    """Turn analyzer hints and flags into a categorised, dominated risk picture."""
    risks: list[Risk] = []

    for hint in hints:
        risks.append(
            Risk(
                code=hint["code"],
                category=hint.get("category", "market"),
                severity=hint.get("severity", "medium"),
                confidence=float(hint.get("confidence", 0.7)),
                rationale=hint["rationale"],
                evidence_ids=list(hint.get("evidence_ids") or []),
                mitigation=hint.get("mitigation"),
            )
        )

    # Flags raised anywhere in the pipeline become risks in their own right, so
    # that a penalty is never applied without a matching, explained risk row.
    flag_map: dict[str, tuple[str, str, str]] = {
        "single_source_dependence": (
            "market",
            "medium",
            "Everything rests on one source family; if it is wrong, the whole case is wrong.",
        ),
        "low_quality_evidence": (
            "market",
            "medium",
            "The sources behind this have low measured reliability.",
        ),
        "attention_without_adoption": (
            "market",
            "high",
            "Interest is growing much faster than any evidence of real use.",
        ),
        "weak_adoption_evidence": (
            "market",
            "high",
            "Nothing here measures anyone actually using this.",
        ),
        "extreme_valuation": (
            "financial",
            "high",
            "The price already assumes the good outcome.",
        ),
        "extreme_competition": (
            "competition",
            "high",
            "Many established providers already serve this.",
        ),
        "easily_copied": (
            "competition",
            "medium",
            "Nothing recorded prevents a competitor copying this.",
        ),
        "regulatory_uncertainty": (
            "regulatory",
            "high",
            "Approval, certification or licensing is unresolved.",
        ),
        "supply_chain_fragility": (
            "supply_chain",
            "high",
            "Production is concentrated in one place.",
        ),
        "single_supplier": (
            "supply_chain",
            "high",
            "One supplier can end this by changing their mind.",
        ),
        "single_customer": (
            "customer_concentration",
            "high",
            "One customer can end this by leaving.",
        ),
        "poor_liquidity": (
            "liquidity",
            "high",
            "Getting out may not be possible at a sensible price.",
        ),
        "concentrated_ownership": (
            "liquidity",
            "high",
            "A few holders can move the price at will.",
        ),
        "anonymous_team": (
            "fraud_manipulation",
            "high",
            "Nobody identifiable is accountable for this.",
        ),
        "promotional_manipulation": (
            "fraud_manipulation",
            "high",
            "The visible enthusiasm may be paid or coordinated.",
        ),
        "poor_economics": (
            "financial",
            "high",
            "The unit economics do not currently work.",
        ),
        "excessive_capital": (
            "execution",
            "medium",
            "The capital required is large in absolute terms, which narrows who "
            "anywhere in the world could realistically take part.",
        ),
        "difficult_local_execution": (
            "geographic",
            "medium",
            "Doing this in the target market is materially harder than elsewhere.",
        ),
        "already_mainstream": (
            "market",
            "medium",
            "The window this depended on has probably closed.",
        ),
        "tiny_market": ("market", "medium", "Even complete success would be small."),
    }
    seen = {r.code for r in risks}
    for flag in sorted(flags):
        if flag in flag_map and flag not in seen:
            category, severity, rationale = flag_map[flag]
            risks.append(
                Risk(code=flag, category=category, severity=severity, confidence=0.7, rationale=rationale)
            )

    # Technology risk is implicit in an early-stage technical trend.
    if metrics.get("trend_stage") in {"weak_signal", "emerging"}:
        risks.append(
            Risk(
                code="unproven_technology",
                category="technology",
                severity="medium",
                confidence=0.6,
                rationale="The underlying technology is early enough that it may not work out.",
                mitigation="Wait for a second independent deployment before committing.",
            )
        )

    # ---- the dominated overall level ---------------------------------------
    blocking = [r for r in risks if r.is_blocking]
    high = [r for r in risks if r.severity == "high" and r.confidence >= 0.5]
    medium = [r for r in risks if r.severity == "medium"]

    if blocking:
        level = "very_high"
        reasoning = (
            f"A blocking risk dominates everything else: {blocking[0].rationale} "
            "No number of comfortable findings offsets this."
        )
    elif len(high) >= 3:
        level = "very_high"
        reasoning = (
            f"{len(high)} separate high-severity risks. Individually survivable; together "
            "they describe something that goes wrong in several ways at once."
        )
    elif high:
        level = "high"
        reasoning = f"Driven by the most serious finding rather than the average: {high[0].rationale}"
    elif len(medium) >= 3:
        level = "moderate"
        reasoning = f"{len(medium)} moderate risks and no severe one."
    elif medium:
        level = "moderate"
        reasoning = f"One moderate risk: {medium[0].rationale}"
    else:
        level = "low"
        reasoning = "No material risk was identified in the stored evidence."

    floor_applied = None
    floor = min_level or (CRYPTO_MIN_RISK if opportunity_type == "crypto" else None)
    if floor:
        raised = _at_least(level, floor)
        if raised != level:
            floor_applied = floor
            reasoning += (
                f" Raised to {raised} by the {opportunity_type} floor: this asset class is "
                "never classified below that, regardless of how the evidence reads."
            )
            level = raised
        elif opportunity_type == "crypto":
            floor_applied = floor

    return RiskAssessment(
        version=RISK_VERSION,
        level=level,
        risks=sorted(risks, key=lambda r: (-SEVERITY_RANK[r.severity], r.code)),
        reasoning=reasoning,
        floor_applied=floor_applied,
    )
