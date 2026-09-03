"""The opportunity engine: gate, score, penalties, confidence, lifecycle.

Three things are kept rigorously apart, and the reason matters:

* **Trend Score** (Phase 3) — *something is changing.*
* **Opportunity Score** — *there appears to be a way to act on that change.*
* **Confidence** — *how much of this can be relied on.*

A trend is not an opportunity. The single most important behaviour in this module
is that `evaluate` is allowed to return `None`: a real, fast, well-evidenced trend
with no accessible way to participate produces no opportunity at all, and that is
a successful outcome rather than a failure to find one.

Nothing here calls a language model. Every number is arithmetic over stored
evidence, and every component carries the sentence that explains it so the UI can
show the whole calculation.
"""

from __future__ import annotations

import math
from dataclasses import dataclass, field
from typing import Any

from app.analytics.opportunity_config import (
    COMPONENT_MAX,
    CONFIDENCE_MAX,
    CONFIDENCE_PENALTY,
    DISQUALIFYING_TREND_FLAGS,
    ELIGIBLE_TREND_STATES,
    GATE,
    MAX_TOTAL_PENALTY,
    OPPORTUNITY_VERSION,
    PENALTY_POINTS,
    REQUIRED_EVIDENCE,
    STATE_THRESHOLDS,
)


# --------------------------------------------------------------------- inputs
@dataclass(slots=True)
class EvidenceFact:
    """One measured thing the engine is allowed to reason about.

    It is deliberately flat and pre-computed: the scorer never queries the
    database, which is what makes it a pure function and therefore testable.
    """

    signal_type: str
    signal_class: str
    source_group: str
    source_reliability: float
    is_proxy: bool
    is_adoption: bool
    growth_30d: float | None = None
    growth_90d: float | None = None
    latest_value: float | None = None
    observation_count: int = 0
    days_since_latest: int = 0
    geo_scope: str = "global"
    currency: str | None = None


@dataclass(slots=True)
class OpportunityInput:
    """Everything the scorer may look at, already gathered."""

    opportunity_type: str
    geo_scope: str = "global"
    country: str | None = None
    industry: str | None = None

    # the underlying trend
    trend_score: float = 0.0
    trend_confidence: float = 0.0
    trend_stage: str = "weak_signal"
    trend_state: str = "candidate"
    trend_is_spike: bool = False
    trend_is_seasonal: bool = False
    trend_history_days: int = 0
    trend_observation_count: int = 0
    trend_missing_count: int = 0

    facts: list[EvidenceFact] = field(default_factory=list)

    # ---- qualitative inputs, supplied by the type-specific analyzer -------
    # Every one of these defaults to "we do not know", and "we do not know"
    # never earns points. Inventing a market size is the failure mode this
    # whole design exists to prevent.
    market_size_band: str | None = None  # tiny | small | medium | large | unknown
    market_size_evidence: str | None = None
    local_penetration: str | None = None  # none | low | medium | high | unknown
    competitor_count: int | None = None
    awareness: str | None = None  # low | medium | high | unknown
    capital_required_usd: float | None = None
    capital_evidence: str | None = None
    accessibility_barriers: list[str] = field(default_factory=list)
    defensibility_markers: list[str] = field(default_factory=list)
    catalyst: str | None = None
    catalyst_is_dated: bool = False
    catalyst_evidence_id: str | None = None
    technical_difficulty: str | None = None  # low | medium | high | unknown

    # ---- flags raised by the analyzers -----------------------------------
    flags: set[str] = field(default_factory=set)

    # ---- evidence completeness -------------------------------------------
    evidence_kinds_present: set[str] = field(default_factory=set)
    assumption_count: int = 0
    has_commercial_data: bool = False


@dataclass(slots=True)
class GateResult:
    passed: bool
    reasons: list[str]
    metrics: dict[str, Any]


@dataclass(slots=True)
class ScoreResult:
    version: str
    raw_score: float
    penalty_total: float
    opportunity_score: float
    components: dict[str, Any]
    penalties: dict[str, float]
    warnings: list[str]
    metrics: dict[str, Any]


