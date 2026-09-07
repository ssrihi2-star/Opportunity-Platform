"""Trend Score, Confidence Score, stage and lifecycle state.

Three things are deliberately kept apart:

* **Trend Score (0-100)** - how unusual and well-corroborated the movement is.
* **Confidence (0-100)** - how much the data behind that judgement can be relied
  on. A score of 90 with a confidence of 40 is a real and useful statement: the
  signal looks strong, and we have only a fortnight of it.
* **Stage** - where in a life cycle the thing appears to be.

None of them is an opportunity score. Nothing here says buy, import or build.

Every number is produced by arithmetic over stored observations, and every
component carries the sentence that explains it, so the dashboard can show the
whole calculation rather than a mysterious total.
"""

from __future__ import annotations

import math
from dataclasses import dataclass, field
from typing import Any

TREND_FORMULA_VERSION = "1.0.0"

COMPONENT_MAX: dict[str, int] = {
    "growth": 20,
    "acceleration": 18,
    "persistence": 15,
    "source_diversity": 18,
    "scale": 12,
    "geographic_spread": 7,
    "novelty": 10,
}

PENALTY_POINTS: dict[str, int] = {
    "tiny_baseline": 15,
    "one_day_spike": 25,
    "low_quality_sources": 10,
    "duplicate_information": 12,
    "insufficient_history": 12,
    "seasonality": 15,
    "missing_data": 8,
    "proxy_only": 10,
    "single_source": 10,
}

MAX_TOTAL_PENALTY = 60

# Lifecycle thresholds.
ACTIVE_SCORE = 35.0
ACTIVE_CONFIDENCE = 25.0
CONFIRMED_SCORE = 60.0
CONFIRMED_CONFIDENCE = 55.0
WEAKENING_DROP = 20.0
STALE_DAYS = 60


@dataclass(slots=True)
class TrendInput:
    """Everything the scorer is allowed to look at, already computed."""

    # aggregated growth across the supporting series
    growth_30d: float | None = None
    growth_90d: float | None = None
    acceleration_pp: float | None = None
    persistence: float | None = None
    momentum: float | None = None
    direction: str = "unknown"

    # scale and history
    baseline_value: float | None = None
    latest_value: float | None = None
    absolute_growth_total: float | None = None
    prior_maximum: float | None = None
    observation_count: int = 0
    missing_count: int = 0
    history_days: int = 0
    days_since_last_observation: int = 0

    # corroboration
    independent_sources: int = 0
    distinct_signal_types: int = 0
    distinct_signal_classes: int = 0
    non_proxy_classes: int = 0
    mean_source_reliability: float = 0.0
    dominant_source_share: float = 0.0
    duplication_ratio: float = 0.0
    geo_scopes: list[str] = field(default_factory=list)

    # deterministic flags from the growth engine
    is_one_day_spike: bool = False
    is_seasonal: bool = False
    seasonal_note: str = ""
    spike_note: str = ""

    # methods that independently point the same way (growth, momentum, persistence)
    agreeing_methods: int = 0
    total_methods: int = 3


@dataclass(slots=True)
class Component:
    name: str
    points: float
    max_points: int
    rationale: str


@dataclass(slots=True)
class TrendScore:
    formula_version: str
    components: list[Component]
    penalties: dict[str, float]
    raw_score: float
    penalty_total: float
    trend_score: float
    confidence: float
    confidence_parts: dict[str, float]
    stage: str
    stage_evidence: list[str]
    warnings: list[str]

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
            "trend_score": self.trend_score,
            "confidence": self.confidence,
            "confidence_parts": self.confidence_parts,
            "stage": self.stage,
            "stage_evidence": self.stage_evidence,
            "warnings": self.warnings,
        }


def _clamp(value: float, lo: float, hi: float) -> float:
    return max(lo, min(hi, value))


