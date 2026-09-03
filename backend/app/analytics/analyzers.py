"""Type-specific analyzers: business, import/distribution, public company, crypto.

Each analyzer turns stored evidence into the qualitative inputs the scorer needs,
and each obeys the same discipline: **an unknown is written down as UNKNOWN and
scores nothing**. None of them invents a market size, a price, a supplier, a
margin, a regulation or a willingness to pay.

They are pure functions over facts that were gathered elsewhere, so they can be
tested without a database and without a network.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

from app.analytics.opportunity_config import CRYPTO_MIN_RISK
from app.analytics.opportunity_scoring import EvidenceFact, OpportunityInput

UNKNOWN = "UNKNOWN"


@dataclass(slots=True)
class AnalyzerOutput:
    """What an analyzer contributes: enrichment, flags, and an audit trail."""

    analysis: dict[str, Any] = field(default_factory=dict)
    flags: set[str] = field(default_factory=set)
    evidence_kinds: set[str] = field(default_factory=set)
    missing_evidence: list[str] = field(default_factory=list)
    assumptions: list[str] = field(default_factory=list)
    participation: list[dict[str, str]] = field(default_factory=list)
    confirmation: list[dict[str, Any]] = field(default_factory=list)
    invalidation: list[dict[str, Any]] = field(default_factory=list)
    why_early: list[str] = field(default_factory=list)
    risk_hints: list[dict[str, Any]] = field(default_factory=list)
    #: A floor the risk engine must respect, e.g. crypto can never be "low".
    min_risk_level: str | None = None


def _facts_by_class(facts: list[EvidenceFact]) -> dict[str, list[EvidenceFact]]:
    out: dict[str, list[EvidenceFact]] = {}
    for f in facts:
        out.setdefault(f.signal_class, []).append(f)
    return out


def _growth(facts: list[EvidenceFact]) -> float | None:
    """Mean 30-day growth, rounded. Full float precision here is false accuracy:
    these are averages of noisy series, and the extra digits only mislead."""
    values = [f.growth_30d for f in facts if f.growth_30d is not None]
    return round(sum(values) / len(values), 1) if values else None


# --------------------------------------------------------------------- business
def analyse_business(inp: OpportunityInput, context: dict[str, Any]) -> AnalyzerOutput:
    """Is there a business here, and could this person actually run it?

    `context` carries whatever the evidence layer could establish. Anything absent
    from it stays UNKNOWN — notably willingness to pay, which is never inferred
    from enthusiasm.
    """
    out = AnalyzerOutput()
    by_class = _facts_by_class(inp.facts)

    problem = context.get("problem") or UNKNOWN
    customer = context.get("customer") or UNKNOWN
    pain_facts = by_class.get("demand_pain", [])
    pain_growth = _growth(pain_facts)

    if pain_facts:
        out.evidence_kinds.add("demand_pain")
        pain = "evidenced"
    else:
        pain = UNKNOWN
        out.missing_evidence.append(
            "No measurement of how often or how badly this problem is felt "
            "(complaints, unmet-need mentions, churn)."
        )

    alternatives = context.get("existing_alternatives") or []
    competitors = context.get("competitor_count")
    if competitors is None:
        out.missing_evidence.append("Number of existing providers is unknown.")

    # Willingness to pay is the single most over-claimed number in this domain.
    wtp = context.get("willingness_to_pay")
    if wtp is None:
        wtp = UNKNOWN
        out.missing_evidence.append(
            "Nobody has been shown to pay for this. Willingness to pay is UNKNOWN and is "
            "not inferred from interest."
        )
    else:
        out.evidence_kinds.add("willingness_to_pay")

    mvp = (context.get("mvp_difficulty") or UNKNOWN).upper()
    capital = context.get("capital_required_usd")
    if capital is None:
        out.assumptions.append("Required capital has no evidence behind it.")

    acquisition = context.get("acquisition_difficulty") or UNKNOWN
    recurring = context.get("recurring_revenue_potential") or UNKNOWN
    retention = context.get("retention_potential") or UNKNOWN

    if by_class.get("developer") or by_class.get("commercial"):
        out.evidence_kinds.add("adoption")
    if by_class.get("commercial"):
        out.evidence_kinds.add("commercial")
    if competitors is not None:
        out.evidence_kinds.add("competition")

    # A validation experiment, phrased so its result is clearly not yet known.
    audience = customer if customer != UNKNOWN else "people with this problem"
    experiment = context.get("validation_experiment") or (
        f"Interview 15 {audience} and record how many already pay for a workaround, and "
        f"how much. Treat the result as unknown until the interviews are done."
    )

    out.analysis = {
        "problem": problem,
        "customer": customer,
        "pain": pain,
        "pain_growth_30d": pain_growth,
        "existing_alternatives": alternatives or UNKNOWN,
        "competitor_count": competitors if competitors is not None else UNKNOWN,
        "willingness_to_pay": wtp,
        "mvp_difficulty": mvp,
        "capital_required_usd": capital if capital is not None else UNKNOWN,
        "acquisition_difficulty": acquisition,
        "recurring_revenue_potential": recurring,
        "retention_potential": retention,
        "defensibility": context.get("defensibility_markers") or [],
        "local_relevance": context.get("local_relevance") or UNKNOWN,
        "validation_experiment": experiment,
        "experiment_status": "not yet performed",
    }

    if wtp == UNKNOWN and not by_class.get("commercial"):
        out.flags.add("weak_adoption_evidence")
    if not context.get("defensibility_markers"):
        out.flags.add("easily_copied")
    if competitors is not None and competitors >= 15:
        out.flags.add("extreme_competition")
    if capital is not None and capital > 250_000:
        out.flags.add("excessive_capital")

    out.participation = [
        {
            "kind": "build",
            "description": f"Build a first version aimed at {audience}.",
            "difficulty": mvp.lower() if mvp != UNKNOWN else "unknown",
        },
        {
            "kind": "provide_service",
            "description": "Offer the same outcome as a service before building any software, which "
            "tests demand at a fraction of the cost.",
            "difficulty": "low",
        },
        {
            "kind": "learn_skill",
            "description": "Learn the underlying skill and sell it directly while the market forms.",
            "difficulty": "low",
        },
    ]
    out.confirmation = [
        {
            "description": "At least 5 of 15 interviewed customers state a price they would pay.",
            "measurable": {"kind": "interview", "target": 5, "of": 15},
        },
        {
            "description": "Adoption signals keep rising for another 90 days.",
            "measurable": {"signal_class": "developer", "comparator": "gte", "value": 0.0, "window_days": 90},
        },
        {
            "description": "A paying customer signs before any product is finished.",
            "measurable": {"kind": "contract", "target": 1},
        },
    ]
    out.invalidation = [
        {
            "description": "Fewer than 2 of 15 interviewed customers will name any price.",
            "measurable": {"kind": "interview", "below": 2, "of": 15},
        },
        {
            "description": "Adoption activity falls for 90 consecutive days.",
            "measurable": {"signal_class": "developer", "comparator": "lt", "value": 0.0, "window_days": 90},
        },
        {
            "description": "An incumbent ships the same capability for free.",
            "measurable": {"kind": "competitive_event"},
        },
    ]
    return out


# ---------------------------------------------------------- import/distribution
def analyse_import(inp: OpportunityInput, context: dict[str, Any]) -> AnalyzerOutput:
    """Could this product be imported and sold here at a sensible margin?

    The interesting pattern is geographic lag: demand rising elsewhere while the
    product is still uncommon locally. That is a *hypothesis*, not a conclusion —
    local demand, competition, regulation, affordability and shipping economics
    all have to survive scrutiny before it scores well.
    """
    out = AnalyzerOutput()
    by_class = _facts_by_class(inp.facts)
    trade_facts = by_class.get("trade", [])
    if trade_facts:
        out.evidence_kinds.update({"trade", "adoption"})

    local_availability = context.get("local_availability") or UNKNOWN
    local_competitors = context.get("local_competitor_count")
    suppliers = context.get("supplier_count")
    concentration = context.get("manufacturing_concentration") or UNKNOWN
    moq = context.get("likely_moq") or UNKNOWN
    weight_kg = context.get("unit_weight_kg")
    cbm = context.get("unit_cbm")
    fragility = context.get("fragility") or UNKNOWN
    customs = context.get("customs_requirements") or UNKNOWN
    certification = context.get("certification") or UNKNOWN
    warranty = context.get("warranty_expectation") or UNKNOWN
    after_sales = context.get("after_sales_need") or UNKNOWN
    seasonality = context.get("seasonality") or UNKNOWN
    education = context.get("market_education_needed") or UNKNOWN
    margin = context.get("margin_evidence")
    price = context.get("price_evidence")

    if suppliers is not None:
        out.evidence_kinds.add("supplier")
    else:
        out.missing_evidence.append("Number of available suppliers is unknown.")
    if local_availability == UNKNOWN:
        out.missing_evidence.append(
            "Local availability has not been checked. Geographic lag cannot be claimed without it."
        )
    else:
        out.evidence_kinds.add("local_availability")
    if margin is None:
        out.missing_evidence.append(
            "No evidence of achievable margin. Landed cost versus local retail price is "
            "the number that decides this, and it is not known."
        )
        out.assumptions.append("Margin is unproven.")
    else:
        out.evidence_kinds.add("shipping_economics")
    if price is None:
        out.missing_evidence.append("No local price evidence.")
    else:
        out.evidence_kinds.add("price")

    shipping = context.get("shipping_difficulty")
    if shipping is None and (weight_kg is not None or cbm is not None):
        heavy = (weight_kg or 0) > 30 or (cbm or 0) > 0.5
        shipping = "high" if heavy else "medium"
        out.assumptions.append("Shipping difficulty inferred from weight and volume only.")
    shipping = shipping or UNKNOWN

    out.analysis = {
        "global_demand_growth_30d": _growth(trade_facts),
        "local_availability": local_availability,
        "local_competitor_count": local_competitors if local_competitors is not None else UNKNOWN,
        "supplier_count": suppliers if suppliers is not None else UNKNOWN,
        "manufacturing_concentration": concentration,
        "likely_moq": moq,
        "unit_weight_kg": weight_kg if weight_kg is not None else UNKNOWN,
        "unit_cbm": cbm if cbm is not None else UNKNOWN,
        "fragility": fragility,
        "shipping_difficulty": shipping,
        "customs_requirements": customs,
        "certification": certification,
        "warranty_expectation": warranty,
        "after_sales_need": after_sales,
        "seasonality": seasonality,
        "market_education_needed": education,
        "wholesale_potential": context.get("wholesale_potential") or UNKNOWN,
        "retail_potential": context.get("retail_potential") or UNKNOWN,
        "margin_evidence": margin if margin is not None else UNKNOWN,
        "price_evidence": price if price is not None else UNKNOWN,
    }

    if concentration in {"single_country", "single_factory"}:
        out.flags.add("supply_chain_fragility")
    if suppliers is not None and suppliers <= 1:
        out.flags.add("single_supplier")
    if certification not in {UNKNOWN, "none", "not_required"} and certification:
        out.flags.add("regulatory_uncertainty")
    if margin is None:
        out.flags.add("poor_economics")
    if str(education).lower() == "high":
        out.flags.add("difficult_local_execution")

    if local_availability in {"none", "low"}:
        out.why_early.append(
            f"The product is {local_availability} in the target market while demand is "
            "measurably rising elsewhere."
        )
    if local_competitors is not None and local_competitors <= 2:
        out.why_early.append(f"Only {local_competitors} local competitor(s) identified.")

    out.participation = [
        {
            "kind": "import",
            "description": "Place a small trial order to test customs, landed cost and real demand "
            "before committing to volume.",
            "difficulty": "medium",
        },
        {
            "kind": "distribute",
            "description": "Approach existing retailers as a wholesaler rather than selling directly.",
            "difficulty": "medium",
        },
        {
            "kind": "partner",
            "description": "Partner with an established importer who already has customs clearance.",
            "difficulty": "low",
        },
    ]
    out.confirmation = [
        {
            "description": "Imports into the target market rise for two consecutive quarters.",
            "measurable": {
                "signal_type": "import_growth",
                "comparator": "gt",
                "value": 0.0,
                "window_days": 180,
            },
        },
        {
            "description": "Landed cost lands below local retail price with a workable margin.",
            "measurable": {"kind": "margin", "comparator": "gte", "value": 0.25},
        },
        {
            "description": "The local distributor count stays low for another 6 months.",
            "measurable": {
                "signal_type": "supplier_count_change",
                "comparator": "lte",
                "value": 0.0,
                "window_days": 180,
            },
        },
    ]
    out.invalidation = [
        {
            "description": "A large distributor enters and takes shelf space.",
            "measurable": {
                "signal_type": "supplier_count_change",
                "comparator": "gt",
                "value": 50.0,
                "window_days": 180,
            },
        },
        {
            "description": "The product fails local certification.",
            "measurable": {"kind": "certification", "expected": "pass"},
        },
        {
            "description": "Shipping or customs cost erases the margin.",
            "measurable": {"kind": "margin", "comparator": "lt", "value": 0.10},
        },
        {
            "description": "Import activity falls for 90 days.",
            "measurable": {
                "signal_type": "import_growth",
                "comparator": "lt",
                "value": 0.0,
                "window_days": 90,
            },
        },
    ]
    return out


# ------------------------------------------------------------- public companies
def analyse_public_company(inp: OpportunityInput, context: dict[str, Any]) -> AnalyzerOutput:
    """Business quality and valuation are two questions, kept apart.

    "AI is growing, therefore every AI company is a good investment" is the exact
    error this analyzer exists to refuse. Trend exposure is measured separately
    from business quality, and business quality separately from price.
    """
    out = AnalyzerOutput()

    revenue_growth = context.get("revenue_growth")
    segment_growth = context.get("segment_growth")
    gross_margin = context.get("gross_margin")
    operating_margin = context.get("operating_margin")
    fcf = context.get("free_cash_flow")
    debt_to_equity = context.get("debt_to_equity")
    capex_ratio = context.get("capex_ratio")
    customer_concentration = context.get("customer_concentration")
    position = context.get("competitive_position") or UNKNOWN

    for name, value in {
        "revenue growth": revenue_growth,
        "gross margin": gross_margin,
        "free cash flow": fcf,
        "debt level": debt_to_equity,
    }.items():
        if value is None:
            out.missing_evidence.append(f"Reported {name} is not available.")
    if revenue_growth is not None:
        out.evidence_kinds.update({"financial", "revenue_growth"})

    # Trend exposure: how much of the business actually touches the trend?
    exposure = context.get("trend_exposure")
    if exposure is None:
        exposure = UNKNOWN
        out.missing_evidence.append(
            "It is not established how much of this company's revenue actually comes from "
            "the detected trend. Exposure is assumed to be zero until shown otherwise."
        )
    else:
        out.evidence_kinds.add("competitive_position")

    # Valuation is only assessed on fresh price data. A stale price is worse than none.
    price_age_days = context.get("price_age_days")
    valuation: dict[str, Any]
    if price_age_days is None or price_age_days > 5:
        valuation = {
            "status": "NOT ASSESSED",
            "reason": (
                "No current market price is available"
                + (f" (newest price is {price_age_days} days old)" if price_age_days else "")
                + ". A valuation from a stale price would be misleading, so none is given."
            ),
        }
        out.missing_evidence.append("Current market price is unavailable, so valuation is not assessed.")
        out.assumptions.append("No valuation could be formed.")
    else:
        valuation = {
            "status": "assessed",
            "price_age_days": price_age_days,
            "measures": context.get("valuation_measures") or {},
            "band": context.get("valuation_band") or UNKNOWN,
        }
        out.evidence_kinds.add("valuation")
        if (context.get("valuation_band") or "").lower() == "extreme":
            out.flags.add("extreme_valuation")

    if debt_to_equity is not None and debt_to_equity > 2.0:
        out.flags.add("poor_economics")
        out.risk_hints.append(
            {
                "code": "high_leverage",
                "category": "financial",
                "severity": "high",
                "rationale": f"Debt-to-equity of {debt_to_equity:.1f} leaves little room for error.",
            }
        )
    if operating_margin is not None and operating_margin < 0:
        out.flags.add("poor_economics")
        out.risk_hints.append(
            {
                "code": "unprofitable",
                "category": "financial",
                "severity": "high",
                "rationale": f"Operating margin is {operating_margin:.1%}.",
            }
        )
    if customer_concentration is not None and customer_concentration > 0.3:
        out.flags.add("single_customer")
        out.risk_hints.append(
            {
                "code": "customer_concentration",
                "category": "customer_concentration",
                "severity": "high",
                "rationale": f"{customer_concentration:.0%} of revenue comes from one customer.",
            }
        )

    out.analysis = {
        "revenue_growth": revenue_growth if revenue_growth is not None else UNKNOWN,
        "segment_growth": segment_growth if segment_growth is not None else UNKNOWN,
        "gross_margin": gross_margin if gross_margin is not None else UNKNOWN,
        "operating_margin": operating_margin if operating_margin is not None else UNKNOWN,
        "free_cash_flow": fcf if fcf is not None else UNKNOWN,
        "debt_to_equity": debt_to_equity if debt_to_equity is not None else UNKNOWN,
        "capex_ratio": capex_ratio if capex_ratio is not None else UNKNOWN,
        "customer_concentration": (customer_concentration if customer_concentration is not None else UNKNOWN),
        "competitive_position": position,
        "trend_exposure": exposure,
        "valuation": valuation,
        "catalysts": context.get("catalysts") or [],
        "research_status": "RESEARCH",
    }

    out.participation = [
        {
            "kind": "investigate_public_company",
            "description": "Read the last two annual filings and check how much revenue actually comes "
            "from the trend before forming any view.",
            "difficulty": "low",
        },
        {
            "kind": "watch",
            "description": "Track the segment's reported revenue for two more quarters.",
            "difficulty": "low",
        },
    ]
    out.confirmation = [
        {
            "description": "Segment revenue tied to the trend grows for two consecutive quarters.",
            "measurable": {
                "signal_type": "revenue_acceleration",
                "comparator": "gt",
                "value": 0.0,
                "window_days": 180,
            },
        },
        {
            "description": "Gross margin holds or improves while revenue grows.",
            "measurable": {"kind": "margin", "comparator": "gte", "value": gross_margin or 0.0},
        },
        {
            "description": "Debt falls or free cash flow turns positive.",
            "measurable": {"kind": "cash_flow", "comparator": "gt", "value": 0.0},
        },
    ]
    out.invalidation = [
        {
            "description": "Revenue growth stalls for two consecutive quarters.",
            "measurable": {
                "signal_type": "revenue_acceleration",
                "comparator": "lte",
                "value": 0.0,
                "window_days": 180,
            },
        },
        {
            "description": "Gross margin collapses.",
            "measurable": {"kind": "margin", "comparator": "lt", "value": 0.15},
        },
        {"description": "The largest customer does not renew.", "measurable": {"kind": "contract_event"}},
        {
            "description": "A competitor cuts price sharply across the segment.",
            "measurable": {"kind": "competitive_event"},
        },
    ]
    return out


# ----------------------------------------------------------------------- crypto
#: Automatic flags. Each one is a published, checkable property, not a vibe.
CRYPTO_FLAGS: dict[str, str] = {
    "meme_coin": "No stated utility beyond the token itself.",
    "anonymous_team": "The people behind it are not identifiable.",
    "low_liquidity": "Too thin to exit without moving the price.",
    "concentrated_ownership": "A small number of wallets hold most of the supply.",
    "no_product": "Nothing is shipped that anyone uses.",
    "unverified_contract": "The contract source is not published or verified.",
    "suspicious_volume": "Trading volume does not match holder or transaction counts.",
    "promotional_hype": "Coverage is paid or coordinated rather than organic.",
    "ponzi_like_incentives": "Returns depend on new buyers rather than on revenue.",
}


def analyse_crypto(inp: OpportunityInput, context: dict[str, Any]) -> AnalyzerOutput:
    """Stricter rules, and a hard risk floor.

    Crypto can never be classified low risk by this system. Not as a default that
    evidence could overturn — as a floor.
    """
    out = AnalyzerOutput()
    out.min_risk_level = CRYPTO_MIN_RISK
    by_class = _facts_by_class(inp.facts)

    users = context.get("active_users")
    dev = by_class.get("developer", [])
    network_activity = context.get("network_activity")
    utility = context.get("utility") or UNKNOWN
    fees = context.get("protocol_revenue")
    distribution = context.get("token_distribution") or {}
    insider_share = distribution.get("insider_share")
    top10_share = distribution.get("top10_share")
    unlocks = context.get("unlock_schedule") or UNKNOWN
    liquidity_usd = context.get("liquidity_usd")
    exchange_concentration = context.get("exchange_concentration")
    audits = context.get("audits") or []
    founders_known = context.get("founders_identified")
    governance = context.get("governance") or UNKNOWN
    bot_share = context.get("bot_activity_share")
    influencer = context.get("influencer_promotion")

    if dev:
        out.evidence_kinds.add("developer")
    if users is not None:
        out.evidence_kinds.add("adoption")
    else:
        out.missing_evidence.append("Number of real users is unknown.")
    if liquidity_usd is not None:
        out.evidence_kinds.add("liquidity")
    else:
        out.missing_evidence.append("Liquidity depth is unknown.")
    if distribution:
        out.evidence_kinds.add("token_distribution")
    else:
        out.missing_evidence.append("Token distribution is unknown.")
    if audits:
        out.evidence_kinds.add("audit")
    else:
        out.missing_evidence.append("No smart-contract audit found.")
    if founders_known is not None:
        out.evidence_kinds.add("team")

    flags: set[str] = set()
    if utility in {UNKNOWN, "none"} and not dev:
        flags.add("meme_coin")
        flags.add("no_product")
    if founders_known is False:
        flags.add("anonymous_team")
        out.flags.add("anonymous_team")
    if liquidity_usd is not None and liquidity_usd < 250_000:
        flags.add("low_liquidity")
        out.flags.add("poor_liquidity")
    if top10_share is not None and top10_share > 0.5:
        flags.add("concentrated_ownership")
        out.flags.add("concentrated_ownership")
    if insider_share is not None and insider_share > 0.3:
        flags.add("concentrated_ownership")
        out.flags.add("concentrated_ownership")
    if not audits:
        flags.add("unverified_contract")
    if bot_share is not None and bot_share > 0.4:
        flags.add("suspicious_volume")
        out.flags.add("promotional_manipulation")
    if influencer:
        flags.add("promotional_hype")
        out.flags.add("promotional_manipulation")
    if context.get("returns_depend_on_inflow"):
        flags.add("ponzi_like_incentives")
    if exchange_concentration is not None and exchange_concentration > 0.7:
        out.flags.add("poor_liquidity")

    attention = by_class.get("attention", [])
    if attention and not (users or dev):
        out.flags.add("attention_without_adoption")

    out.analysis = {
        "active_users": users if users is not None else UNKNOWN,
        "developer_activity_30d": _growth(dev),
        "network_activity": network_activity if network_activity is not None else UNKNOWN,
        "utility": utility,
        "protocol_revenue": fees if fees is not None else UNKNOWN,
        "token_distribution": distribution or UNKNOWN,
        "unlock_schedule": unlocks,
        "liquidity_usd": liquidity_usd if liquidity_usd is not None else UNKNOWN,
        "exchange_concentration": (exchange_concentration if exchange_concentration is not None else UNKNOWN),
        "audits": audits or UNKNOWN,
        "founders_identified": founders_known if founders_known is not None else UNKNOWN,
        "governance": governance,
        "bot_activity_share": bot_share if bot_share is not None else UNKNOWN,
        "influencer_promotion": influencer if influencer is not None else UNKNOWN,
        "automatic_flags": {f: CRYPTO_FLAGS[f] for f in sorted(flags)},
        "risk_floor": CRYPTO_MIN_RISK,
    }

    for f in sorted(flags):
        out.risk_hints.append(
            {
                "code": f,
                "category": "fraud_manipulation"
                if f
                in {
                    "promotional_hype",
                    "suspicious_volume",
                    "ponzi_like_incentives",
                    "anonymous_team",
                    "unverified_contract",
                }
                else "liquidity"
                if f in {"low_liquidity", "concentrated_ownership"}
                else "market",
                "severity": "blocking" if f in {"ponzi_like_incentives"} else "high",
                "rationale": CRYPTO_FLAGS[f],
            }
        )

    out.participation = [
        {
            "kind": "watch",
            "description": "Watch only. Record whether real usage appears before price does.",
            "difficulty": "low",
        },
        {
            "kind": "investigate_public_company",
            "description": "If the underlying technology matters, look for a regulated company exposed "
            "to it instead.",
            "difficulty": "low",
        },
    ]
    out.confirmation = [
        {
            "description": "Active users grow for 90 days without a matching price move.",
            "measurable": {"kind": "users", "comparator": "gt", "value": 0.0, "window_days": 90},
        },
        {
            "description": "A published audit from a recognised firm appears.",
            "measurable": {"kind": "audit", "target": 1},
        },
        {
            "description": "Top-10 wallet concentration falls below 30%.",
            "measurable": {"kind": "top10_share", "comparator": "lt", "value": 0.3},
        },
    ]
    out.invalidation = [
        {
            "description": "Liquidity falls below the level needed to exit a normal position.",
            "measurable": {"kind": "liquidity_usd", "comparator": "lt", "value": 250_000},
        },
        {"description": "A large unlock hits the market.", "measurable": {"kind": "unlock_event"}},
        {
            "description": "Developer activity stops for 60 days.",
            "measurable": {"signal_class": "developer", "comparator": "lte", "value": 0.0, "window_days": 60},
        },
    ]
    return out


ANALYZERS = {
    "business": analyse_business,
    "import_distribution": analyse_import,
    "public_investment": analyse_public_company,
    "crypto": analyse_crypto,
}


def run_analyzer(inp: OpportunityInput, context: dict[str, Any]) -> AnalyzerOutput:
    analyzer = ANALYZERS.get(inp.opportunity_type)
    if analyzer is None:
        return AnalyzerOutput(analysis={"status": UNKNOWN})
    return analyzer(inp, context)
