"""The global geographic engine, and the rule that governs it.

The rule, from section 30 of the Phase 5 brief: *do not conclude "no opportunities
exist in Country X" when the real situation is "we have poor data coverage for
Country X."* Most of the tests below exist only to hold that line, because it is
the kind of rule that quietly erodes the first time somebody wants a tidier number.
"""

from __future__ import annotations

import pytest

from app.analytics.country import AdoptionReading
from app.analytics.geographic import (
    GAP_DIMENSIONS,
    TRANSFER_DIMENSIONS,
    UNEVIDENCED,
    MarketFacts,
    analyse_gap,
    find_localization_candidates,
    similarity,
    transferability_score,
)


def rich_facts(**over: object) -> MarketFacts:
    base = dict(
        internet_penetration=0.9,
        payment_infrastructure=0.85,
        logistics_performance=0.8,
        electricity_reliability=0.95,
        business_formation_ease=0.8,
        import_openness=0.7,
        regulatory_environment=0.75,
        purchasing_power=0.9,
        language="en",
        currency="USD",
    )
    base.update(over)
    return MarketFacts(**base)  # type: ignore[arg-type]


def leader(country: str, **over: object) -> AdoptionReading:
    base = dict(
        country=country,
        status="measured",
        level="high",
        adoption_growth=70.0,
        attention_growth=55.0,
        supplier_count=40,
        competitor_count=12,
        first_observed_days_ago=900,
        observation_count=30,
        source_count=4,
        data_coverage=85.0,
    )
    base.update(over)
    return AdoptionReading(**base)  # type: ignore[arg-type]


# ------------------------------------------------------------------ weights
def test_gap_dimensions_sum_to_one_hundred() -> None:
    assert sum(GAP_DIMENSIONS.values()) == 100


def test_transfer_dimensions_sum_to_one_hundred() -> None:
    assert sum(TRANSFER_DIMENSIONS.values()) == 100


# -------------------------------------------------------- UNKNOWN is not ZERO
def test_unmeasured_target_claims_no_adoption_gap() -> None:
    """The whole point. A country nobody looked at must not read as empty."""
    result = analyse_gap(
        subject="solar water pumps",
        readings=[leader("US"), leader("DE"), AdoptionReading(country="NG", status="no_data")],
        target_country="NG",
        target_facts=rich_facts(purchasing_power=0.3),
    )
    adoption = result.dimensions["adoption_gap"]
    assert adoption.measured is False
    assert adoption.points == 0.0
    assert "missing data, not low adoption" in adoption.why
    assert "NG" in result.unmeasured
    assert any("NO DATA" in c for c in result.caveats)


def test_checked_absence_is_a_real_gap() -> None:
    """We looked and found nothing. That is a finding, and it should score."""
    result = analyse_gap(
        subject="solar water pumps",
        readings=[leader("US"), leader("DE"), AdoptionReading(country="NG", status="no_signal")],
        target_country="NG",
        target_facts=rich_facts(),
    )
    adoption = result.dimensions["adoption_gap"]
    assert adoption.measured is True
    assert adoption.points > 0
    assert "checked absence" in adoption.why
    assert result.gap is not None and result.gap > 0


def test_no_data_and_no_signal_produce_different_answers() -> None:
    """If these two ever converge, the fairness rule has been lost."""
    leaders = [leader("US"), leader("DE")]
    unknown = analyse_gap(
        subject="x",
        readings=[*leaders, AdoptionReading(country="NG", status="no_data")],
        target_country="NG",
        target_facts=rich_facts(),
    )
    checked = analyse_gap(
        subject="x",
        readings=[*leaders, AdoptionReading(country="NG", status="no_signal")],
        target_country="NG",
        target_facts=rich_facts(),
    )
    assert unknown.dimensions["adoption_gap"].points < checked.dimensions["adoption_gap"].points