# ------------------------------------------------------------------ components
def _growth(inp: TrendInput) -> Component:
    cap = COMPONENT_MAX["growth"]
    value = inp.growth_30d if inp.growth_30d is not None else inp.growth_90d
    label = "30-day" if inp.growth_30d is not None else "90-day"
    if value is None:
        return Component("growth", 0, cap, "Not enough history to measure growth over any window.")
    if value <= 0:
        return Component("growth", 0, cap, f"{label} change is {value:+.1f}%; nothing is growing.")
    # 100% over the window earns full marks; the curve is log-shaped so a
    # 900% jump is not worth nine times a 100% one.
    pts = round(_clamp(math.log10(1 + value / 10.0) / math.log10(11.0), 0, 1) * cap, 2)
    return Component("growth", pts, cap, f"{label} growth is {value:+.1f}%.")


def _acceleration(inp: TrendInput) -> Component:
    cap = COMPONENT_MAX["acceleration"]
    if inp.acceleration_pp is None:
        return Component("acceleration", 0, cap, "Too little history to compare growth rates.")
    if inp.acceleration_pp <= 0:
        return Component(
            "acceleration",
            0,
            cap,
            f"Growth is not speeding up ({inp.acceleration_pp:+.1f} percentage points versus the "
            "earlier period). Steady growth scores here only through persistence.",
        )
    pts = round(_clamp(inp.acceleration_pp / 60.0, 0, 1) * cap, 2)
    return Component(
        "acceleration",
        pts,
        cap,
        f"Recent growth runs {inp.acceleration_pp:+.1f} percentage points above the earlier period.",
    )


def _persistence(inp: TrendInput) -> Component:
    cap = COMPONENT_MAX["persistence"]
    if inp.persistence is None:
        return Component("persistence", 0, cap, "Series is too short to test whether the rise holds.")
    pts = round(inp.persistence * cap, 2)
    return Component(
        "persistence",
        pts,
        cap,
        f"{inp.persistence:.0%} of consecutive periods moved up, so the rise is "
        f"{'consistent' if inp.persistence >= 0.7 else 'intermittent'}.",
    )


def _source_diversity(inp: TrendInput) -> Component:
    cap = COMPONENT_MAX["source_diversity"]
    if inp.independent_sources <= 1:
        return Component(
            "source_diversity",
            0,
            cap,
            f"{inp.independent_sources} independent source. One source cannot corroborate itself.",
        )
    breadth = _clamp((inp.independent_sources - 1) / 3.0, 0, 1)
    kinds = _clamp((inp.distinct_signal_classes - 1) / 2.0, 0, 1)
    quality = _clamp(inp.mean_source_reliability, 0, 1)
    pts = round(cap * (0.45 * breadth + 0.35 * kinds + 0.20 * quality), 2)
    return Component(
        "source_diversity",
        pts,
        cap,
        f"{inp.independent_sources} independent sources across {inp.distinct_signal_classes} "
        f"kind(s) of evidence, mean reliability {quality:.2f}.",
    )


def _scale(inp: TrendInput) -> Component:
    """A rough magnitude band.

    Units are not comparable across signals - stars, tonnes and pageviews share no
    scale - so this is a deliberately coarse order-of-magnitude read, and worth
    only 12 of the 100 points for exactly that reason.
    """
    cap = COMPONENT_MAX["scale"]
    value = inp.latest_value
    if value is None or value <= 0:
        return Component("scale", 0, cap, "No positive level to size.")
    magnitude = math.log10(value)
    pts = round(_clamp((magnitude - 1) / 3.0, 0, 1) * cap, 2)
    return Component(
        "scale",
        pts,
        cap,
        f"Latest level is about {value:,.0f} (order of magnitude 1e{magnitude:.1f}). "
        "Units differ between sources, so this is a coarse size band only.",
    )


def _geographic_spread(inp: TrendInput) -> Component:
    cap = COMPONENT_MAX["geographic_spread"]
    scopes = {s for s in inp.geo_scopes if s}
    countries = {s for s in scopes if s != "global"}
    if not scopes:
        return Component("geographic_spread", 0, cap, "No geography recorded.")
    if countries:
        pts = round(_clamp(len(countries) / 3.0, 0, 1) * cap, 2)
        return Component(
            "geographic_spread",
            pts,
            cap,
            f"Observed in {len(countries)} specific place(s): {', '.join(sorted(countries))}.",
        )
    return Component(
        "geographic_spread",
        cap * 0.4,
        cap,
        "Only global-scope evidence, so no local spread can be shown.",
    )


