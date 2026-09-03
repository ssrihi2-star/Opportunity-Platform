"""The four type-specific analyzers, and the two experimental measures.

The recurring assertion: **an unknown is recorded as UNKNOWN and scores nothing.**
Nothing here may invent a market size, a price, a supplier, a margin, a regulation
or a willingness to pay.
"""

from __future__ import annotations

import pytest

from app.analytics.analyzers import (
    UNKNOWN,
    analyse_business,
    analyse_crypto,
    analyse_import,
    analyse_public_company,
    run_analyzer,
)
from app.analytics.experimental import (
    GeoObservation,
    adoption_to_attention,
    geographic_gap,
)
from app.analytics.opportunity_scoring import EvidenceFact
from tests.test_opportunity_gate import fact, good_input


# ------------------------------------------------------------------- business
def test_willingness_to_pay_is_never_inferred_from_interest():
    """Enthusiasm is not revenue. With no evidence, this stays UNKNOWN."""
    out = analyse_business(good_input(), context={})
    assert out.analysis["willingness_to_pay"] == UNKNOWN
    assert any("shown to pay" in m for m in out.missing_evidence)


def test_stated_willingness_to_pay_is_recorded():
    out = analyse_business(good_input(), context={"willingness_to_pay": "40 USD/month, 6 of 15"})
    assert out.analysis["willingness_to_pay"] != UNKNOWN
    assert "willingness_to_pay" in out.evidence_kinds


def test_an_unknown_competitor_count_is_named_as_missing():
    out = analyse_business(good_input(), context={})
    assert out.analysis["competitor_count"] == UNKNOWN
    assert any("existing providers" in m for m in out.missing_evidence)


def test_a_validation_experiment_is_generated_and_marked_not_yet_done():
    out = analyse_business(good_input(), context={"customer": "shop owners"})
    assert "shop owners" in out.analysis["validation_experiment"]
    assert out.analysis["experiment_status"] == "not yet performed"


def test_no_defensibility_marker_flags_easily_copied():
    out = analyse_business(good_input(), context={})
    assert "easily_copied" in out.flags


def test_business_always_offers_a_cheaper_way_to_test_demand():
    out = analyse_business(good_input(), context={})
    kinds = {p["kind"] for p in out.participation}
    assert "provide_service" in kinds


# ------------------------------------------------------------ import/distribution
IMPORT_CONTEXT = {
    "local_availability": "low",
    "local_competitor_count": 2,
    "supplier_count": 30,
    "manufacturing_concentration": "single_country",
    "unit_weight_kg": 18.0,
    "unit_cbm": 0.09,
    "margin_evidence": "22-30% before shipping",
    "price_evidence": "local retail listings",
}


def test_import_records_the_shipping_facts_it_has():
    out = analyse_import(good_input(opportunity_type="import_distribution"), IMPORT_CONTEXT)
    assert out.analysis["unit_weight_kg"] == 18.0
    assert out.analysis["shipping_difficulty"] in {"low", "medium", "high"}


def test_no_margin_evidence_flags_poor_economics_and_says_why():
    context = {**IMPORT_CONTEXT}
    context.pop("margin_evidence")
    out = analyse_import(good_input(opportunity_type="import_distribution"), context)
    assert "poor_economics" in out.flags
    assert any("margin" in m for m in out.missing_evidence)
    assert out.analysis["margin_evidence"] == UNKNOWN


def test_single_country_manufacturing_flags_supply_chain_fragility():
    out = analyse_import(good_input(opportunity_type="import_distribution"), IMPORT_CONTEXT)
    assert "supply_chain_fragility" in out.flags


def test_one_supplier_is_a_flag():
    out = analyse_import(
        good_input(opportunity_type="import_distribution"),
        {**IMPORT_CONTEXT, "supplier_count": 1},
    )
    assert "single_supplier" in out.flags


def test_low_local_availability_supports_why_it_may_be_early():
    out = analyse_import(good_input(opportunity_type="import_distribution"), IMPORT_CONTEXT)
    assert any("rising elsewhere" in w for w in out.why_early)


def test_geographic_lag_is_not_claimed_without_checking_local_availability():
    """A low competitor count is still evidence of headroom; the *lag* claim is not."""
    context = {**IMPORT_CONTEXT}
    context.pop("local_availability")
    out = analyse_import(good_input(opportunity_type="import_distribution"), context)
    assert out.analysis["local_availability"] == UNKNOWN
    assert any("Geographic lag cannot be claimed" in m for m in out.missing_evidence)
    assert not any("rising elsewhere" in w for w in out.why_early)