# ----------------------------------------------------------------------- gate
def check_gate(inp: OpportunityInput) -> GateResult:
    """Decide whether this trend has earned the right to be examined at all.

    Fails closed and explains itself. A refusal here is the normal case.
    """
    reasons: list[str] = []

    types = {f.signal_type for f in inp.facts}
    classes = {f.signal_class for f in inp.facts}
    groups = {f.source_group for f in inp.facts}
    non_proxy_classes = {f.signal_class for f in inp.facts if not f.is_proxy}
    proxy_weight = sum(1 for f in inp.facts if f.is_proxy)
    proxy_share = proxy_weight / len(inp.facts) if inp.facts else 1.0
    missing_ratio = (
        inp.trend_missing_count / inp.trend_observation_count if inp.trend_observation_count else 1.0
    )

    if inp.trend_is_spike:
        reasons.append(
            "The underlying movement rests on a single observation, so there is nothing here to build on."
        )
    if inp.trend_is_seasonal:
        reasons.append("The underlying rise repeats every year. A season is not a new opportunity.")
    for flag in DISQUALIFYING_TREND_FLAGS & inp.flags:
        reasons.append(f"Disqualifying flag on the underlying trend: {flag.replace('_', ' ')}.")

    if inp.trend_state not in ELIGIBLE_TREND_STATES:
        reasons.append(
            f"The underlying trend is only a {inp.trend_state}; it has not been corroborated "
            "well enough to look for an action yet."
        )
    if len(types) < GATE["min_signal_types"]:
        reasons.append(
            f"{len(types)} distinct signal type(s); {int(GATE['min_signal_types'])} are required. "
            "Several measurements of the same thing count once."
        )
    if len(classes) < GATE["min_signal_classes"]:
        reasons.append(
            f"All the evidence is of {len(classes)} kind(s). Different kinds of evidence — "
            "developer activity, trade flow, commercial data — are what make a case."
        )
    if len(groups) < GATE["min_independent_sources"]:
        reasons.append(
            f"{len(groups)} independent source famil(y/ies); "
            f"{int(GATE['min_independent_sources'])} are required."
        )
    if inp.trend_score < GATE["min_trend_score"]:
        reasons.append(
            f"Trend score {inp.trend_score:.0f} is below the minimum of {GATE['min_trend_score']:.0f}."
        )
    if inp.trend_confidence < GATE["min_trend_confidence"]:
        reasons.append(
            f"Trend confidence {inp.trend_confidence:.0f} is below the minimum of "
            f"{GATE['min_trend_confidence']:.0f}."
        )
    if inp.trend_observation_count < GATE["min_observations"]:
        reasons.append(
            f"Only {inp.trend_observation_count} observations; {int(GATE['min_observations'])} are required."
        )
    if inp.trend_history_days < GATE["min_history_days"]:
        reasons.append(
            f"Only {inp.trend_history_days} days of history; {int(GATE['min_history_days'])} are required."
        )
    if missing_ratio > GATE["max_missing_ratio"]:
        reasons.append(
            f"{missing_ratio:.0%} of the periods were never reported, which is too many to judge from."
        )
    if proxy_share > GATE["max_proxy_share"] or not non_proxy_classes:
        reasons.append(
            f"{proxy_share:.0%} of the evidence is a proxy measurement. A proxy can corroborate "
            "a case; it cannot carry one."
        )

    return GateResult(
        passed=not reasons,
        reasons=reasons,
        metrics={
            "signal_types": len(types),
            "signal_classes": len(classes),
            "independent_sources": len(groups),
            "proxy_share": round(proxy_share, 3),
            "missing_ratio": round(missing_ratio, 3),
            "adoption_facts": sum(1 for f in inp.facts if f.is_adoption),
        },
    )


# -------------------------------------------------------------------- helpers
def _mean(values: list[float]) -> float | None:
    return sum(values) / len(values) if values else None


def _band(value: float, edges: list[tuple[float, float]]) -> float:
    """Piecewise-linear band lookup; returns the fraction for the first match."""
    for threshold, fraction in edges:
        if value >= threshold:
            return fraction
    return 0.0