def _novelty(inp: TrendInput) -> Component:
    """Is the current level unprecedented for this series?"""
    cap = COMPONENT_MAX["novelty"]
    if inp.latest_value is None or inp.prior_maximum is None or inp.prior_maximum <= 0:
        return Component("novelty", 0, cap, "No earlier peak to compare against.")
    ratio = inp.latest_value / inp.prior_maximum
    if ratio >= 1.5:
        return Component(
            "novelty", float(cap), cap, f"Current level is {ratio:.1f}x the previous high for this series."
        )
    if ratio > 1.0:
        return Component(
            "novelty",
            cap * 0.6,
            cap,
            f"Current level is a new high, {(ratio - 1) * 100:.0f}% above the last peak.",
        )
    if ratio >= 0.9:
        return Component("novelty", cap * 0.3, cap, "Near, but not above, the previous peak.")
    return Component("novelty", 0, cap, f"Current level is {(1 - ratio) * 100:.0f}% below the previous peak.")


# ------------------------------------------------------------------- penalties
def _penalties(inp: TrendInput) -> tuple[dict[str, float], list[str]]:
    out: dict[str, float] = {}
    warnings: list[str] = []

    tiny_baseline = (inp.baseline_value or 0) < 25
    tiny_absolute = abs(inp.absolute_growth_total or 0) < 15
    if tiny_baseline and tiny_absolute:
        out["tiny_baseline"] = float(PENALTY_POINTS["tiny_baseline"])
        warnings.append(
            f"Growth is measured from a baseline of {inp.baseline_value or 0:,.0f} and the total "
            f"move is {inp.absolute_growth_total or 0:,.0f}. A large percentage on numbers this "
            "small is arithmetic, not evidence."
        )
    elif tiny_baseline:
        # The move is real in absolute terms, but a base this small still makes the
        # percentage untrustworthy - so half the penalty, not none.
        out["tiny_baseline"] = float(PENALTY_POINTS["tiny_baseline"]) / 2
        warnings.append(
            f"The starting level was only {inp.baseline_value or 0:,.0f}, so the percentage "
            "overstates how established this is, even though the absolute move is real."
        )

    if inp.is_one_day_spike:
        out["one_day_spike"] = float(PENALTY_POINTS["one_day_spike"])
        warnings.append(inp.spike_note or "A single observation dominates the series.")

    if inp.mean_source_reliability < 0.5 and inp.independent_sources:
        out["low_quality_sources"] = float(PENALTY_POINTS["low_quality_sources"])
        warnings.append(
            f"Mean source reliability is {inp.mean_source_reliability:.2f}; the evidence comes "
            "mostly from weak sources."
        )

    if inp.duplication_ratio >= 0.4:
        out["duplicate_information"] = float(PENALTY_POINTS["duplicate_information"])
        warnings.append(
            f"{inp.duplication_ratio:.0%} of the supporting articles are the same story "
            "republished elsewhere."
        )

    if inp.history_days < 30 or inp.observation_count < 8:
        out["insufficient_history"] = float(PENALTY_POINTS["insufficient_history"])
        warnings.append(
            f"Only {inp.observation_count} observations over {inp.history_days} days. "
            "There is not yet enough history to be sure of the shape."
        )

    if inp.is_seasonal:
        out["seasonality"] = float(PENALTY_POINTS["seasonality"])
        warnings.append(inp.seasonal_note or "This rise repeats at the same time every year.")

    total_points = inp.observation_count + inp.missing_count
    if total_points and inp.missing_count / total_points > 0.2:
        out["missing_data"] = float(PENALTY_POINTS["missing_data"])
        warnings.append(
            f"{inp.missing_count} of {total_points} periods were never reported, so the series "
            "has holes in it."
        )

    if inp.distinct_signal_classes and inp.non_proxy_classes == 0:
        out["proxy_only"] = float(PENALTY_POINTS["proxy_only"])
        warnings.append(
            "Every supporting measurement is a proxy. Nothing here observes the activity directly."
        )

    if inp.independent_sources <= 1:
        out["single_source"] = float(PENALTY_POINTS["single_source"])
    elif inp.dominant_source_share > 0.75:
        out["single_source"] = float(PENALTY_POINTS["single_source"]) * 0.5
        warnings.append(f"One source supplies {inp.dominant_source_share:.0%} of the observations.")
    return out, warnings


