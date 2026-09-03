"""The User Relevance Engine, and the wall between it and the global score.

Everything here is about one distinction. The Global Opportunity Score answers
"how attractive does this look on the evidence?" and is the same number for
everyone alive. The User Relevance Score answers "how realistic is this for
*you*?" and is different for everyone by design. These tests exist to make it
expensive to accidentally merge them.
"""

from __future__ import annotations

import pytest

from app.analytics.relevance import (
    FACTOR_MAX,
    PATH_REQUIREMENTS,
    RELEVANCE_VERSION,
    RISK_RANK,
    OpportunityContext,
    UserContext,
    score_relevance,
)


def opportunity(**over: object) -> OpportunityContext:
    base = dict(
        opportunity_id="opp-1",
        opportunity_type="business",
        industry="technology",
        geo_scope="global",
        global_score=70.0,
        confidence=65.0,
        risk_level="moderate",
        capital_required_usd=20_000.0,
        participation_paths=["build", "provide_service", "watch"],
    )
    base.update(over)
    return OpportunityContext(**base)  # type: ignore[arg-type]


US_INVESTOR = UserContext(
    home_country="US",
    residence_country="US",
    operating_countries=["US"],
    familiar_countries=["US", "GB"],
    interest_ranking=["public_investment", "technology", "saas"],
    industries=["technology"],
    experience_industries=["technology", "finance"],
    capital_currency="USD",
    max_capital=250_000.0,
    max_capital_usd=250_000.0,
    skills=["finance", "product"],
    assets=["capital"],
    risk_tolerance="aggressive",
    time_commitment="few_hours_week",
)

NG_ENTREPRENEUR = UserContext(
    home_country="NG",
    residence_country="NG",
    operating_countries=["NG"],
    familiar_countries=["NG", "GH"],
    target_countries=["NG"],
    interest_ranking=["import_distribution", "business", "manufacturing"],
    industries=["import_distribution"],
    experience_industries=["import_distribution"],
    capital_currency="NGN",
    max_capital=30_000_000.0,
    max_capital_usd=20_000.0,
    skills=["importing", "sales", "logistics"],
    assets=["supplier_network", "distribution_network"],
    risk_tolerance="moderate",
    time_commitment="full_time",
)

IN_STUDENT = UserContext(
    home_country="IN",
    residence_country="IN",
    operating_countries=["IN"],
    interest_ranking=["skills_career", "technology"],
    capital_currency="INR",
    max_capital=42_000.0,
    max_capital_usd=500.0,
    skills=["programming"],
    risk_tolerance="conservative",
    time_commitment="few_hours_week",
)


# ------------------------------------------------------------------- structure
def test_factor_weights_are_exactly_the_specification() -> None:
    assert FACTOR_MAX == {
        "geographic_accessibility": 15,
        "capital_fit": 15,
        "industry_experience": 15,
        "skills_fit": 10,
        "supplier_advantage": 10,
        "distribution_advantage": 10,
        "regulatory_accessibility": 10,
        "time_commitment_fit": 5,
        "risk_tolerance_fit": 5,
        "type_preference": 5,
    }
    assert sum(FACTOR_MAX.values()) == 100


def test_all_thirteen_participation_modes_are_defined() -> None:
    assert len(PATH_REQUIREMENTS) == 13


def test_every_factor_is_explained() -> None:
    """Section 5: always explain the relevance score, factor by factor."""
    result = score_relevance(US_INVESTOR, opportunity())
    for key in FACTOR_MAX:
        part = result.parts[key]
        assert part["max"] == FACTOR_MAX[key]
        assert 0 <= part["points"] <= FACTOR_MAX[key]
        assert isinstance(part["why"], str) and part["why"].strip()


def test_result_is_stamped_with_the_engine_version() -> None:
    assert score_relevance(US_INVESTOR, opportunity()).version == RELEVANCE_VERSION


def test_score_is_bounded() -> None:
    for user in (US_INVESTOR, NG_ENTREPRENEUR, IN_STUDENT):
        result = score_relevance(user, opportunity())
        assert 0.0 <= result.relevance <= 100.0


# ------------------------------------------------------- the wall between scores
def test_the_same_opportunity_scores_differently_for_different_people() -> None:
    """Section 32: identical global score, differing relevance."""
    opp = opportunity(
        opportunity_type="import_distribution",
        industry="import_distribution",
        country="NG",
        capital_required_usd=15_000.0,
        participation_paths=["import", "distribute", "watch"],
    )
    scores = {
        "US": score_relevance(US_INVESTOR, opp).relevance,
        "NG": score_relevance(NG_ENTREPRENEUR, opp).relevance,
        "IN": score_relevance(IN_STUDENT, opp).relevance,
    }
    assert scores["NG"] > scores["US"]
    assert scores["NG"] > scores["IN"]
    assert len(set(scores.values())) == 3