# ------------------------------------------------------------------ components
def _real_adoption(inp: OpportunityInput) -> tuple[float, str]:
    """Real use, not attention. Attention scores zero here, deliberately."""
    maximum = COMPONENT_MAX["real_adoption"]
    adoption = [f for f in inp.facts if f.is_adoption and not f.is_proxy]
    if not adoption:
        attention = [f for f in inp.facts if f.signal_class == "attention"]
        why = (
            "No adoption evidence at all — the case rests on attention, which does not count as use."
            if attention
            else "No adoption evidence: nothing here measures anyone actually using it."
        )
        return 0.0, why

    growths = [f.growth_30d for f in adoption if f.growth_30d is not None]
    mean_growth = _mean(growths)
    if mean_growth is None:
        return (
            maximum * 0.25,
            f"{len(adoption)} adoption measurement(s) exist, but none has enough history "
            "to show a rate of change yet.",
        )

    # Breadth matters as much as rate: one growing number is an anecdote.
    breadth = min(1.0, len(adoption) / 3)
    rate = _band(
        mean_growth,
        [(100.0, 1.0), (50.0, 0.85), (25.0, 0.7), (10.0, 0.5), (3.0, 0.3), (0.0, 0.15)],
    )
    points = maximum * (0.6 * rate + 0.4 * breadth)
    kinds = ", ".join(sorted({f.signal_type.replace("_", " ") for f in adoption})[:3])
    return (
        round(points, 2),
        f"Real use is growing {mean_growth:+.1f}% across {len(adoption)} adoption measurement(s) ({kinds}).",
    )


def _market_potential(inp: OpportunityInput) -> tuple[float, str]:
    """Never invented. An unknown market size scores zero and says so."""
    maximum = COMPONENT_MAX["market_potential"]
    band = (inp.market_size_band or "unknown").lower()
    fractions = {"large": 1.0, "medium": 0.65, "small": 0.35, "tiny": 0.1}
    if band not in fractions:
        return 0.0, (
            "Market size is unknown. No figure is estimated here; this scores zero until "
            "evidence supplies one."
        )
    why = f"Market size banded as {band}"
    why += f" — {inp.market_size_evidence}" if inp.market_size_evidence else " (banded from stored evidence)."
    return round(maximum * fractions[band], 2), why


def _evidence_diversity(inp: OpportunityInput) -> tuple[float, str]:
    maximum = COMPONENT_MAX["evidence_diversity"]
    classes = {f.signal_class for f in inp.facts}
    groups = {f.source_group for f in inp.facts}
    non_proxy = {f.signal_class for f in inp.facts if not f.is_proxy}
    reliability = _mean([f.source_reliability for f in inp.facts]) or 0.0

    class_part = min(1.0, len(non_proxy) / 4)
    group_part = min(1.0, (len(groups) - 1) / 3) if len(groups) > 1 else 0.0
    points = maximum * (0.5 * class_part + 0.3 * group_part + 0.2 * reliability)
    return (
        round(points, 2),
        f"{len(classes)} kind(s) of evidence from {len(groups)} independent source famil(y/ies), "
        f"mean reliability {reliability:.2f}.",
    )


def _early_entry(inp: OpportunityInput) -> tuple[float, str]:
    """Is there room left? Low local penetration and low awareness are the signal."""
    maximum = COMPONENT_MAX["early_entry"]
    parts: list[float] = []
    notes: list[str] = []

    penetration = (inp.local_penetration or "unknown").lower()
    pen_fraction = {"none": 1.0, "low": 0.8, "medium": 0.4, "high": 0.0}.get(penetration)
    if pen_fraction is not None:
        parts.append(pen_fraction)
        notes.append(f"local penetration {penetration}")

    if inp.competitor_count is not None:
        # Fewer competitors means more headroom, so the bands run downwards.
        n = inp.competitor_count
        comp = 1.0 if n <= 1 else 0.75 if n <= 3 else 0.45 if n <= 8 else 0.2 if n <= 20 else 0.05
        parts.append(comp)
        notes.append(f"{n} known competitor(s)")

    awareness = (inp.awareness or "unknown").lower()
    aw_fraction = {"low": 1.0, "medium": 0.5, "high": 0.0}.get(awareness)
    if aw_fraction is not None:
        parts.append(aw_fraction)
        notes.append(f"{awareness} mainstream awareness")

    if inp.trend_stage in {"weak_signal", "emerging", "early_adoption"}:
        parts.append(0.8)
        notes.append(f"trend stage {inp.trend_stage.replace('_', ' ')}")
    elif inp.trend_stage in {"mainstream", "mature"}:
        parts.append(0.05)
        notes.append(f"trend stage {inp.trend_stage}")

    if not parts:
        return 0.0, (
            "Nothing in the evidence shows whether this is early. No points are awarded for an unknown."
        )
    points = maximum * (sum(parts) / len(parts))
    return round(points, 2), "Signs of headroom: " + ", ".join(notes) + "."