# ------------------------------------------------------------------ confidence
def compute_confidence(inp: TrendInput) -> tuple[float, dict[str, float]]:
    """How much the data itself can be relied on. Independent of the score."""
    parts: dict[str, float] = {}

    parts["history"] = round(_clamp(inp.history_days / 180.0, 0, 1) * 25, 2)
    parts["observations"] = round(_clamp(inp.observation_count / 60.0, 0, 1) * 20, 2)
    parts["source_quality"] = round(_clamp(inp.mean_source_reliability, 0, 1) * 20, 2)
    parts["independent_sources"] = round(_clamp((inp.independent_sources - 1) / 3.0, 0, 1) * 20, 2)
    agreement = inp.agreeing_methods / inp.total_methods if inp.total_methods else 0.0
    parts["method_agreement"] = round(_clamp(agreement, 0, 1) * 15, 2)

    total_points = inp.observation_count + inp.missing_count
    missing_share = inp.missing_count / total_points if total_points else 0.0
    parts["missing_data_penalty"] = -round(_clamp(missing_share * 2, 0, 1) * 20, 2)

    if inp.days_since_last_observation > STALE_DAYS:
        parts["stale_penalty"] = -10.0

    confidence = round(_clamp(sum(parts.values()), 0, 100), 2)
    return confidence, parts


# ---------------------------------------------------------------------- stages
def classify_stage(inp: TrendInput, trend_score: float) -> tuple[str, list[str]]:
    """Deterministic stage ladder. No model participates in this decision."""
    evidence: list[str] = []
    growth = inp.growth_30d if inp.growth_30d is not None else inp.growth_90d

    if inp.direction == "falling" or (growth is not None and growth < -10):
        growth_str = f"{growth:+.1f}%" if growth is not None else "no measured window"
        evidence.append(f"Activity is falling ({growth_str} over the measured window).")
        if inp.persistence is not None and inp.persistence <= 0.3:
            evidence.append(f"Only {inp.persistence:.0%} of periods rose.")
        return "declining", evidence

    thin = inp.observation_count < 8 or inp.history_days < 21 or inp.independent_sources < 2
    if thin or inp.is_one_day_spike:
        if inp.is_one_day_spike:
            evidence.append("A single observation accounts for the movement.")
        if inp.observation_count < 8:
            evidence.append(f"Only {inp.observation_count} observations so far.")
        if inp.independent_sources < 2:
            evidence.append(f"{inp.independent_sources} independent source.")
        if inp.history_days < 21:
            evidence.append(f"Only {inp.history_days} days of history.")
        return "weak_signal", evidence

    accelerating = (inp.acceleration_pp or 0) > 15 and (growth or 0) > 25
    if accelerating and (inp.persistence or 0) >= 0.5:
        evidence.append(
            f"Growth of {growth:+.1f}% is running {inp.acceleration_pp:+.1f} percentage points "
            "above the earlier period."
        )
        evidence.append(f"{inp.independent_sources} independent sources agree.")
        return "accelerating", evidence

    big = (inp.latest_value or 0) >= 10_000
    if big and (growth is None or abs(growth) < 5):
        evidence.append(f"Large, flat series (level about {inp.latest_value:,.0f}).")
        return "mature", evidence
    if big and (growth or 0) >= 5:
        evidence.append(f"Large series still growing {growth:+.1f}%.")
        return "mainstream", evidence

    if (growth or 0) >= 15 and (inp.persistence or 0) >= 0.5:
        evidence.append(f"Consistent growth of {growth:+.1f}% across {inp.observation_count} observations.")
        evidence.append(f"{inp.independent_sources} independent sources.")
        return "early_adoption", evidence

    evidence.append(
        f"Growth of {growth:+.1f}% with {inp.independent_sources} sources and "
        f"{inp.history_days} days of history."
        if growth is not None
        else "Movement is present but small."
    )
    return "emerging", evidence


