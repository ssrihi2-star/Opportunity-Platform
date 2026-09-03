"""Configuration for the opportunity engine, versioned as data.

Everything here is a number a person might reasonably want to change. Changing
one requires bumping `OPPORTUNITY_VERSION`, because every stored evaluation
records the version it was computed under and historical scores are never
silently rewritten.
"""

from __future__ import annotations

from typing import Final

#: Bump on ANY change to a weight, a threshold or a gate rule below.
OPPORTUNITY_VERSION: Final[str] = "1.0.0"
SKEPTIC_PROMPT_VERSION: Final[str] = "1.0.0"
REPORT_PROMPT_VERSION: Final[str] = "1.0.0"
RISK_VERSION: Final[str] = "1.0.0"
GEO_GAP_VERSION: Final[str] = "0.1.0-experimental"
ADOPTION_ATTENTION_VERSION: Final[str] = "0.1.0-experimental"

# ------------------------------------------------------------------- the gate
#: The gate is deliberately hard to pass. Three meaningful opportunities beat a
#: hundred exciting ones, and a false positive costs real money and real weeks.
GATE: Final[dict[str, float]] = {
    #: Distinct signal *types*, not measurements. Three news feeds are one type.
    "min_signal_types": 3,
    #: Distinct source families. Syndicated coverage collapses to one family.
    "min_independent_sources": 2,
    #: Distinct signal *classes* — developer, trade, commercial are three classes;
    #: two attention metrics are one. This is the rule that stops a pile of
    #: press coverage from looking like corroboration.
    "min_signal_classes": 2,
    #: The underlying trend must itself be credible before we look for an action.
    "min_trend_score": 30.0,
    "min_trend_confidence": 35.0,
    #: Enough observations and enough calendar time to mean anything.
    "min_observations": 20,
    "min_history_days": 45,
    #: Data quality: how much of the evidence may be missing or proxy-only.
    "max_missing_ratio": 0.35,
    "max_proxy_share": 0.75,
    #: The opportunity's own confidence floor, after the skeptic has spoken.
    "min_confidence": 30.0,
    #: And a floor on the score itself. Section 28 of the brief: three meaningful
    #: candidates beat a hundred exciting ones, so a weak candidate is not stored
    #: at all rather than stored and ignored.
    "min_opportunity_score": 22.0,
}

#: A trend carrying any of these is never turned into an opportunity at all.
DISQUALIFYING_TREND_FLAGS: Final[frozenset[str]] = frozenset({"one_day_spike", "seasonality"})

#: Trend lifecycle states that are allowed to produce an opportunity. A trend the
#: engine has already invalidated cannot become a business plan.
ELIGIBLE_TREND_STATES: Final[frozenset[str]] = frozenset({"active", "confirmed", "weakening"})

# ------------------------------------------------------------- score components
#: Section 6 of the Phase 4 brief, verbatim, summing to 100.
COMPONENT_MAX: Final[dict[str, int]] = {
    "real_adoption": 20,
    "market_potential": 15,
    "evidence_diversity": 15,
    "early_entry": 15,
    "accessibility": 10,
    "defensibility": 10,
    "catalyst": 10,
    "timing": 5,
}

# --------------------------------------------------------------------- penalties
PENALTY_POINTS: Final[dict[str, int]] = {
    "extreme_valuation": 15,
    "weak_adoption_evidence": 15,
    "attention_without_adoption": 15,
    "tiny_market": 10,
    "extreme_competition": 10,
    "easily_copied": 8,
    "regulatory_uncertainty": 10,
    "supply_chain_fragility": 8,
    "single_supplier": 8,
    "single_customer": 10,
    "poor_liquidity": 10,
    "concentrated_ownership": 12,
    "anonymous_team": 12,
    "promotional_manipulation": 15,
    "low_quality_evidence": 10,
    "single_source_dependence": 10,
    "already_mainstream": 12,
    "difficult_local_execution": 8,
    "poor_economics": 10,
    "excessive_capital": 8,
}

#: As in Phase 3: a cap so a strong candidate is not zeroed by many small flags.
#: When it bites, a warning says so and the risk level carries the rest.
MAX_TOTAL_PENALTY: Final[float] = 65.0

# ------------------------------------------------------------------- confidence
CONFIDENCE_MAX: Final[dict[str, int]] = {
    "evidence_volume": 20,
    "evidence_quality": 20,
    "independent_confirmation": 20,
    "evidence_freshness": 15,
    "source_agreement": 15,
    "commercial_data": 10,
}

#: Confidence loses points for what is absent, not only for what is weak.
CONFIDENCE_PENALTY: Final[dict[str, int]] = {
    "missing_required_evidence": 20,
    "assumption_heavy": 15,
    "stale_evidence": 15,
}

# ------------------------------------------------------------- lifecycle states
STATE_THRESHOLDS: Final[dict[str, float]] = {
    "watchlist_score": 35.0,
    "promising_score": 55.0,
    "promising_confidence": 45.0,
    "strong_score": 70.0,
    "strong_confidence": 65.0,
    #: A fall of this many points from the peak means WEAKENING.
    "weakening_drop": 18.0,
}

# ------------------------------------------------------------ personal relevance
RELEVANCE_MAX: Final[dict[str, int]] = {
    "geography": 25,
    "type_priority": 20,
    "capital_fit": 15,
    "industry_experience": 10,
    "supplier_access": 8,
    "distribution_access": 8,
    "technical_fit": 7,
    "regulatory_access": 7,
}

#: Capital bands used when an opportunity states a capital requirement.
TECH_LEVEL_RANK: Final[dict[str, int]] = {"low": 0, "medium": 1, "high": 2}

# --------------------------------------------------------- required evidence sets
#: What a *complete* case looks like for each opportunity type. A missing item is
#: named in the report as missing rather than quietly skipped.
REQUIRED_EVIDENCE: Final[dict[str, tuple[str, ...]]] = {
    "business": ("adoption", "demand_pain", "competition", "willingness_to_pay"),
    "import_distribution": (
        "trade",
        "local_availability",
        "supplier",
        "shipping_economics",
        "price",
    ),
    "public_investment": ("financial", "revenue_growth", "valuation", "competitive_position"),
    "crypto": ("adoption", "developer", "liquidity", "token_distribution", "audit", "team"),
}

#: Crypto can never be labelled low risk. This is a floor, not a default.
CRYPTO_MIN_RISK: Final[str] = "high"
