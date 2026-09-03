"""Editable scoring configuration.

Changing anything here REQUIRES bumping FORMULA_VERSION and regenerating
tests/data/scoring_vectors.json in the same commit (see docs/contributing.md).
"""

from __future__ import annotations

FORMULA_VERSION = "1.0.0"

COMPONENT_MAX: dict[str, int] = {
    "real_adoption_growth": 20,
    "market_size": 15,
    "signal_acceleration": 15,
    "independent_source_confirmation": 15,
    "entry_attractiveness": 10,
    "defensibility": 10,
    "clear_catalyst": 10,
    "accessibility_liquidity": 5,
}

PENALTY_POINTS: dict[str, int] = {
    "manipulation_risk": 25,
    "anonymous_team": 10,
    "concentrated_ownership": 10,
    "thin_liquidity": 10,
    "extreme_valuation": 10,
    "no_working_product": 10,
    "paid_promotion_dependence": 10,
    "weak_evidence": 10,
    "regulatory_danger": 10,
    "single_source_dependence": 8,
    "unsustainable_growth": 8,
    "high_competition": 6,
    "operational_difficulty": 6,
}

MAX_TOTAL_PENALTY = 60

# Market size bands in USD. `None` (unknown) scores zero - never guessed.
MARKET_SIZE_BANDS: list[tuple[float, int]] = [
    (10_000_000_000, 15),
    (1_000_000_000, 12),
    (100_000_000, 9),
    (10_000_000, 6),
    (1_000_000, 3),
    (0, 1),
]

DEFENSIBILITY_MARKERS: dict[str, int] = {
    "patent": 3,
    "network_effect": 3,
    "switching_cost": 2,
    "exclusive_distribution": 3,
    "regulatory_licence": 2,
    "proprietary_data": 3,
    "brand": 1,
    "capital_barrier": 2,
}

ACCESSIBILITY_POINTS: dict[str, int] = {
    "buildable_solo": 10,
    "importable": 8,
    "partnerable": 6,
    "publicly_tradable": 6,
    "restricted": 2,
    "unknown": 0,
}

LIQUIDITY_POINTS: dict[str, int] = {
    "high": 5,
    "medium": 3,
    "low": 1,
    "illiquid": 0,
    "not_applicable": 3,
    "unknown": 0,
}

BLOCKING_RISK_CODES: frozenset[str] = frozenset(
    {"confirmed_fraud", "honeypot_behaviour", "delisted", "sanctions_exposure"}
)

CRYPTO_ESCALATING_FLAGS: frozenset[str] = frozenset(
    {
        "anonymous_founders",
        "missing_audit",
        "unverified_contract",
        "concentrated_ownership",
        "honeypot_behaviour",
        "no_working_product",
    }
)

MAX_MEME_SCORE = 25

# Evidence fields each category must have before it can be called complete.
REQUIRED_EVIDENCE: dict[str, list[str]] = {
    "technology": ["adoption_metric", "developer_activity", "who_is_building", "timeline"],
    "public_investment": [
        "revenue",
        "margins",
        "valuation",
        "insider_or_institutional",
        "competitive_position",
    ],
    "business": [
        "problem_evidence",
        "existing_solutions",
        "willingness_to_pay",
        "market_size",
        "acquisition_channel",
    ],
    "import_distribution": [
        "demand_evidence",
        "local_availability",
        "supplier_evidence",
        "landed_cost_inputs",
        "certification_requirements",
    ],
}