def test_gap_is_none_not_zero_when_nothing_is_comparable() -> None:
    result = analyse_gap(
        subject="x",
        readings=[
            AdoptionReading(country="NG", status="no_data"),
            AdoptionReading(country="KE", status="no_data"),
        ],
        target_country="NG",
    )
    assert result.gap is None
    assert "No comparison market has usable data" in " ".join(result.caveats)


def test_thin_coverage_costs_confidence() -> None:
    result = analyse_gap(
        subject="x",
        readings=[
            leader("US"),
            AdoptionReading(country="NG", status="no_signal", data_coverage=12.0),
            AdoptionReading(country="KE", status="no_data", data_coverage=8.0),
        ],
        target_country="NG",
        target_facts=rich_facts(),
    )
    assert result.confidence_penalty > 0
    assert "not measured at all" in result.coverage_note


def test_every_gap_carries_the_it_is_a_question_caveat() -> None:
    result = analyse_gap(
        subject="x",
        readings=[leader("US"), leader("DE"), leader("NG", level="low", adoption_growth=4.0)],
        target_country="NG",
        target_facts=rich_facts(),
    )
    assert any("a question, not an answer" in c for c in result.caveats)


# ----------------------------------------------------------------- currencies
def test_prices_in_different_currencies_are_not_compared() -> None:
    readings = [
        leader("US", unit_price=100.0, price_currency="USD"),
        AdoptionReading(
            country="NG",
            status="measured",
            level="low",
            adoption_growth=5.0,
            unit_price=90000.0,
            price_currency="NGN",
        ),
    ]
    result = analyse_gap(subject="x", readings=readings, target_country="NG", target_facts=rich_facts())
    price = result.dimensions["price_gap"]
    assert price.measured is False
    assert price.points == 0.0
    assert "no stored exchange rate" in price.why
    assert any("would be a guess" in c for c in result.caveats)


def test_prices_in_one_currency_are_compared() -> None:
    readings = [
        leader("US", unit_price=100.0, price_currency="USD"),
        AdoptionReading(
            country="NG",
            status="measured",
            level="low",
            adoption_growth=5.0,
            unit_price=160.0,
            price_currency="USD",
        ),
    ]
    result = analyse_gap(subject="x", readings=readings, target_country="NG", target_facts=rich_facts())
    price = result.dimensions["price_gap"]
    assert price.measured is True
    assert price.points > 0
    assert "1.60x" in price.why


# ------------------------------------------------------------------ similarity
def test_similarity_reports_which_dimensions_it_used() -> None:
    score, dims = similarity(rich_facts(), rich_facts(purchasing_power=0.4))
    assert 0.8 < score < 1.0
    assert "purchasing power" in dims
    assert len(dims) == 8


def test_similarity_of_two_empty_markets_is_zero_with_no_dimensions() -> None:
    score, dims = similarity(MarketFacts(), MarketFacts())
    assert score == 0.0
    assert dims == []


# --------------------------------------------------------- cross-country search
def test_localization_never_targets_an_unmeasured_market() -> None:
    """Recommending a country because we never looked at it is the failure mode."""
    facts = {"US": rich_facts(), "DE": rich_facts(), "NG": rich_facts()}
    out = find_localization_candidates(
        subject="solar water pumps",
        readings=[leader("US"), leader("DE"), AdoptionReading(country="NG", status="no_data")],
        facts_by_country=facts,
    )
    assert all(c.target_market != "NG" for c in out)


def test_localization_finds_a_checked_empty_similar_market() -> None:
    facts = {"US": rich_facts(), "DE": rich_facts(), "NG": rich_facts()}
    out = find_localization_candidates(
        subject="solar water pumps",
        readings=[
            leader("US"),
            leader("DE"),
            AdoptionReading(country="NG", status="no_signal", supplier_count=0, competitor_count=0),
        ],
        facts_by_country=facts,
    )
    assert [c.target_market for c in out] == ["NG"]
    candidate = out[0]
    assert sorted(candidate.source_markets) == ["DE", "US"]
    assert candidate.gap >= 25.0
    assert candidate.similarity >= 0.6


