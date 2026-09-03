"""The scoring engine. A pure function: same input -> same output, forever.

Nothing in this module calls a model, reads the clock in a way that changes the
result, or uses randomness. Every component returns its own rationale so the UI
can show *why* a number is what it is.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from statistics import median
from typing import Any

from app.analytics.scoring_config import (
    ACCESSIBILITY_POINTS,
    BLOCKING_RISK_CODES,
    COMPONENT_MAX,
    CRYPTO_ESCALATING_FLAGS,
    DEFENSIBILITY_MARKERS,
    FORMULA_VERSION,
    LIQUIDITY_POINTS,
    MARKET_SIZE_BANDS,
    MAX_MEME_SCORE,
    MAX_TOTAL_PENALTY,
    PENALTY_POINTS,
    REQUIRED_EVIDENCE,
)


@dataclass(slots=True)
class RiskFlag:
    code: str
    severity: str = "medium"  # info|low|medium|high|blocking
    rationale: str = ""
    evidence_ids: list[str] = field(default_factory=list)


@dataclass(slots=True)
class ScoringInput:
    category: str
    adoption_growth_pcts: list[float] = field(default_factory=list)
    acceleration_pp: float | None = None
    market_size_usd: float | None = None
    source_reliabilities: list[float] = field(default_factory=list)
    independent_source_count: int = 0
    dominant_source_share: float = 0.0  # 0-1, largest single source's weight
    signal_types: list[str] = field(default_factory=list)
    accessibility: str = "unknown"
    liquidity: str = "unknown"
    capital_required_usd: float | None = None
    user_capital_max_usd: float | None = None
    defensibility_markers: list[str] = field(default_factory=list)
    catalyst_dated: bool = False
    catalyst_has_evidence: bool = False
    satisfied_evidence_fields: list[str] = field(default_factory=list)
    cross_source_agreement: float = 0.0  # 0-1
    newest_evidence_age_days: int = 999
    manipulation_probability: float = 0.0
    risk_flags: list[RiskFlag] = field(default_factory=list)
    is_crypto: bool = False
    is_meme: bool = False


@dataclass(slots=True)
class Component:
    name: str
    points: float
    max_points: int
    rationale: str


@dataclass(slots=True)
class ScoreResult:
    formula_version: str
    components: list[Component]
    penalties: dict[str, float]
    raw_score: float
    penalty_total: float
    adjusted_score: float
    confidence: float
    evidence_completeness: float
    risk_level: str
    blocked: bool
    notes: list[str]

    def to_dict(self) -> dict[str, Any]:
        return {
            "formula_version": self.formula_version,
            "components": [
                {"name": c.name, "points": c.points, "max": c.max_points, "why": c.rationale}
                for c in self.components
            ],
            "penalties": self.penalties,
            "raw_score": self.raw_score,
            "penalty_total": self.penalty_total,
            "adjusted_score": self.adjusted_score,
            "confidence": self.confidence,
            "evidence_completeness": self.evidence_completeness,
            "risk_level": self.risk_level,
            "blocked": self.blocked,
            "notes": self.notes,
        }


def _clamp(v: float, lo: float, hi: float) -> float:
    return max(lo, min(hi, v))


# --------------------------------------------------------------------- components
def _adoption(inp: ScoringInput) -> Component:
    cap = COMPONENT_MAX["real_adoption_growth"]
    if not inp.adoption_growth_pcts:
        return Component(
            "real_adoption_growth",
            0,
            cap,
            "No adoption-class signal present. Attention alone does not count.",
        )
    m = median(inp.adoption_growth_pcts)
    pts = round(_clamp(m / 100.0, 0.0, 1.0) * cap, 2)
    return Component(
        "real_adoption_growth",
        pts,
        cap,
        f"Median adoption growth across {len(inp.adoption_growth_pcts)} signal(s) is {m:.1f}%.",
    )


def _market_size(inp: ScoringInput) -> Component:
    cap = COMPONENT_MAX["market_size"]
    if inp.market_size_usd is None:
        return Component("market_size", 0, cap, "Market size unknown. Scored zero rather than estimated.")
    for threshold, pts in MARKET_SIZE_BANDS:
        if inp.market_size_usd >= threshold:
            return Component(
                "market_size", float(pts), cap, f"Evidenced market size ~${inp.market_size_usd:,.0f}."
            )
    return Component("market_size", 0, cap, "Market size below the smallest band.")


def _acceleration(inp: ScoringInput) -> Component:
    cap = COMPONENT_MAX["signal_acceleration"]
    if inp.acceleration_pp is None:
        return Component("signal_acceleration", 0, cap, "Not enough history to measure acceleration.")
    if inp.acceleration_pp <= 0:
        return Component(
            "signal_acceleration", 0, cap, f"Growth is decelerating ({inp.acceleration_pp:.1f}pp)."
        )
    pts = round(_clamp(inp.acceleration_pp / 40.0, 0.0, 1.0) * cap, 2)
    return Component(
        "signal_acceleration",
        pts,
        cap,
        f"Growth is speeding up by {inp.acceleration_pp:.1f} percentage points versus the previous window.",
    )


def _sources(inp: ScoringInput) -> Component:
    cap = COMPONENT_MAX["independent_source_confirmation"]
    n = inp.independent_source_count
    if n <= 1:
        return Component(
            "independent_source_confirmation",
            0,
            cap,
            f"Only {n} independent source. The multi-source gate is not met.",
        )
    breadth = _clamp((n - 1) / 3.0, 0.0, 1.0)
    quality = (
        sum(inp.source_reliabilities) / len(inp.source_reliabilities) if inp.source_reliabilities else 0.5
    )
    pts = round(breadth * quality * cap, 2)
    return Component(
        "independent_source_confirmation",
        pts,
        cap,
        f"{n} independent sources, mean reliability {quality:.2f}.",
    )


def _entry(inp: ScoringInput) -> Component:
    cap = COMPONENT_MAX["entry_attractiveness"]
    access_pts = ACCESSIBILITY_POINTS.get(inp.accessibility, 0)
    scaled = access_pts / max(ACCESSIBILITY_POINTS.values()) * cap
    if inp.capital_required_usd is None or inp.user_capital_max_usd is None:
        return Component(
            "entry_attractiveness",
            round(scaled * 0.6, 2),
            cap,
            f"Accessibility '{inp.accessibility}'; capital requirement unknown, "
            "so the score is discounted rather than assumed affordable.",
        )
    if inp.capital_required_usd > inp.user_capital_max_usd:
        return Component(
            "entry_attractiveness",
            0,
            cap,
            f"Requires ~${inp.capital_required_usd:,.0f}, above the configured "
            f"maximum of ${inp.user_capital_max_usd:,.0f}.",
        )
    headroom = 1.0 - (inp.capital_required_usd / max(inp.user_capital_max_usd, 1.0))
    return Component(
        "entry_attractiveness",
        round(scaled * (0.6 + 0.4 * headroom), 2),
        cap,
        f"Accessibility '{inp.accessibility}'; capital ~${inp.capital_required_usd:,.0f} "
        f"fits within ${inp.user_capital_max_usd:,.0f}.",
    )


def _defensibility(inp: ScoringInput) -> Component:
    cap = COMPONENT_MAX["defensibility"]
    if not inp.defensibility_markers:
        return Component("defensibility", 0, cap, "No evidenced moat.")
    pts = sum(DEFENSIBILITY_MARKERS.get(m, 0) for m in inp.defensibility_markers)
    return Component(
        "defensibility",
        float(min(pts, cap)),
        cap,
        "Moat evidence: " + ", ".join(sorted(inp.defensibility_markers)) + ".",
    )


def _catalyst(inp: ScoringInput) -> Component:
    cap = COMPONENT_MAX["clear_catalyst"]
    if not inp.catalyst_dated:
        return Component("clear_catalyst", 0, cap, "No dated catalyst identified.")
    if not inp.catalyst_has_evidence:
        return Component(
            "clear_catalyst",
            cap * 0.3,
            cap,
            "A catalyst was claimed but is not backed by a stored evidence item.",
        )
    return Component("clear_catalyst", float(cap), cap, "A dated catalyst is linked to stored evidence.")


def _liquidity(inp: ScoringInput) -> Component:
    cap = COMPONENT_MAX["accessibility_liquidity"]
    pts = LIQUIDITY_POINTS.get(inp.liquidity, 0)
    return Component(
        "accessibility_liquidity",
        float(min(pts, cap)),
        cap,
        f"Liquidity/accessibility rated '{inp.liquidity}'.",
    )


# ---------------------------------------------------------------------- penalties
def _penalties(inp: ScoringInput, evidence_completeness: float) -> dict[str, float]:
    out: dict[str, float] = {}

    if inp.manipulation_probability > 0:
        out["manipulation_risk"] = round(
            _clamp(inp.manipulation_probability, 0, 1) * PENALTY_POINTS["manipulation_risk"], 2
        )
    if evidence_completeness < 0.4:
        out["weak_evidence"] = float(PENALTY_POINTS["weak_evidence"])
    if inp.independent_source_count <= 1 or inp.dominant_source_share > 0.7:
        out["single_source_dependence"] = float(PENALTY_POINTS["single_source_dependence"])

    flag_to_penalty = {
        "anonymous_founders": "anonymous_team",
        "concentrated_ownership": "concentrated_ownership",
        "thin_liquidity": "thin_liquidity",
        "extreme_valuation": "extreme_valuation",
        "no_working_product": "no_working_product",
        "paid_influencer_promotion": "paid_promotion_dependence",
        "active_enforcement": "regulatory_danger",
        "import_ban_risk": "regulatory_danger",
        "single_regulation_dependence": "regulatory_danger",
        "unsustainable_growth": "unsustainable_growth",
        "dominant_incumbent": "high_competition",
        "local_competitor_exists": "high_competition",
        "operational_difficulty": "operational_difficulty",
        "certification_required": "operational_difficulty",
    }
    for flag in inp.risk_flags:
        key = flag_to_penalty.get(flag.code)
        if key:
            out[key] = float(PENALTY_POINTS[key])
    return out


def _risk_level(inp: ScoringInput, penalty_total: float, confidence: float) -> str:
    severities = {f.severity for f in inp.risk_flags}
    if penalty_total <= 5 and confidence >= 0.75 and not (severities & {"medium", "high", "blocking"}):
        level = "low"
    elif penalty_total <= 20 and "high" not in severities and "blocking" not in severities:
        level = "medium"
    elif penalty_total <= 40 or "high" in severities:
        level = "high"
    else:
        level = "very_high"

    if inp.manipulation_probability > 0.5 or "blocking" in severities:
        level = "very_high"

    # Crypto floor: never low, and any escalating flag pushes it to very_high.
    if inp.is_crypto:
        order = ["low", "medium", "high", "very_high"]
        if order.index(level) < order.index("high"):
            level = "high"
        if {f.code for f in inp.risk_flags} & CRYPTO_ESCALATING_FLAGS:
            level = "very_high"
    return level


# ------------------------------------------------------------------------- public
def evidence_completeness(category: str, satisfied: list[str]) -> float:
    required = REQUIRED_EVIDENCE.get(category, [])
    if not required:
        return 0.0
    have = len(set(satisfied) & set(required))
    return round(have / len(required), 4)


def compute_confidence(inp: ScoringInput, completeness: float) -> float:
    reliability = (
        sum(inp.source_reliabilities) / len(inp.source_reliabilities) if inp.source_reliabilities else 0.0
    )
    if inp.newest_evidence_age_days <= 7:
        recency = 1.0
    elif inp.newest_evidence_age_days >= 90:
        recency = 0.0
    else:
        recency = 1.0 - (inp.newest_evidence_age_days - 7) / 83.0
    value = (
        0.40 * reliability
        + 0.25 * completeness
        + 0.20 * _clamp(inp.cross_source_agreement, 0, 1)
        + 0.15 * recency
    )
    return round(_clamp(value, 0.0, 1.0), 4)


def score_opportunity(inp: ScoringInput) -> ScoreResult:
    notes: list[str] = []
    blocking = [f for f in inp.risk_flags if f.code in BLOCKING_RISK_CODES or f.severity == "blocking"]

    completeness = evidence_completeness(inp.category, inp.satisfied_evidence_fields)
    confidence = compute_confidence(inp, completeness)

    components = [
        _adoption(inp),
        _market_size(inp),
        _acceleration(inp),
        _sources(inp),
        _entry(inp),
        _defensibility(inp),
        _catalyst(inp),
        _liquidity(inp),
    ]
    raw_score = round(sum(c.points for c in components), 2)

    penalties = _penalties(inp, completeness)
    penalty_total = round(min(sum(penalties.values()), MAX_TOTAL_PENALTY), 2)
    if sum(penalties.values()) > MAX_TOTAL_PENALTY:
        notes.append(
            f"Penalties totalled {sum(penalties.values()):.0f} but are capped at "
            f"{MAX_TOTAL_PENALTY}. The risk level, not the score, carries the rest."
        )

    adjusted = round(_clamp(raw_score - penalty_total, 0.0, 100.0), 2)
    risk_level = _risk_level(inp, penalty_total, confidence)

    if inp.is_meme and adjusted > MAX_MEME_SCORE:
        notes.append(
            f"Classified as a meme asset: score capped at {MAX_MEME_SCORE} and excluded "
            "from top-opportunity listings."
        )
        adjusted = float(MAX_MEME_SCORE)

    if blocking:
        notes.append(
            "Blocking risk flag present ("
            + ", ".join(sorted(f.code for f in blocking))
            + "): the opportunity is rejected regardless of score."
        )
        adjusted = 0.0
        risk_level = "very_high"

    if len(set(inp.signal_types)) < 3:
        notes.append(
            f"Only {len(set(inp.signal_types))} distinct signal type(s); the gate requires 3. "
            "This is a candidate, not an opportunity."
        )

    return ScoreResult(
        formula_version=FORMULA_VERSION,
        components=components,
        penalties=penalties,
        raw_score=raw_score,
        penalty_total=penalty_total,
        adjusted_score=adjusted,
        confidence=confidence,
        evidence_completeness=completeness,
        risk_level=risk_level,
        blocked=bool(blocking),
        notes=notes,
    )
