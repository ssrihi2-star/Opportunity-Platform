"""The skeptic pass: a deliberate attempt to destroy the idea.

This is not a balanced review. Its job is to argue against, list what is missing,
and reduce confidence where the case is thinner than it looks.

Two structural guarantees:

* **It can only reduce confidence.** There is no code path here that raises it.
  `apply` takes a confidence and returns a number that is never larger.
* **It runs on arithmetic first.** Every finding below is derived from stored
  evidence. A language model may later phrase these findings more fluently, but
  it cannot add one, remove one, or change the reduction.

The seventeen questions from the brief are answered explicitly and stored, so a
reader can see which ones were asked and what the evidence said — including the
ones that came back clean.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

from app.analytics.opportunity_config import SKEPTIC_PROMPT_VERSION
from app.analytics.opportunity_scoring import OpportunityInput

#: Asked of every candidate, in this order, and recorded whether or not they bite.
QUESTIONS: tuple[str, ...] = (
    "What could make this thesis completely wrong?",
    "Is this simply hype?",
    "Is adoption real?",
    "Are the sources repeating each other?",
    "Is the opportunity already widely known?",
    "Is the valuation already extreme?",
    "Are customers actually paying?",
    "Is the market large enough?",
    "Is the growth temporary?",
    "Is there a stronger incumbent?",
    "Can competitors copy this easily?",
    "Is regulation a threat?",
    "Is the market accessible?",
    "Is there survivorship bias?",
    "Could the trend succeed while this specific opportunity fails?",
    "What important evidence is missing?",
    "What is the strongest alternative explanation?",
)


@dataclass(slots=True)
class SkepticResult:
    version: str
    strongest_counterargument: str
    counterarguments: list[str] = field(default_factory=list)
    missing_evidence: list[str] = field(default_factory=list)
    red_flags: list[str] = field(default_factory=list)
    alternative_explanations: list[str] = field(default_factory=list)
    manipulation_probability: float = 0.0
    too_late_reasons: list[str] = field(default_factory=list)
    inaccessible_reasons: list[str] = field(default_factory=list)
    confidence_reduction: float = 0.0
    status: str = "continue_research"
    questions_asked: list[str] = field(default_factory=lambda: list(QUESTIONS))

    def apply(self, confidence: float) -> float:
        """Reduce a confidence score. Never raises it — that is the whole point."""
        return max(0.0, min(confidence, confidence - self.confidence_reduction))


def _mean(values: list[float]) -> float | None:
    return sum(values) / len(values) if values else None


def review(
    inp: OpportunityInput,
    *,
    analysis: dict[str, Any],
    missing_evidence: list[str],
    duplication_ratio: float | None = None,
    risk_level: str = "moderate",
) -> SkepticResult:
    """Argue against this candidate as hard as the evidence allows."""
    counters: list[str] = []
    flags: list[str] = []
    alternatives: list[str] = []
    too_late: list[str] = []
    inaccessible: list[str] = []
    reduction = 0.0
    manipulation = 0.0

    adoption = [f for f in inp.facts if f.is_adoption and not f.is_proxy]
    attention = [f for f in inp.facts if f.signal_class == "attention"]
    groups = {f.source_group for f in inp.facts}
    reliability = _mean([f.source_reliability for f in inp.facts]) or 0.0

    # --- is adoption real? ---------------------------------------------------
    if not adoption:
        counters.append(
            "There is no measurement of anyone actually using this. Every number here "
            "describes interest, not use, and interest is free."
        )
        reduction += 15
        flags.append("no adoption evidence")
    else:
        ado = _mean([f.growth_30d for f in adoption if f.growth_30d is not None])
        att = _mean([f.growth_30d for f in attention if f.growth_30d is not None])
        if att is not None and ado is not None and att > ado * 2.5 and att > 30:
            counters.append(
                f"Attention is growing {att:+.0f}% while actual use grows {ado:+.0f}%. "
                "The most likely explanation is a story spreading, not a market forming."
            )
            alternatives.append("A media cycle that will pass, leaving the underlying usage where it was.")
            reduction += 10
            manipulation = max(manipulation, 0.3)

    # --- are the sources repeating each other? -------------------------------
    if len(groups) < 2:
        counters.append(
            "Only one source family supports this. That is one opinion presented as a body of evidence."
        )
        reduction += 12
    if duplication_ratio is not None and duplication_ratio >= 0.4:
        counters.append(
            f"{duplication_ratio:.0%} of the supporting coverage is the same story "
            "republished. Volume of coverage is not corroboration."
        )
        alternatives.append("One press release amplified by outlets that did not check it.")
        reduction += 8
        manipulation = max(manipulation, 0.4)

    if reliability < 0.55:
        counters.append(
            f"Mean source reliability is {reliability:.2f}. The evidence comes mostly from "
            "places that are often wrong."
        )
        reduction += 8

    # --- already known / too late -------------------------------------------
    if inp.trend_stage in {"mainstream", "mature"}:
        too_late.append(f"The trend is already {inp.trend_stage}. Whoever was going to notice has noticed.")
        counters.append(too_late[-1])
        reduction += 8
    if (inp.awareness or "").lower() == "high":
        too_late.append("Mainstream awareness is already high.")
    if inp.competitor_count is not None and inp.competitor_count >= 15:
        too_late.append(f"{inp.competitor_count} providers already serve this market.")
        counters.append(
            f"{inp.competitor_count} competitors are already here. Being right about the "
            "trend does not help if you are the sixteenth to arrive."
        )
        reduction += 6

    # --- valuation -----------------------------------------------------------
    valuation = (analysis.get("valuation") or {}) if isinstance(analysis, dict) else {}
    if isinstance(valuation, dict) and valuation.get("band") == "extreme":
        counters.append(
            "The price already assumes the good outcome. Being right about the business "
            "and wrong about the entry price loses money just as effectively."
        )
        reduction += 10
    if isinstance(valuation, dict) and valuation.get("status") == "NOT ASSESSED":
        counters.append(
            "No current price is available, so nothing here can say whether this is "
            "expensive. A good company at any price is not an investment case."
        )
        reduction += 8

    # --- are customers paying? ----------------------------------------------
    wtp = analysis.get("willingness_to_pay") if isinstance(analysis, dict) else None
    if wtp == "UNKNOWN":
        counters.append(
            "Nobody has been shown to pay for this. Until someone does, the demand is hypothetical."
        )
        reduction += 10

    # --- market size ---------------------------------------------------------
    if (inp.market_size_band or "unknown").lower() in {"tiny", "unknown"}:
        counters.append(
            "The size of this market is not established. It may be real and still be too "
            "small to be worth the years."
        )
        reduction += 6

    # --- temporary growth ----------------------------------------------------
    if inp.trend_history_days < 120:
        counters.append(
            f"Only {inp.trend_history_days} days of history. A rise this short is "
            "indistinguishable from a good quarter."
        )
        alternatives.append("Normal variation that happens to point upwards right now.")
        reduction += 8

    # --- defensibility -------------------------------------------------------
    if not inp.defensibility_markers:
        counters.append("Nothing stops a better-funded competitor doing this next quarter.")
        reduction += 6

    # --- regulation ----------------------------------------------------------
    if "regulatory_uncertainty" in inp.flags:
        counters.append(
            "Certification or approval is unresolved, and that is the kind of thing that "
            "turns a six-month plan into a two-year one."
        )
        reduction += 6

    # --- accessibility -------------------------------------------------------
    if inp.accessibility_barriers:
        inaccessible.extend(inp.accessibility_barriers)
    if inp.capital_required_usd and inp.capital_required_usd > 100_000:
        inaccessible.append(
            f"About {inp.capital_required_usd:,.0f} of capital is needed before anything works."
        )

    # --- survivorship --------------------------------------------------------
    counters.append(
        "The evidence describes the things that grew. The ones that tried the same and "
        "failed leave no trail in these sources, so the picture is flattering by construction."
    )

    # --- trend succeeds, opportunity fails -----------------------------------
    counters.append(
        "The trend can be entirely real and this particular way of participating can still "
        "fail — because the value accrues to someone else in the chain."
    )
    alternatives.append(
        "The trend is real and the profit goes to incumbents, suppliers or platform owners "
        "rather than to a new entrant."
    )

    # --- manipulation --------------------------------------------------------
    if "promotional_manipulation" in inp.flags:
        manipulation = max(manipulation, 0.6)
        flags.append("promotional or coordinated coverage")
    if "anonymous_team" in inp.flags:
        manipulation = max(manipulation, 0.5)
        flags.append("anonymous team")
    if "concentrated_ownership" in inp.flags:
        flags.append("concentrated ownership")
    if manipulation >= 0.5:
        reduction += 12
        # A manipulation finding outranks every other objection, so it leads.
        counters.insert(
            0,
            "The visible enthusiasm here may be manufactured: "
            + ", ".join(flags)
            + ". If that is what this is, every other number on the page is downstream "
            "of someone's marketing budget.",
        )

    reduction = min(reduction, 60.0)

    # --- status --------------------------------------------------------------
    if manipulation >= 0.6 or risk_level == "very_high" and not adoption:
        status = "possible_manipulation" if manipulation >= 0.6 else "reject"
    elif not adoption:
        status = "insufficient_evidence"
    elif reduction >= 40:
        status = "high_speculation"
    elif too_late:
        status = "watch_only"
    elif reduction <= 12 and adoption and len(groups) >= 2:
        status = "strong_evidence"
    else:
        status = "continue_research"

    strongest = (
        counters[0]
        if counters
        else (
            "No decisive objection was found in the stored evidence, which is itself a reason "
            "to look harder rather than a clean bill of health."
        )
    )

    return SkepticResult(
        version=SKEPTIC_PROMPT_VERSION,
        strongest_counterargument=strongest,
        counterarguments=counters,
        missing_evidence=list(missing_evidence),
        red_flags=flags,
        alternative_explanations=alternatives,
        manipulation_probability=round(manipulation, 2),
        too_late_reasons=too_late,
        inaccessible_reasons=inaccessible,
        confidence_reduction=round(reduction, 2),
        status=status,
    )