def test_import_invalidation_includes_certification_failure():
    out = analyse_import(good_input(opportunity_type="import_distribution"), IMPORT_CONTEXT)
    assert any("certification" in c["description"].lower() for c in out.invalidation)


# --------------------------------------------------------------- public company
def test_a_stale_price_produces_no_valuation_at_all():
    """A valuation from an old price is worse than no valuation."""
    out = analyse_public_company(good_input(opportunity_type="public_investment"), {"price_age_days": 40})
    assert out.analysis["valuation"]["status"] == "NOT ASSESSED"
    assert "stale" in out.analysis["valuation"]["reason"] or "days old" in out.analysis["valuation"]["reason"]


def test_a_missing_price_produces_no_valuation():
    out = analyse_public_company(good_input(opportunity_type="public_investment"), {})
    assert out.analysis["valuation"]["status"] == "NOT ASSESSED"


def test_a_fresh_price_allows_a_valuation():
    out = analyse_public_company(
        good_input(opportunity_type="public_investment"),
        {"price_age_days": 1, "valuation_band": "extreme"},
    )
    assert out.analysis["valuation"]["status"] == "assessed"
    assert "extreme_valuation" in out.flags


def test_trend_exposure_is_never_assumed():
    """'AI is growing, therefore every AI company benefits' is the error refused here."""
    out = analyse_public_company(good_input(opportunity_type="public_investment"), {})
    assert out.analysis["trend_exposure"] == UNKNOWN
    assert any("actually comes from the detected trend" in m for m in out.missing_evidence)


def test_high_leverage_becomes_a_financial_risk():
    out = analyse_public_company(good_input(opportunity_type="public_investment"), {"debt_to_equity": 3.4})
    assert "poor_economics" in out.flags
    assert any(h["category"] == "financial" for h in out.risk_hints)


def test_customer_concentration_becomes_its_own_risk_category():
    out = analyse_public_company(
        good_input(opportunity_type="public_investment"), {"customer_concentration": 0.41}
    )
    assert any(h["category"] == "customer_concentration" for h in out.risk_hints)


def test_a_public_company_is_research_never_a_buy_instruction():
    out = analyse_public_company(good_input(opportunity_type="public_investment"), {})
    assert out.analysis["research_status"] == "RESEARCH"
    text = " ".join(p["description"] for p in out.participation).lower()
    for banned in ("buy", "sell", "invest now"):
        assert banned not in text


# ---------------------------------------------------------------------- crypto
SCAM = {
    "utility": "none",
    "token_distribution": {"top10_share": 0.78, "insider_share": 0.41},
    "liquidity_usd": 90_000,
    "audits": [],
    "founders_identified": False,
    "bot_activity_share": 0.61,
    "influencer_promotion": True,
    "returns_depend_on_inflow": True,
}


def test_crypto_always_carries_a_risk_floor():
    out = analyse_crypto(good_input(opportunity_type="crypto"), {})
    assert out.min_risk_level == "high"
    assert out.analysis["risk_floor"] == "high"


def test_a_scam_shaped_token_raises_every_expected_flag():
    # No developer activity: a token with real engineering behind it is not a
    # meme coin, and the analyzer is right to withhold that flag when it sees one.
    noise_only = good_input(
        opportunity_type="crypto",
        facts=[
            fact("social_discussion_growth", "attention", "social", proxy=True, growth=900.0),
            fact("media_coverage_growth", "attention", "news", growth=700.0),
        ],
    )
    out = analyse_crypto(noise_only, SCAM)
    flags = set(out.analysis["automatic_flags"])
    for expected in (
        "anonymous_team",
        "low_liquidity",
        "concentrated_ownership",
        "unverified_contract",
        "suspicious_volume",
        "promotional_hype",
        "ponzi_like_incentives",
        "meme_coin",
        "no_product",
    ):
        assert expected in flags, f"{expected} was not flagged"


def test_a_token_with_real_developer_activity_is_not_called_a_meme_coin():
    out = analyse_crypto(good_input(opportunity_type="crypto"), SCAM)
    assert "meme_coin" not in out.analysis["automatic_flags"]
    # ...but everything else that is wrong with it is still flagged.
    assert "anonymous_team" in out.analysis["automatic_flags"]


def test_ponzi_incentives_produce_a_blocking_risk():
    out = analyse_crypto(good_input(opportunity_type="crypto"), SCAM)
    assert any(h["severity"] == "blocking" for h in out.risk_hints)


def test_every_crypto_flag_carries_a_plain_explanation():
    out = analyse_crypto(good_input(opportunity_type="crypto"), SCAM)
    for code, explanation in out.analysis["automatic_flags"].items():
        assert explanation.endswith("."), code