def test_localization_skips_dissimilar_markets() -> None:
    poor = rich_facts(
        internet_penetration=0.1,
        payment_infrastructure=0.05,
        logistics_performance=0.1,
        electricity_reliability=0.15,
        business_formation_ease=0.1,
        import_openness=0.1,
        regulatory_environment=0.2,
        purchasing_power=0.05,
    )
    out = find_localization_candidates(
        subject="x",
        readings=[leader("US"), AdoptionReading(country="NG", status="no_signal")],
        facts_by_country={"US": rich_facts(), "NG": poor},
    )
    assert out == []


def test_localization_needs_a_market_where_it_already_works() -> None:
    out = find_localization_candidates(
        subject="x",
        readings=[
            AdoptionReading(country="US", status="measured", level="low", adoption_growth=3.0),
            AdoptionReading(country="NG", status="no_signal"),
        ],
        facts_by_country={"US": rich_facts(), "NG": rich_facts()},
    )
    assert out == []


# ------------------------------------------------------------ transferability
def test_transferability_is_none_when_under_evidenced() -> None:
    result = transferability_score(
        source_facts=[MarketFacts(language="en")], target_facts=MarketFacts(), gap=0.0
    )
    assert result.score is None
    assert "Too few dimensions" in result.note


def test_transferability_is_none_without_any_source_facts() -> None:
    result = transferability_score(source_facts=[], target_facts=rich_facts(), gap=50.0)
    assert result.score is None
    assert set(UNEVIDENCED) <= set(result.missing)


def test_transferability_names_what_it_cannot_evidence() -> None:
    result = transferability_score(source_facts=[rich_facts()], target_facts=rich_facts(), gap=60.0)
    assert result.score is not None
    for consideration in UNEVIDENCED:
        assert consideration in result.missing
        assert consideration in result.note


def test_transferability_is_always_marked_experimental() -> None:
    scored = transferability_score(source_facts=[rich_facts()], target_facts=rich_facts(), gap=60.0)
    unscored = transferability_score(source_facts=[MarketFacts()], target_facts=MarketFacts(), gap=0.0)
    assert "Experimental" in scored.note
    assert "Experimental" in unscored.note
    assert scored.version.endswith("-experimental")


def test_transferability_penalises_a_much_poorer_target() -> None:
    strong = transferability_score(source_facts=[rich_facts()], target_facts=rich_facts(), gap=60.0)
    weak = transferability_score(
        source_facts=[rich_facts()],
        target_facts=rich_facts(purchasing_power=0.1, logistics_performance=0.2),
        gap=60.0,
    )
    assert strong.score is not None and weak.score is not None
    assert weak.score < strong.score


def test_different_language_scores_lower_than_shared_language() -> None:
    shared = transferability_score(
        source_facts=[rich_facts()], target_facts=rich_facts(language="en"), gap=50.0
    )
    other = transferability_score(
        source_facts=[rich_facts()], target_facts=rich_facts(language="ar"), gap=50.0
    )
    assert shared.score is not None and other.score is not None
    assert other.score < shared.score
    assert "needs localising" in other.parts["language"]["why"]


# ------------------------------------------------------------ no country code
@pytest.mark.parametrize("country", ["LY", "TN", "US", "CN", "ZZ"])
def test_no_country_receives_special_treatment(country: str) -> None:
    """Identical inputs must produce an identical gap whatever the country is."""
    result = analyse_gap(
        subject="x",
        readings=[leader("AA"), leader("BB"), AdoptionReading(country=country, status="no_signal")],
        target_country=country,
        target_facts=rich_facts(),
    )
    assert result.gap is not None
    assert result.target == country
    assert result.gap == pytest.approx(
        analyse_gap(
            subject="x",
            readings=[
                leader("AA"),
                leader("BB"),
                AdoptionReading(country="QQ", status="no_signal"),
            ],
            target_country="QQ",
            target_facts=rich_facts(),
        ).gap
    )
