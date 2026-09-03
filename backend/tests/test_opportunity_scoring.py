"""Opportunity Score, penalties, confidence, lifecycle and personal relevance.

Includes the golden test the brief calls out specifically: **a high Trend Score
must not imply a high Opportunity Score.**
"""

from __future__ import annotations

import pytest

from app.analytics.opportunity_config import COMPONENT_MAX, MAX_TOTAL_PENALTY
from app.analytics.opportunity_scoring import (
    EvidenceFact,
    OpportunityInput,
    accessibility_verdict,
    next_state,
    score_confidence,
    score_opportunity,
)
from tests.test_opportunity_gate import fact, good_input


def test_components_sum_to_one_hundred():
    assert sum(COMPONENT_MAX.values()) == 100


def test_every_component_is_explained():
    """The calculation is shown, so every component must carry its sentence."""
    result = score_opportunity(good_input())
    for name, component in result.components.items():
        assert component["why"], f"{name} has no explanation"
        assert component["max"] == COMPONENT_MAX[name]
        assert 0 <= component["points"] <= component["max"] + 1e-9


# --------------------------------------------------------------- real adoption
def test_attention_alone_earns_nothing_for_adoption():
    inp = good_input(
        facts=[
            fact("media_coverage_growth", "attention", "a", growth=400.0),
            fact("social_discussion_growth", "attention", "b", growth=380.0),
            fact("wiki_pageview_growth", "attention", "c", proxy=True, growth=350.0),
        ]
    )
    result = score_opportunity(inp)
    assert result.components["real_adoption"]["points"] == 0
    assert "attention" in result.components["real_adoption"]["why"].lower()


def test_a_proxy_measurement_does_not_count_as_adoption():
    inp = good_input(facts=[fact("package_downloads", "developer", "npm", adoption=False, proxy=True)])
    assert score_opportunity(inp).components["real_adoption"]["points"] == 0


def test_real_use_growing_scores_adoption():
    result = score_opportunity(good_input())
    assert result.components["real_adoption"]["points"] > 10


# ------------------------------------------------------------- market potential
def test_an_unknown_market_size_scores_zero_and_says_so():
    result = score_opportunity(good_input(market_size_band=None))
    component = result.components["market_potential"]
    assert component["points"] == 0
    assert "unknown" in component["why"].lower()
    assert "estimated" in component["why"].lower()


def test_a_market_size_is_never_invented():
    """Two candidates identical but for a stated band must differ only by that."""
    a = score_opportunity(good_input(market_size_band=None))
    b = score_opportunity(good_input(market_size_band="large"))
    delta = b.raw_score - a.raw_score
    assert delta == pytest.approx(COMPONENT_MAX["market_potential"], abs=0.01)


# -------------------------------------------------------------------- penalties
def test_attention_without_adoption_is_penalised():
    inp = good_input(
        facts=[
            fact("github_contributors", "developer", "gh", adoption=True, growth=3.0),
            fact("import_growth", "trade", "customs", adoption=True, growth=2.0),
            fact("media_coverage_growth", "attention", "news", growth=400.0),
        ]
    )
    result = score_opportunity(inp)
    assert "attention_without_adoption" in result.penalties
    assert any("ahead of the doing" in w for w in result.warnings)


def test_an_already_mainstream_trend_is_penalised():
    result = score_opportunity(good_input(trend_stage="mainstream"))
    assert "already_mainstream" in result.penalties


def test_penalties_are_capped_and_the_cap_is_disclosed():
    inp = good_input(
        trend_stage="mature",
        market_size_band="tiny",
        competitor_count=40,
        facts=[
            fact("media_coverage_growth", "attention", "a", reliability=0.2, growth=500.0),
        ],
        flags={
            "anonymous_team",
            "promotional_manipulation",
            "poor_liquidity",
            "concentrated_ownership",
            "extreme_valuation",
            "poor_economics",
        },
    )
    result = score_opportunity(inp)
    assert result.metrics["penalty_before_cap"] > MAX_TOTAL_PENALTY
    assert result.penalty_total == MAX_TOTAL_PENALTY
    assert any("capped" in w for w in result.warnings)


def test_no_penalty_is_hidden():
    """Every applied penalty must appear in the stored dictionary."""
    inp = good_input(flags={"anonymous_team", "poor_liquidity"})
    result = score_opportunity(inp)
    assert "anonymous_team" in result.penalties
    assert "poor_liquidity" in result.penalties