#: How wide the door is, on absolute capital. A global score may not read any
#: one person's ceiling — that is what the USER RELEVANCE SCORE is for — but how
#: much money a thing needs at all is a fact about the opportunity, and a
#: $2,000 route is genuinely open to more of the world than a $2,000,000 one.
CAPITAL_OPENNESS: tuple[tuple[float, float], ...] = (
    (1_000.0, 1.0),
    (10_000.0, 0.85),
    (100_000.0, 0.6),
    (1_000_000.0, 0.35),
)


def _capital_openness(required: float | None) -> float:
    if required is None:
        return 0.5
    for ceiling, share in CAPITAL_OPENNESS:
        if required <= ceiling:
            return share
    return 0.15


#: Capital at which money alone stops being the deciding factor and starts
#: being a fact about the scale of the thing. Used only to REINFORCE recorded
#: barriers, never on its own — a large number is not evidence that no route
#: exists. A $400M factory can still be supplied to, invested in or worked for.
PROHIBITIVE_CAPITAL_USD: float = 10_000_000.0


def accessibility_verdict(inp: OpportunityInput) -> tuple[bool, str]:
    """Is there any way in at all — for anyone, anywhere? Absolute, never per-user.

    A real trend that nobody can act on is a fact about the world, not an
    opportunity, and the product should say so rather than scoring it low and
    letting the reason blur into a generic "score too low".

    This only ever concludes "closed" from **recorded, evidenced barriers**.
    Capital can reinforce that evidence but can never establish it alone: the
    absence of a route has to be observed, not inferred from a big number.

    Before Phase 5 this judgement was an accident of arithmetic — the capital
    branch of `_accessibility` early-returned exactly 0.0 when the requirement
    exceeded *one configured user's* ceiling, and the refusal keyed off that
    zero. That made a supposedly global refusal depend on whose profile was
    loaded. It is now explicit, and reads nothing about any person.
    """
    barriers = inp.accessibility_barriers
    if len(barriers) >= 3:
        return True, (f"{len(barriers)} independent barriers stand in the way: " + "; ".join(barriers[:3]))
    if len(barriers) >= 2:
        difficulty = (inp.technical_difficulty or "unknown").lower()
        dear = inp.capital_required_usd is not None and inp.capital_required_usd >= PROHIBITIVE_CAPITAL_USD
        if dear or difficulty == "high":
            because = (
                f"about {inp.capital_required_usd:,.0f} USD is required"
                if dear
                else "the technical difficulty is high"
            )
            return True, (
                f"{len(barriers)} barriers stand in the way and {because}: " + "; ".join(barriers[:2])
            )
    return False, ""


