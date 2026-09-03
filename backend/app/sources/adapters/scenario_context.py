"""Analyzer facts for the demo scenarios.

A time series cannot tell you how many suppliers exist, what a company's debt is,
or who owns a token. In production those facts arrive from filings, trade
registries, chain explorers and manual research. Here they are written down, once,
so the demo exercises the real analyzers rather than a stubbed version of them.

It lives beside the scenario adapter rather than in `scripts/` because the API
serves it too, and the application must never import from a maintenance script.

Every value here is **synthetic**. That is why every opportunity generated from
this file is stored with `validation_status = DEMO` and labelled as such on every
screen. Fields deliberately left out stay UNKNOWN and score nothing — the same
behaviour real missing data produces.
"""

from __future__ import annotations

from typing import Any

#: Keyed by "<trend name>|<opportunity type>", falling back to "<trend name>".
SCENARIO_CONTEXT: dict[str, dict[str, Any]] = {
    # ---------------------------------------------------- A: real business case
    "edge inference runtime|business": {
        "title": "Edge inference tooling — build or serve",
        "slug": "business-edge-inference-runtime",
        "summary": (
            "Running models on local hardware instead of in a data centre is being adopted "
            "by developers faster than it is being written about."
        ),
        "thesis": (
            "Contributor counts, package downloads and job postings for edge inference are "
            "all rising together while press coverage stays flat, which is the shape of a "
            "technology being used rather than discussed. Teams adopting it hit the same "
            "integration problems repeatedly, and nobody sells a solution to those problems yet."
        ),
        "mechanism": (
            "As models get small enough to run on ordinary hardware, the bottleneck moves "
            "from model quality to deployment plumbing. Whoever sells the plumbing is paid "
            "regardless of which model wins."
        ),
        "problem": "Deploying and updating models on hardware you do not control",
        "customer": "small engineering teams shipping on-device features",
        "competitor_count": 3,
        "existing_alternatives": ["hand-rolled scripts", "cloud inference at higher cost"],
        # Deliberately absent: willingness_to_pay. Nobody has been shown to pay.
        "mvp_difficulty": "medium",
        "capital_required_usd": 25_000,
        "capital_evidence": "Two engineers for four months at prevailing contract rates.",
        "acquisition_difficulty": "medium",
        "recurring_revenue_potential": "high",
        "retention_potential": "high",
        "defensibility_markers": ["switching cost once embedded in a build pipeline"],
        "market_size_band": "medium",
        "market_size_evidence": "Banded from observed download volume, not from a market report.",
        "local_penetration": "low",
        "awareness": "low",
        "technical_difficulty": "medium",
        "catalyst": "Cheap NPU-equipped hardware reaching general availability",
        "catalyst_is_dated": True,
        "catalyst_evidence_id": "scenario:edge_inference:capex",
        "next_research_steps": [
            "Interview 15 teams already using this and record what they built themselves.",
            "Check whether any of the three competitors publishes pricing.",
            "Establish whether the integration problem is felt monthly or yearly.",
        ],
    },
    # -------------------------------------------------------- B: hype, no case
    "quantum wellness devices|business": {
        "title": "Quantum wellness devices — business",
        "slug": "business-quantum-wellness",
        "summary": "Very loud, very sustained coverage over an order book that is not moving.",
        "thesis": ("Coverage and social discussion of these devices have risen enormously over six months."),
        "mechanism": "None established.",
        "problem": "Not established",
        "customer": "Not established",
        "competitor_count": 22,
        "market_size_band": "unknown",
        "awareness": "high",
        "technical_difficulty": "high",
        "mvp_difficulty": "HIGH",
    },
    # -------------------------------------- C: real trend, poor investment case
    "ThermaCore Industries|public_investment": {
        "title": "ThermaCore Industries — liquid cooling exposure",
        "slug": "equity-thermacore",
        "summary": (
            "The cooling market is growing quickly. This particular company carries the debt "
            "and the price of a much better one."
        ),
        "thesis": (
            "Data-centre liquid cooling demand is rising sharply on capital-expenditure "
            "announcements, import volumes and hiring. ThermaCore sells into that market."
        ),
        "mechanism": (
            "Denser compute produces more heat than air can remove, so cooling spend rises "
            "with compute spend."
        ),
        "revenue_growth": 0.09,
        "segment_growth": 0.14,
        "gross_margin": 0.19,
        "operating_margin": -0.04,
        "free_cash_flow": -38_000_000,
        "debt_to_equity": 3.4,
        "capex_ratio": 0.22,
        "customer_concentration": 0.41,
        "competitive_position": "third of four suppliers, competing largely on price",
        "trend_exposure": 0.23,
        "price_age_days": 1,
        "valuation_band": "extreme",
        "valuation_measures": {
            "ev_to_sales": 11.4,
            "ev_to_ebitda": "not meaningful (EBITDA negative)",
            "note": "Multiples are from the stored price snapshot, not estimated.",
        },
        "catalysts": ["A large customer's next build cycle"],
        "market_size_band": "large",
        "market_size_evidence": "Banded from observed capex announcement volume.",
        "awareness": "high",
        "technical_difficulty": "low",
        "next_research_steps": [
            "Read the last two annual filings and separate cooling revenue from the rest.",
            "Check whether the 41% customer is contracted beyond the current year.",
            "Compare gross margin against the two larger suppliers.",
        ],
    },
    # ------------------------------------------------- D: geographic import gap
    "solar water pumps|import_distribution": {
        "title": "Solar water pumps — import into Libya",
        "slug": "import-solar-water-pumps-ly",
        "summary": (
            "Exports are rising sharply from China and imports across Europe, while the "
            "local market shows almost nothing."
        ),
        "thesis": (
            "Solar water pump exports from China and imports into Europe are both rising "
            "steeply, supplier counts are growing, and measured local import volume is near "
            "zero. Diesel pumping is the incumbent, and fuel availability is unreliable."
        ),
        "mechanism": (
            "Panel prices fell far enough that a solar pump pays for itself against diesel "
            "within a season or two, which changes the purchase from a luxury to a cost decision."
        ),
        "local_availability": "low",
        "local_competitor_count": 2,
        "supplier_count": 34,
        "manufacturing_concentration": "single_country",
        "likely_moq": "50 units",
        "unit_weight_kg": 18.0,
        "unit_cbm": 0.09,
        "fragility": "low",
        "customs_requirements": "standard machinery tariff; no import licence identified",
        "certification": "none identified for agricultural use",
        "warranty_expectation": "24 months is standard in the source market",
        "after_sales_need": "medium — pump servicing, not electronics repair",
        "seasonality": "planting season concentrates demand",
        "market_education_needed": "medium",
        "wholesale_potential": "agricultural suppliers and cooperatives",
        "retail_potential": "limited; this is a wholesale product",
        "margin_evidence": (
            "Observed export unit values against observed local retail listings imply a "
            "gross margin band of 22-30% before shipping."
        ),
        "price_evidence": "Local retail listings for comparable diesel pumps.",
        "market_size_band": "small",
        "market_size_evidence": "Banded from the target market's agricultural holdings count.",
        "local_penetration": "low",
        "awareness": "low",
        "competitor_count": 2,
        "capital_required_usd": 18_000,
        "capital_evidence": "50-unit MOQ at observed export unit value plus freight.",
        "defensibility_markers": ["first distributor relationship", "service capability"],
        "technical_difficulty": "low",
        "catalyst": "Continued diesel supply disruption",
        "catalyst_is_dated": False,
        "leader_markets": [
            {
                "geo": "CN",
                "adoption_growth": 88.0,
                "supplier_count": 34,
                "competitor_count": 26,
                "first_observed_days_ago": 900,
            },
            {
                "geo": "EU",
                "adoption_growth": 52.0,
                "supplier_count": 20,
                "competitor_count": 14,
                "first_observed_days_ago": 700,
            },
        ],
        "local_adoption_growth": 4.0,
        "local_competitor_count_target": 2,
        "local_first_observed_days_ago": 150,
        "next_research_steps": [
            "Get three landed-cost quotes including freight and customs.",
            "Visit four agricultural suppliers and count how many already stock any solar pump.",
            "Confirm no import licence is required for this tariff code.",
        ],
    },
    # ----------------------------------------- E: real trend, nowhere to stand
    "EUV lithography capacity|business": {
        "title": "EUV lithography capacity",
        "slug": "business-euv-lithography",
        "summary": "A real and important trend with no accessible way to take part.",
        "thesis": (
            "Capital expenditure, patent filings and export volumes around EUV lithography "
            "capacity are all rising together."
        ),
        "mechanism": "Advanced chip demand requires more of an extremely scarce machine.",
        "competitor_count": 1,
        "market_size_band": "large",
        "market_size_evidence": "Banded from observed capex announcement values.",
        "awareness": "high",
        "technical_difficulty": "high",
        "capital_required_usd": 400_000_000,
        "capital_evidence": "A single tool's published order value.",
        "accessibility_barriers": [
            "One manufacturer worldwide, with a multi-year order book",
            "Export controls restrict who may buy",
            "Capital requirement is four orders of magnitude above any stated ceiling",
        ],
        "mvp_difficulty": "HIGH",
    },
    # ------------------------------------------------------------ F: scam token
    "Luna9 Token|crypto": {
        "title": "Luna9 Token",
        "slug": "crypto-luna9",
        "summary": "Loud, illiquid, anonymous and concentrated.",
        "thesis": "Social discussion and trading volume have risen very sharply.",
        "mechanism": "None established.",
        "utility": "none",
        "active_users": None,
        "network_activity": None,
        "protocol_revenue": None,
        "token_distribution": {"top10_share": 0.78, "insider_share": 0.41},
        "unlock_schedule": "40% unlocks in one tranche in 60 days",
        "liquidity_usd": 90_000,
        "exchange_concentration": 0.92,
        "audits": [],
        "founders_identified": False,
        "governance": "none",
        "bot_activity_share": 0.61,
        "influencer_promotion": True,
        "returns_depend_on_inflow": True,
        "market_size_band": "unknown",
        "awareness": "high",
        "technical_difficulty": "low",
    },
}