# ------------------------------------------------------- THE golden test (§25)
def test_a_high_trend_score_does_not_imply_a_high_opportunity_score():
    """Section 25 of the brief, tested directly.

    The trend is excellent — 90, well corroborated, accelerating. The opportunity
    is not: the market is saturated, the valuation is extreme, the economics do
    not work, and there is no defensible position. The system must be able to
    hold both of those thoughts at once.
    """
    inp = good_input(
        trend_score=90.0,
        trend_confidence=95.0,
        trend_stage="mainstream",
        market_size_band="large",
        competitor_count=40,
        awareness="high",
        local_penetration="high",
        capital_required_usd=500_000,
        defensibility_markers=[],
        flags={"extreme_valuation", "poor_economics", "extreme_competition"},
    )
    result = score_opportunity(inp)

    assert inp.trend_score >= 90
    assert result.opportunity_score < 40, (
        f"a saturated, expensive, undefensible market scored "
        f"{result.opportunity_score} on a trend of {inp.trend_score}"
    )
    assert "extreme_valuation" in result.penalties
    assert "already_mainstream" in result.penalties


def test_the_same_trend_scores_differently_for_different_ways_of_participating():
    """One trend, two actions, two verdicts — which is the point of Phase 4."""
    common = {
        "trend_score": 70.0,
        "trend_confidence": 80.0,
        "trend_stage": "early_adoption",
        "trend_state": "active",
        "trend_history_days": 200,
        "trend_observation_count": 200,
        "facts": good_input().facts,
        "market_size_band": "medium",
    }
    buildable = score_opportunity(
        OpportunityInput(
            opportunity_type="business",
            local_penetration="low",
            awareness="low",
            competitor_count=2,
            capital_required_usd=20_000,
            defensibility_markers=["switching cost"],
            technical_difficulty="medium",
            **common,
        ),
    )
    unbuildable = score_opportunity(
        OpportunityInput(
            opportunity_type="business",
            local_penetration="high",
            awareness="high",
            competitor_count=40,
            capital_required_usd=5_000_000,
            defensibility_markers=[],
            technical_difficulty="high",
            **common,
        ),
    )
    assert buildable.opportunity_score > unbuildable.opportunity_score + 25


# ------------------------------------------------------------------- confidence
def test_confidence_is_separate_from_the_score():
    """A candidate can look attractive and be poorly evidenced at the same time."""
    inp = good_input(
        market_size_band="large",
        local_penetration="none",
        awareness="low",
        competitor_count=1,
        defensibility_markers=["network effect", "brand"],
        catalyst="x",
        catalyst_is_dated=True,
        catalyst_evidence_id="e1",
        trend_observation_count=25,
        trend_history_days=60,
    )
    score = score_opportunity(inp)
    confidence, _ = score_confidence(inp)
    assert score.opportunity_score > 55
    assert confidence < score.opportunity_score


def test_missing_required_evidence_lowers_confidence():
    full = good_input()
    full.evidence_kinds_present = {"adoption", "demand_pain", "competition", "willingness_to_pay"}
    empty = good_input()
    empty.evidence_kinds_present = set()
    assert score_confidence(full)[0] > score_confidence(empty)[0]


def test_stale_evidence_lowers_confidence():
    fresh = good_input()
    stale = good_input(
        facts=[
            EvidenceFact(
                "github_contributors",
                "developer",
                "gh",
                0.8,
                False,
                True,
                growth_30d=30,
                observation_count=200,
                days_since_latest=200,
            ),
            EvidenceFact(
                "import_growth",
                "trade",
                "customs",
                0.8,
                False,
                True,
                growth_30d=30,
                observation_count=200,
                days_since_latest=200,
            ),
        ]
    )
    assert score_confidence(fresh)[0] > score_confidence(stale)[0]


# -------------------------------------------------------------------- lifecycle
def test_a_rejected_candidate_is_invalidated():
    state, reason = next_state(
        current_state="promising",
        opportunity_score=80,
        confidence=80,
        peak_score=80,
        risk_level="moderate",
        skeptic_status="reject",
        days_since_evidence=1,
    )
    assert state == "invalidated"
    assert "skeptic" in reason.lower()


def test_a_very_high_risk_candidate_never_reaches_strong_evidence():
    state, _ = next_state(
        current_state="promising",
        opportunity_score=95,
        confidence=95,
        peak_score=95,
        risk_level="very_high",
        skeptic_status="continue_research",
        days_since_evidence=1,
    )
    assert state != "strong_evidence"


def test_a_falling_score_becomes_weakening():
    state, reason = next_state(
        current_state="promising",
        opportunity_score=40,
        confidence=70,
        peak_score=75,
        risk_level="moderate",
        skeptic_status=None,
        days_since_evidence=1,
    )
    assert state == "weakening"
    assert "fallen" in reason


def test_a_quiet_candidate_is_archived_not_left_looking_current():
    state, reason = next_state(
        current_state="watchlist",
        opportunity_score=50,
        confidence=60,
        peak_score=50,
        risk_level="moderate",
        skeptic_status=None,
        days_since_evidence=200,
    )
    assert state == "archived"
    assert "no new evidence" in reason.lower()


def test_there_is_no_state_above_strong_evidence():
    state, _ = next_state(
        current_state="strong_evidence",
        opportunity_score=100,
        confidence=100,
        peak_score=100,
        risk_level="low",
        skeptic_status="strong_evidence",
        days_since_evidence=0,
    )
    assert state == "strong_evidence"