def _accessibility(inp: OpportunityInput) -> tuple[float, str]:
    """How reachable is this, for anyone at all?

    Deliberately impersonal. Before Phase 5 this read the configured user's
    capital ceiling, which quietly made the "global" score a score for one
    person. It now reads only the opportunity's own absolute requirement.
    """
    maximum = COMPONENT_MAX["accessibility"]

    # The component and the refusal must agree by construction. Deriving one
    # from the other is what stops them drifting apart the way they did when the
    # refusal depended on an incidental early return.
    closed, _ = accessibility_verdict(inp)
    if closed:
        return 0.0, ("No accessible way in: " + "; ".join(inp.accessibility_barriers[:3]) + ".")

    headroom = _capital_openness(inp.capital_required_usd)
    barrier_penalty = min(1.0, len(inp.accessibility_barriers) * 0.25)
    difficulty = (inp.technical_difficulty or "unknown").lower()
    diff_fraction = {"low": 1.0, "medium": 0.6, "high": 0.25}.get(difficulty, 0.5)

    points = maximum * max(0.0, (0.4 * headroom + 0.35 * diff_fraction + 0.25) - barrier_penalty * 0.25)
    barriers = (
        "; barriers: " + ", ".join(inp.accessibility_barriers[:3])
        if inp.accessibility_barriers
        else "; no barriers recorded"
    )
    cap_note = (
        f"capital about {inp.capital_required_usd:,.0f} USD"
        if inp.capital_required_usd is not None
        else "capital requirement unknown"
    )
    return round(points, 2), f"{cap_note}, technical difficulty {difficulty}{barriers}."


def _defensibility(inp: OpportunityInput) -> tuple[float, str]:
    maximum = COMPONENT_MAX["defensibility"]
    markers = inp.defensibility_markers
    if not markers:
        return 0.0, ("No evidenced advantage that would stop someone else doing the same thing.")
    points = maximum * min(1.0, len(markers) / 3)
    return round(points, 2), "Evidenced advantages: " + ", ".join(markers[:4]) + "."


def _catalyst(inp: OpportunityInput) -> tuple[float, str]:
    maximum = COMPONENT_MAX["catalyst"]
    if not inp.catalyst:
        return 0.0, "No specific event identified that would accelerate adoption."
    if inp.catalyst_evidence_id and inp.catalyst_is_dated:
        return float(maximum), f"Dated catalyst linked to stored evidence: {inp.catalyst}"
    if inp.catalyst_is_dated:
        return round(maximum * 0.5, 2), (
            f"Dated catalyst, but not linked to a stored evidence item: {inp.catalyst}"
        )
    return round(maximum * 0.3, 2), f"Catalyst claimed but undated: {inp.catalyst}"


def _timing(inp: OpportunityInput) -> tuple[float, str]:
    maximum = COMPONENT_MAX["timing"]
    fractions = {
        "weak_signal": 0.6,
        "emerging": 1.0,
        "early_adoption": 0.9,
        "accelerating": 0.7,
        "mainstream": 0.2,
        "mature": 0.0,
        "declining": 0.0,
    }
    fraction = fractions.get(inp.trend_stage, 0.3)
    return round(maximum * fraction, 2), (
        f"The underlying trend is at the {inp.trend_stage.replace('_', ' ')} stage."
    )