def test_crypto_participation_is_watch_only():
    out = analyse_crypto(good_input(opportunity_type="crypto"), SCAM)
    assert out.participation[0]["kind"] == "watch"


def test_unknown_crypto_facts_are_named_as_missing():
    out = analyse_crypto(good_input(opportunity_type="crypto"), {})
    joined = " ".join(out.missing_evidence).lower()
    for expected in ("users", "liquidity", "distribution", "audit"):
        assert expected in joined


# ------------------------------------------------------------------- dispatch
@pytest.mark.parametrize(
    "opportunity_type", ["business", "import_distribution", "public_investment", "crypto"]
)
def test_every_type_has_an_analyzer(opportunity_type: str):
    out = run_analyzer(good_input(opportunity_type=opportunity_type), {})
    assert out.analysis
    assert out.invalidation, "every candidate must say what would prove it wrong"


# ------------------------------------------------- experimental: adoption ratio
def test_adoption_ahead_of_attention_is_the_interesting_shape():
    facts = [
        EvidenceFact("github_contributors", "developer", "gh", 0.8, False, True, growth_30d=120),
        EvidenceFact("customer_count_growth", "commercial", "v", 0.9, False, True, growth_30d=80),
        EvidenceFact("media_coverage_growth", "attention", "n", 0.6, True, False, growth_30d=15),
    ]
    result = adoption_to_attention(facts)
    assert result.ratio is not None and result.ratio > 1.5
    assert "ahead of the talking" in result.reading


def test_attention_ahead_of_adoption_is_reported_as_such():
    facts = [
        EvidenceFact("customer_count_growth", "commercial", "v", 0.9, False, True, growth_30d=5),
        EvidenceFact("media_coverage_growth", "attention", "n", 0.6, False, False, growth_30d=500),
    ]
    result = adoption_to_attention(facts)
    assert result.ratio is not None and result.ratio < 0.9
    assert "ahead of the doing" in result.reading


def test_no_adoption_measurement_returns_no_ratio_rather_than_zero():
    facts = [EvidenceFact("media_coverage_growth", "attention", "n", 0.6, False, False, growth_30d=50)]
    result = adoption_to_attention(facts)
    assert result.ratio is None
    assert "not measured at all" in result.reading


def test_the_ratio_is_labelled_experimental():
    facts = [EvidenceFact("github_contributors", "developer", "gh", 0.8, False, True, growth_30d=10)]
    assert "experimental" in str(adoption_to_attention(facts).parts["note"]).lower()


# ------------------------------------------- experimental: geographic gap
def test_a_gap_needs_a_comparison_market():
    result = geographic_gap(leaders=[], target=GeoObservation("LY"))
    assert result.gap is None
    assert "no gap can be measured" in result.notes[0]


def test_a_clear_lag_produces_a_large_gap():
    result = geographic_gap(
        leaders=[GeoObservation("CN", adoption_growth=90, competitor_count=25, first_observed_days_ago=900)],
        target=GeoObservation(
            "LY", adoption_growth=3, supplier_count=0, competitor_count=1, first_observed_days_ago=120
        ),
    )
    assert result.gap is not None and result.gap > 40


def test_an_unmeasured_local_market_is_caveated_not_treated_as_zero_demand():
    result = geographic_gap(
        leaders=[GeoObservation("CN", adoption_growth=90)],
        target=GeoObservation("LY", adoption_growth=None, supplier_count=0),
    )
    assert any("not proven to be zero" in c for c in result.caveats)


def test_prices_in_different_currencies_are_not_compared():
    """No conversion happens anywhere in this system, so no price gap is claimed."""
    result = geographic_gap(
        leaders=[GeoObservation("CN", unit_price=100, price_currency="CNY", adoption_growth=50)],
        target=GeoObservation("LY", unit_price=900, price_currency="LYD", adoption_growth=5),
    )
    assert "price_gap" not in result.parts
    assert any("different currencies" in c for c in result.caveats)


def test_prices_in_the_same_currency_are_compared():
    result = geographic_gap(
        leaders=[GeoObservation("EU", unit_price=100, price_currency="USD", adoption_growth=50)],
        target=GeoObservation("LY", unit_price=150, price_currency="USD", adoption_growth=5),
    )
    assert "price_gap" in result.parts


def test_a_gap_is_always_caveated_as_a_question_not_an_answer():
    result = geographic_gap(
        leaders=[GeoObservation("CN", adoption_growth=90)],
        target=GeoObservation("LY", adoption_growth=3, supplier_count=1),
    )
    assert any("a question, not an answer" in c for c in result.caveats)