def test_relevance_never_reads_or_writes_the_global_score() -> None:
    """Change only the global score; relevance must not move."""
    low = score_relevance(US_INVESTOR, opportunity(global_score=5.0)).relevance
    high = score_relevance(US_INVESTOR, opportunity(global_score=99.0)).relevance
    assert low == high


def test_high_global_low_relevance_is_a_valid_result() -> None:
    """Section 33: global 91, relevance 12, and the row still exists."""
    opp = opportunity(
        opportunity_type="manufacturing",
        industry="manufacturing",
        country="DE",
        global_score=91.0,
        capital_required_usd=4_000_000.0,
        participation_paths=["manufacture"],
    )
    result = score_relevance(IN_STUDENT, opp)
    assert result.relevance < 35
    assert opp.global_score == 91.0  # untouched
    assert result.best_path == "manufacture"


# ------------------------------------------------------------------ per path
def test_each_path_is_scored_independently() -> None:
    """Section 6: thirteen participation modes, each scored on its own."""
    opp = opportunity(participation_paths=["build", "import", "consult", "watch"])
    result = score_relevance(NG_ENTREPRENEUR, opp)
    assert set(result.path_relevance) == {"build", "import", "consult", "watch"}
    assert len(set(result.path_relevance.values())) > 1


def test_the_headline_is_the_best_route_not_an_average() -> None:
    opp = opportunity(participation_paths=["build", "import", "watch"])
    result = score_relevance(NG_ENTREPRENEUR, opp)
    assert result.relevance == max(result.path_relevance.values())
    assert result.best_path == max(result.path_relevance, key=result.path_relevance.get)  # type: ignore[arg-type]


def test_watching_alone_does_not_look_like_a_strong_personal_fit() -> None:
    watch_only = score_relevance(US_INVESTOR, opportunity(participation_paths=["watch"]))
    doable = score_relevance(US_INVESTOR, opportunity(participation_paths=["investigate_public_company"]))
    assert watch_only.relevance < doable.relevance


def test_a_cheap_route_through_an_expensive_opportunity_is_cheap() -> None:
    opp = opportunity(capital_required_usd=500_000.0, participation_paths=["franchise", "learn_skill"])
    result = score_relevance(IN_STUDENT, opp)
    assert result.path_relevance["learn_skill"] > result.path_relevance["franchise"]


# ------------------------------------------------------------------- currency
def test_capital_without_a_stored_rate_is_not_guessed() -> None:
    """Section 23: never invent a conversion."""
    user = UserContext(capital_currency="TND", max_capital=90_000.0, max_capital_usd=None)
    result = score_relevance(user, opportunity(capital_required_usd=30_000.0))
    why = result.parts["capital_fit"]["why"]
    assert "no stored exchange rate" in why
    assert result.parts["capital_fit"]["points"] > 0  # neutral, not a zero


def test_capital_is_compared_in_the_users_own_currency_terms() -> None:
    rich = UserContext(capital_currency="EUR", max_capital=150_000.0, max_capital_usd=162_000.0)
    poor = UserContext(capital_currency="INR", max_capital=42_000.0, max_capital_usd=500.0)
    opp = opportunity(capital_required_usd=100_000.0, participation_paths=["import"])
    assert (
        score_relevance(rich, opp).parts["capital_fit"]["points"]
        > score_relevance(poor, opp).parts["capital_fit"]["points"]
    )


# ----------------------------------------------------------------------- risk
def test_risk_preference_never_removes_a_risk_label() -> None:
    """Section 3: risk tolerance changes the fit score, never the warning."""
    opp = opportunity(risk_level="very_high", participation_paths=["build"])
    speculative = UserContext(risk_tolerance="speculative", operating_countries=["US"])
    conservative = UserContext(risk_tolerance="conservative", operating_countries=["US"])

    bold = score_relevance(speculative, opp)
    careful = score_relevance(conservative, opp)

    assert bold.parts["risk_tolerance_fit"]["points"] > careful.parts["risk_tolerance_fit"]["points"]
    assert opp.risk_level == "very_high"
    assert "very high" in bold.parts["risk_tolerance_fit"]["why"]
    assert "risk label is unchanged" in bold.parts["risk_tolerance_fit"]["why"]


def test_a_risk_above_tolerance_is_still_scored_and_still_returned() -> None:
    opp = opportunity(risk_level="very_high")
    result = score_relevance(IN_STUDENT, opp)
    assert result.relevance > 0
    assert "still shown" in result.parts["risk_tolerance_fit"]["why"]