# ------------------------------------------------------------------- penalties
def _penalties(inp: OpportunityInput, components: dict[str, Any]) -> tuple[dict[str, float], list[str]]:
    out: dict[str, float] = {}
    warnings: list[str] = []

    adoption_points = components["real_adoption"]["points"]
    attention_facts = [f for f in inp.facts if f.signal_class == "attention"]
    adoption_facts = [f for f in inp.facts if f.is_adoption and not f.is_proxy]

    if adoption_points <= 0:
        out["weak_adoption_evidence"] = PENALTY_POINTS["weak_adoption_evidence"]
        warnings.append("Nothing here measures anyone actually using this.")

    if attention_facts and adoption_facts:
        att = _mean([f.growth_30d for f in attention_facts if f.growth_30d is not None])
        ado = _mean([f.growth_30d for f in adoption_facts if f.growth_30d is not None])
        if att is not None and ado is not None and att > 40 and att > ado * 3:
            out["attention_without_adoption"] = PENALTY_POINTS["attention_without_adoption"]
            warnings.append(
                f"Attention is growing {att:+.0f}% while real use grows {ado:+.0f}%. "
                "The talk is running well ahead of the doing."
            )
    elif attention_facts and not adoption_facts:
        out["attention_without_adoption"] = PENALTY_POINTS["attention_without_adoption"]
        warnings.append("The case is built on attention with no adoption evidence beneath it.")

    groups = {f.source_group for f in inp.facts}
    if len(groups) < 2:
        out["single_source_dependence"] = PENALTY_POINTS["single_source_dependence"]
        warnings.append("Everything rests on one source family.")

    reliability = _mean([f.source_reliability for f in inp.facts]) or 0.0
    if reliability < 0.55:
        out["low_quality_evidence"] = PENALTY_POINTS["low_quality_evidence"]
        warnings.append(f"Mean source reliability is {reliability:.2f}; the evidence is weak.")

    if (inp.market_size_band or "").lower() == "tiny":
        out["tiny_market"] = PENALTY_POINTS["tiny_market"]
        warnings.append("The addressable market is banded as tiny.")

    if inp.trend_stage in {"mainstream", "mature"}:
        out["already_mainstream"] = PENALTY_POINTS["already_mainstream"]
        warnings.append(
            f"The trend is already {inp.trend_stage}; whatever advantage existed is probably gone."
        )

    if inp.competitor_count is not None and inp.competitor_count >= 15:
        out["extreme_competition"] = PENALTY_POINTS["extreme_competition"]
        warnings.append(f"{inp.competitor_count} competitors already serve this.")

    if not inp.defensibility_markers and inp.opportunity_type in {"business", "import_distribution"}:
        out["easily_copied"] = PENALTY_POINTS["easily_copied"]
        warnings.append("Nothing recorded would stop a competitor copying this quickly.")

    # Flags raised by the type-specific analyzers map straight onto penalties.
    for flag in sorted(inp.flags):
        if flag in PENALTY_POINTS and flag not in out:
            out[flag] = PENALTY_POINTS[flag]
            warnings.append(f"Flagged by the analyzer: {flag.replace('_', ' ')}.")

    return out, warnings


# ----------------------------------------------------------------------- score
def score_opportunity(inp: OpportunityInput) -> ScoreResult:
    """Compute the GLOBAL Opportunity Score. Pure arithmetic, fully explained.

    Identical for every user on Earth. Nothing about any particular person may
    reach this function — no capital ceiling, no country, no preference. How
    relevant this is to someone is a different number entirely, computed in
    `app.analytics.relevance` and stored in a different table.
    """
    components: dict[str, Any] = {}

    for name, (points, why) in {
        "real_adoption": _real_adoption(inp),
        "market_potential": _market_potential(inp),
        "evidence_diversity": _evidence_diversity(inp),
        "early_entry": _early_entry(inp),
        "accessibility": _accessibility(inp),
        "defensibility": _defensibility(inp),
        "catalyst": _catalyst(inp),
        "timing": _timing(inp),
    }.items():
        components[name] = {"points": points, "max": COMPONENT_MAX[name], "why": why}

    raw = sum(c["points"] for c in components.values())
    penalties, warnings = _penalties(inp, components)
    penalty_total = sum(penalties.values())
    capped = min(penalty_total, MAX_TOTAL_PENALTY)
    if penalty_total > MAX_TOTAL_PENALTY:
        warnings.append(
            f"Penalties totalled {penalty_total:.0f} but are capped at {MAX_TOTAL_PENALTY:.0f}; "
            "the risk level carries the rest."
        )

    final = max(0.0, min(100.0, raw - capped))
    return ScoreResult(
        version=OPPORTUNITY_VERSION,
        raw_score=round(raw, 2),
        penalty_total=round(capped, 2),
        opportunity_score=round(final, 2),
        components=components,
        penalties=penalties,
        warnings=warnings,
        metrics={
            "signal_types": len({f.signal_type for f in inp.facts}),
            "signal_classes": len({f.signal_class for f in inp.facts}),
            "independent_sources": len({f.source_group for f in inp.facts}),
            "adoption_facts": len([f for f in inp.facts if f.is_adoption and not f.is_proxy]),
            "penalty_before_cap": round(penalty_total, 2),
        },
    )