@pytest.mark.parametrize("banned", ["buy", "guaranteed", "sure thing", "100x", "next bitcoin", "invest now"])
def test_no_lifecycle_wording_ever_tells_anyone_to_buy(banned: str):
    for score in (10, 40, 60, 80, 95):
        _, reason = next_state(
            current_state=None,
            opportunity_score=score,
            confidence=score,
            peak_score=score,
            risk_level="moderate",
            skeptic_status=None,
            days_since_evidence=1,
        )
        assert banned not in reason.lower()


# ------------------------------------- the global score is the same for everyone
def test_the_global_score_reads_nothing_about_any_person():
    """Phase 5, section 1: identical for every user, forever.

    Before Phase 5 this function took a `capital_cap` drawn from one user's
    preferences, which quietly made the "global" score a score for that person.
    The signature no longer admits such an argument, and this test is what stops
    one being reintroduced.
    """
    import inspect

    signature = inspect.signature(score_opportunity)
    assert list(signature.parameters) == ["inp"]


def test_accessibility_reads_absolute_capital_not_a_personal_ceiling():
    cheap = score_opportunity(good_input(capital_required_usd=2_000))
    dear = score_opportunity(good_input(capital_required_usd=2_000_000))
    assert cheap.components["accessibility"]["points"] > dear.components["accessibility"]["points"]
    # And the explanation says so in absolute terms, with no reference to a user.
    why = dear.components["accessibility"]["why"]
    assert "2,000,000 USD" in why
    assert "ceiling" not in why


def test_the_same_input_always_produces_the_same_global_score():
    inp = good_input()
    assert score_opportunity(inp).opportunity_score == score_opportunity(inp).opportunity_score


# ------------------------------------------------- no accessible way to take part
# A real trend nobody can act on is a fact about the world, not an opportunity.
# This refusal used to be an accident of arithmetic: `_accessibility` early
# returned exactly 0.0 when capital exceeded ONE CONFIGURED USER'S ceiling, and
# the refusal keyed off that zero. Phase 5 removed the personal ceiling from the
# global score — correctly — and the refusal silently stopped firing. These tests
# pin the behaviour to evidence rather than to a number that happened to be zero.
def euv_like(**over):
    """The shape of a genuinely closed route: recorded barriers, huge capital."""
    return good_input(
        capital_required_usd=over.pop("capital_required_usd", 400_000_000),
        technical_difficulty=over.pop("technical_difficulty", "high"),
        accessibility_barriers=over.pop(
            "accessibility_barriers",
            [
                "One manufacturer worldwide, with a multi-year order book",
                "Export controls restrict who may buy",
                "Capital requirement is orders of magnitude beyond individual reach",
            ],
        ),
        **over,
    )


def test_three_recorded_barriers_close_the_route():
    closed, why = accessibility_verdict(euv_like())
    assert closed is True
    assert "One manufacturer worldwide" in why


def test_a_closed_route_scores_zero_accessibility():
    """The component and the refusal must agree by construction, not by luck."""
    score = score_opportunity(euv_like())
    assert score.components["accessibility"]["points"] == 0.0
    assert "No accessible way in" in score.components["accessibility"]["why"]


def test_capital_alone_never_closes_a_route():
    """A large number is not evidence that no way in exists.

    A $400m factory can still be supplied to, invested in, or worked for. Only
    recorded barriers may establish that the route is shut.
    """
    closed, _ = accessibility_verdict(euv_like(accessibility_barriers=[]))
    assert closed is False
    closed_one, _ = accessibility_verdict(
        euv_like(accessibility_barriers=["Export controls restrict who may buy"])
    )
    assert closed_one is False


def test_two_barriers_close_the_route_only_when_reinforced():
    two = ["Export controls restrict who may buy", "One manufacturer worldwide"]
    hard = accessibility_verdict(
        euv_like(accessibility_barriers=two, technical_difficulty="high", capital_required_usd=5_000)
    )
    easy = accessibility_verdict(
        euv_like(accessibility_barriers=two, technical_difficulty="low", capital_required_usd=5_000)
    )
    dear = accessibility_verdict(
        euv_like(accessibility_barriers=two, technical_difficulty="low", capital_required_usd=50_000_000)
    )
    assert hard[0] is True, "two barriers plus high difficulty is a closed route"
    assert dear[0] is True, "two barriers plus prohibitive capital is a closed route"
    assert easy[0] is False, "two barriers alone are not enough to refuse"


def test_an_ordinary_candidate_is_never_called_inaccessible():
    """The five scenarios with no recorded barriers must be untouched by this."""
    closed, why = accessibility_verdict(good_input())
    assert closed is False
    assert why == ""


def test_the_verdict_reads_nothing_about_any_person():
    """Absolute by construction: the signature admits no user, profile or ceiling."""
    import inspect

    assert list(inspect.signature(accessibility_verdict).parameters) == ["inp"]