# ------------------------------------------------------------ outside profile
def test_outside_the_profile_is_flagged_never_hidden() -> None:
    """Section 14: the user can still be shown things beyond their filters."""
    picky = UserContext(
        operating_countries=["US"],
        min_global_score=80.0,
        min_confidence=70.0,
        max_risk_level="low",
    )
    result = score_relevance(picky, opportunity(global_score=40.0, risk_level="high"))
    assert result.outside_profile is True
    assert result.relevance > 0  # scored, returned, and visible
    assert any("Outside your usual filters" in note for note in result.notes)


def test_a_matching_opportunity_is_not_flagged() -> None:
    result = score_relevance(US_INVESTOR, opportunity())
    assert result.outside_profile is False


def test_a_disabled_category_zeroes_preference_without_deleting_the_row() -> None:
    user = UserContext(operating_countries=["US"], disabled_categories=["crypto"])
    result = score_relevance(user, opportunity(opportunity_type="crypto", industry="crypto"))
    assert result.parts["type_preference"]["points"] == 0.0
    assert result.relevance > 0
    assert result.outside_profile is True


def test_an_excluded_country_zeroes_geography() -> None:
    user = UserContext(operating_countries=["US"], excluded_countries=["RU"])
    result = score_relevance(user, opportunity(country="RU"))
    assert result.parts["geographic_accessibility"]["points"] == 0.0
    assert "excluded" in result.parts["geographic_accessibility"]["why"]


# ------------------------------------------------------ nothing is hardcoded
@pytest.mark.parametrize("country", ["LY", "TN", "US", "BR", "CN", "ZZ"])
def test_no_country_is_privileged_by_the_code(country: str) -> None:
    """Section 36: the architecture must not depend on any particular country."""
    user = UserContext(
        home_country=country,
        residence_country=country,
        operating_countries=[country],
        interest_ranking=["business"],
        max_capital_usd=50_000.0,
        skills=["sales"],
    )
    result = score_relevance(user, opportunity(country=country))
    baseline = UserContext(
        home_country="QQ",
        residence_country="QQ",
        operating_countries=["QQ"],
        interest_ranking=["business"],
        max_capital_usd=50_000.0,
        skills=["sales"],
    )
    assert result.relevance == score_relevance(baseline, opportunity(country="QQ")).relevance


def test_skills_are_free_text_not_a_fixed_list() -> None:
    """Section 3: users define their own skills."""
    user = UserContext(
        operating_countries=["US"],
        skills=["camel husbandry", "importing", "arabic calligraphy"],
        max_capital_usd=200_000.0,
    )
    result = score_relevance(user, opportunity(participation_paths=["import"]))
    assert result.parts["skills_fit"]["points"] > 0
    assert "importing" in result.parts["skills_fit"]["why"]


def test_thin_country_coverage_is_reported_as_our_gap() -> None:
    """Section 30 again, this time in the per-user explanation."""
    result = score_relevance(NG_ENTREPRENEUR, opportunity(country="NG", country_data_coverage=12.0))
    assert any("gap in our data" in note for note in result.notes)


# ------------------------------------------------- the risk-ceiling fail-open
# These exist because the bug they describe was real, silent, and pointed the
# wrong way: an unrecognised risk ceiling resolved to the most permissive
# setting, so a user who asked for less risk was shown more of it, unflagged.
def test_an_unrecognised_risk_ceiling_fails_closed() -> None:
    opp = opportunity(risk_level="very_high", participation_paths=["build"])
    user = UserContext(operating_countries=["US"], max_risk_level="medium")
    result = score_relevance(user, opp)
    assert result.outside_profile is True, "an uninterpretable ceiling must flag, never wave things through"
    assert any("does not recognise" in note for note in result.notes)


def test_an_unrecognised_ceiling_is_not_treated_as_the_most_permissive() -> None:
    opp = opportunity(risk_level="very_high", participation_paths=["build"])
    unknown = score_relevance(UserContext(operating_countries=["US"], max_risk_level="medium"), opp)
    permissive = score_relevance(UserContext(operating_countries=["US"], max_risk_level="very_high"), opp)
    assert unknown.outside_profile != permissive.outside_profile


@pytest.mark.parametrize("ceiling", ["low", "moderate", "high"])
def test_a_ceiling_below_the_risk_always_flags(ceiling: str) -> None:
    opp = opportunity(risk_level="very_high", participation_paths=["build"])
    result = score_relevance(UserContext(operating_countries=["US"], max_risk_level=ceiling), opp)
    assert result.outside_profile is True


def test_every_risk_level_the_engine_emits_is_understood_here() -> None:
    """The enum, the risk engine and this table must not drift apart again."""
    from app.analytics.risk_engine import LEVELS
    from app.models.enums import RiskLevel

    assert set(LEVELS) == set(RISK_RANK)
    assert {level.value for level in RiskLevel} == set(RISK_RANK)