# ------------------------------------------------------------------ confidence
def score_confidence(inp: OpportunityInput) -> tuple[float, dict[str, float]]:
    """How much can this judgement be relied on? Separate from how good it looks."""
    parts: dict[str, float] = {}

    volume = min(1.0, inp.trend_observation_count / 120)
    parts["evidence_volume"] = round(CONFIDENCE_MAX["evidence_volume"] * volume, 2)

    reliability = _mean([f.source_reliability for f in inp.facts]) or 0.0
    parts["evidence_quality"] = round(CONFIDENCE_MAX["evidence_quality"] * reliability, 2)

    groups = len({f.source_group for f in inp.facts})
    parts["independent_confirmation"] = round(
        CONFIDENCE_MAX["independent_confirmation"] * min(1.0, (groups - 1) / 3) if groups > 1 else 0.0,
        2,
    )

    freshest = min((f.days_since_latest for f in inp.facts), default=999)
    fresh = math.exp(-freshest / 45) if freshest < 999 else 0.0
    parts["evidence_freshness"] = round(CONFIDENCE_MAX["evidence_freshness"] * fresh, 2)

    growths = [f.growth_30d for f in inp.facts if f.growth_30d is not None]
    if len(growths) >= 2:
        rising = sum(1 for g in growths if g > 0) / len(growths)
        agreement = abs(rising - 0.5) * 2  # 1.0 when they all agree either way
    else:
        agreement = 0.0
    parts["source_agreement"] = round(CONFIDENCE_MAX["source_agreement"] * agreement, 2)

    parts["commercial_data"] = float(CONFIDENCE_MAX["commercial_data"]) if inp.has_commercial_data else 0.0

    required = set(REQUIRED_EVIDENCE.get(inp.opportunity_type, ()))
    missing = required - inp.evidence_kinds_present
    if required:
        share_missing = len(missing) / len(required)
        parts["missing_required_evidence"] = -round(
            CONFIDENCE_PENALTY["missing_required_evidence"] * share_missing, 2
        )

    if inp.assumption_count:
        parts["assumption_heavy"] = -round(
            min(CONFIDENCE_PENALTY["assumption_heavy"], inp.assumption_count * 3.0), 2
        )

    if freshest > 90:
        parts["stale_evidence"] = -float(CONFIDENCE_PENALTY["stale_evidence"])

    total = max(0.0, min(100.0, sum(parts.values())))
    return round(total, 2), parts


# ------------------------------------------------------------------- lifecycle
def next_state(
    *,
    current_state: str | None,
    opportunity_score: float,
    confidence: float,
    peak_score: float,
    risk_level: str,
    skeptic_status: str | None,
    days_since_evidence: int,
) -> tuple[str, str]:
    """Advance the opportunity lifecycle. Sticky: the same candidate is updated.

    There is no state above STRONG_EVIDENCE, and no wording anywhere that tells
    anyone to buy.
    """
    if skeptic_status == "reject":
        return "invalidated", "The skeptic pass found the thesis does not survive scrutiny."
    if days_since_evidence > 120:
        return "archived", (
            f"No new evidence for {days_since_evidence} days; archived rather than left looking current."
        )
    if peak_score - opportunity_score >= STATE_THRESHOLDS["weakening_drop"] and current_state in {
        "promising",
        "strong_evidence",
        "watchlist",
    }:
        return "weakening", (
            f"Score has fallen {peak_score - opportunity_score:.0f} points from its peak of {peak_score:.0f}."
        )
    if (
        opportunity_score >= STATE_THRESHOLDS["strong_score"]
        and confidence >= STATE_THRESHOLDS["strong_confidence"]
        and risk_level != "very_high"
    ):
        return "strong_evidence", (
            f"Score {opportunity_score:.0f} at confidence {confidence:.0f} with "
            f"{risk_level} risk. Still a research candidate, not a recommendation."
        )
    if (
        opportunity_score >= STATE_THRESHOLDS["promising_score"]
        and confidence >= STATE_THRESHOLDS["promising_confidence"]
    ):
        return "promising", f"Score {opportunity_score:.0f} at confidence {confidence:.0f}."
    if opportunity_score >= STATE_THRESHOLDS["watchlist_score"]:
        return "watchlist", (
            f"Score {opportunity_score:.0f} is worth keeping an eye on but not worth acting on."
        )
    return "candidate", (f"Score {opportunity_score:.0f} is too low to promote; kept as a candidate.")