# ------------------------------------------------------------------- lifecycle
def next_state(
    *,
    current_state: str | None,
    trend_score: float,
    confidence: float,
    peak_score: float,
    independent_sources: int,
    days_since_last_observation: int,
    is_one_day_spike: bool,
    is_seasonal: bool,
    stage: str,
) -> tuple[str, str]:
    """Advance the lifecycle. Returns (state, reason).

    Deliberately sticky: a trend is a thing being followed over time, so the
    engine updates the same record rather than declaring a new discovery daily.
    """
    if days_since_last_observation > STALE_DAYS:
        return "ended", (
            f"No new observation for {days_since_last_observation} days; the trail has gone cold."
        )
    # A spike and a season are both real patterns, and neither is a trend. They
    # never rise above candidate; if one had already been promoted, the earlier
    # promotion was a mistake and is marked as such.
    if is_one_day_spike:
        if current_state in {"active", "confirmed"}:
            return "invalidated", "The movement turned out to rest on a single observation."
        return "candidate", "A single observation accounts for the movement; not treated as a trend."
    if is_seasonal:
        if current_state in {"active", "confirmed"}:
            return "invalidated", "The rise repeats every year; it is a season, not a trend."
        return "candidate", (
            "This rise happens at the same time every year, so it is a seasonal pattern rather "
            "than a new trend."
        )
    if peak_score - trend_score >= WEAKENING_DROP and current_state in {"active", "confirmed"}:
        return "weakening", (
            f"Score has fallen {peak_score - trend_score:.0f} points from its peak of {peak_score:.0f}."
        )
    if trend_score >= CONFIRMED_SCORE and confidence >= CONFIRMED_CONFIDENCE and independent_sources >= 2:
        return "confirmed", (
            f"Score {trend_score:.0f} and confidence {confidence:.0f} with "
            f"{independent_sources} independent sources."
        )
    if trend_score >= ACTIVE_SCORE and confidence >= ACTIVE_CONFIDENCE:
        if independent_sources < 2:
            # A single source can produce a high score, but calling that "active"
            # would dress one opinion up as corroboration.
            return "candidate", (
                f"Score {trend_score:.0f} looks active, but only {independent_sources} independent "
                "source supports it. Held as a candidate until something else confirms it."
            )
        return "active", f"Score {trend_score:.0f} at confidence {confidence:.0f}."
    if stage == "declining":
        return "weakening", "Activity is falling."
    return "candidate", "Below the activation thresholds; kept under observation."


# ---------------------------------------------------------------------- public
def score_trend(inp: TrendInput) -> TrendScore:
    """The whole calculation, with every step exposed."""
    components = [
        _growth(inp),
        _acceleration(inp),
        _persistence(inp),
        _source_diversity(inp),
        _scale(inp),
        _geographic_spread(inp),
        _novelty(inp),
    ]
    raw = round(sum(c.points for c in components), 2)

    penalties, warnings = _penalties(inp)
    penalty_total = round(min(sum(penalties.values()), MAX_TOTAL_PENALTY), 2)
    if sum(penalties.values()) > MAX_TOTAL_PENALTY:
        warnings.append(
            f"Penalties totalled {sum(penalties.values()):.0f} but are capped at "
            f"{MAX_TOTAL_PENALTY}; the confidence score carries the rest."
        )

    trend_score = round(_clamp(raw - penalty_total, 0, 100), 2)
    confidence, parts = compute_confidence(inp)
    stage, stage_evidence = classify_stage(inp, trend_score)

    return TrendScore(
        formula_version=TREND_FORMULA_VERSION,
        components=components,
        penalties=penalties,
        raw_score=raw,
        penalty_total=penalty_total,
        trend_score=trend_score,
        confidence=confidence,
        confidence_parts=parts,
        stage=stage,
        stage_evidence=stage_evidence,
        warnings=warnings,
    )
