"""The skeptic pass and the risk engine.

Two structural guarantees are tested here rather than assumed:

* the skeptic can only *reduce* confidence, at any input, ever;
* the overall risk level is dominated by the worst finding, not averaged.
"""

from __future__ import annotations

import pytest

from app.analytics.risk_engine import LEVELS, assess_risk
from app.analytics.skeptic import QUESTIONS, review
from tests.test_opportunity_gate import fact, good_input


# ------------------------------------------------------------------ the skeptic
def test_the_skeptic_asks_every_question():
    result = review(good_input(), analysis={}, missing_evidence=[])
    assert len(result.questions_asked) == len(QUESTIONS) == 17


@pytest.mark.parametrize("confidence", [0.0, 1.0, 25.0, 50.0, 99.9, 100.0])
def test_the_skeptic_can_only_reduce_confidence(confidence: float):
    """The one property that must hold for every possible input."""
    for inp in (
        good_input(),
        good_input(trend_stage="mainstream", competitor_count=50),
        good_input(facts=[fact("media_coverage_growth", "attention", "a", reliability=0.1)]),
    ):
        result = review(inp, analysis={"willingness_to_pay": "UNKNOWN"}, missing_evidence=[])
        assert result.apply(confidence) <= confidence
        assert result.apply(confidence) >= 0.0


def test_the_skeptic_never_returns_a_negative_reduction():
    result = review(good_input(), analysis={}, missing_evidence=[])
    assert result.confidence_reduction >= 0


def test_no_adoption_evidence_is_the_leading_objection():
    inp = good_input(facts=[fact("media_coverage_growth", "attention", "news")])
    result = review(inp, analysis={}, missing_evidence=[])
    assert "no measurement of anyone actually using" in result.strongest_counterargument.lower()
    assert result.status == "insufficient_evidence"


def test_syndicated_coverage_is_called_out():
    result = review(good_input(), analysis={}, missing_evidence=[], duplication_ratio=0.7)
    assert any("republished" in c for c in result.counterarguments)
    assert result.manipulation_probability >= 0.4


def test_manipulation_leads_the_argument_when_it_is_likely():
    inp = good_input(flags={"promotional_manipulation", "anonymous_team"})
    result = review(inp, analysis={}, missing_evidence=[])
    assert result.manipulation_probability >= 0.6
    assert "manufactured" in result.strongest_counterargument
    assert result.status == "possible_manipulation"


def test_an_extreme_valuation_is_challenged():
    result = review(good_input(), analysis={"valuation": {"band": "extreme"}}, missing_evidence=[])
    assert any("entry price" in c for c in result.counterarguments)


def test_a_missing_price_is_challenged_rather_than_estimated():
    result = review(good_input(), analysis={"valuation": {"status": "NOT ASSESSED"}}, missing_evidence=[])
    assert any("no current price" in c.lower() for c in result.counterarguments)


def test_unknown_willingness_to_pay_is_challenged():
    result = review(good_input(), analysis={"willingness_to_pay": "UNKNOWN"}, missing_evidence=[])
    assert any("shown to pay" in c for c in result.counterarguments)


def test_survivorship_bias_is_always_raised():
    """It applies to every candidate, so it is never omitted."""
    result = review(good_input(), analysis={}, missing_evidence=[])
    assert any("failed leave no trail" in c for c in result.counterarguments)


def test_the_trend_may_succeed_while_the_opportunity_fails():
    result = review(good_input(), analysis={}, missing_evidence=[])
    assert any("value accrues to someone else" in c for c in result.counterarguments)


def test_a_mainstream_trend_produces_a_too_late_reason():
    result = review(good_input(trend_stage="mainstream"), analysis={}, missing_evidence=[])
    assert result.too_late_reasons
    assert result.status in {"watch_only", "high_speculation", "continue_research"}


def test_barriers_become_inaccessible_reasons():
    inp = good_input(accessibility_barriers=["export controls"], capital_required_usd=5_000_000)
    result = review(inp, analysis={}, missing_evidence=[])
    assert "export controls" in result.inaccessible_reasons
    assert any("capital" in r for r in result.inaccessible_reasons)


def test_missing_evidence_is_passed_through_untouched():
    missing = ["No supplier count.", "No price evidence."]
    result = review(good_input(), analysis={}, missing_evidence=missing)
    assert result.missing_evidence == missing


# ---------------------------------------------------------------- the risk engine
def test_a_blocking_risk_dominates_everything_else():
    """Nine comfortable findings do not offset one fatal one."""
    assessment = assess_risk(
        opportunity_type="business",
        hints=[
            {
                "code": "fraud",
                "category": "fraud_manipulation",
                "severity": "blocking",
                "rationale": "An active fraud investigation.",
            },
            *[
                {"code": f"minor{i}", "category": "market", "severity": "low", "rationale": "Minor."}
                for i in range(9)
            ],
        ],
        flags=set(),
        metrics={},
    )
    assert assessment.level == "very_high"
    assert "dominates" in assessment.reasoning


def test_risk_is_not_an_average():
    """One high finding beats several low ones, which averaging would not do."""
    high_only = assess_risk(
        opportunity_type="business",
        hints=[{"code": "a", "category": "financial", "severity": "high", "rationale": "Bad."}],
        flags=set(),
        metrics={},
    )
    many_low = assess_risk(
        opportunity_type="business",
        hints=[
            {"code": f"l{i}", "category": "market", "severity": "low", "rationale": "Minor."}
            for i in range(8)
        ],
        flags=set(),
        metrics={},
    )
    assert LEVELS.index(high_only.level) > LEVELS.index(many_low.level)


def test_three_high_risks_together_are_very_high():
    assessment = assess_risk(
        opportunity_type="business",
        hints=[
            {"code": f"h{i}", "category": "market", "severity": "high", "rationale": "Bad."} for i in range(3)
        ],
        flags=set(),
        metrics={},
    )
    assert assessment.level == "very_high"


def test_crypto_can_never_be_low_risk():
    """Not a default that evidence could overturn — a floor."""
    assessment = assess_risk(
        opportunity_type="crypto",
        hints=[],
        flags=set(),
        metrics={},
    )
    assert assessment.level != "low"
    assert LEVELS.index(assessment.level) >= LEVELS.index("high")
    assert assessment.floor_applied == "high"


def test_a_clean_non_crypto_case_can_be_low_risk():
    assessment = assess_risk(opportunity_type="business", hints=[], flags=set(), metrics={})
    assert assessment.level == "low"


def test_every_flag_becomes_an_explained_risk():
    """A penalty without a matching, explained risk row would be unaccountable."""
    flags = {"anonymous_team", "poor_liquidity", "extreme_valuation", "single_supplier"}
    assessment = assess_risk(
        opportunity_type="business",
        hints=[],
        flags=flags,
        metrics={},
    )
    codes = {r.code for r in assessment.risks}
    assert flags <= codes
    for risk in assessment.risks:
        assert risk.rationale
        assert 0 <= risk.confidence <= 1


def test_risks_are_ordered_worst_first():
    assessment = assess_risk(
        opportunity_type="business",
        hints=[
            {"code": "low1", "category": "market", "severity": "low", "rationale": "x"},
            {"code": "block", "category": "fraud_manipulation", "severity": "blocking", "rationale": "y"},
            {"code": "high1", "category": "financial", "severity": "high", "rationale": "z"},
        ],
        flags=set(),
        metrics={},
    )
    assert assessment.risks[0].severity == "blocking"
    assert assessment.risks[1].severity == "high"


def test_an_early_stage_technology_carries_technology_risk():
    assessment = assess_risk(
        opportunity_type="business",
        hints=[],
        flags=set(),
        metrics={"trend_stage": "emerging"},
    )
    assert any(r.category == "technology" for r in assessment.risks)


def test_the_risk_floor_is_reported_not_silent():
    assessment = assess_risk(opportunity_type="crypto", hints=[], flags=set(), metrics={}, min_level="high")
    assert assessment.floor_applied
    assert "floor" in assessment.reasoning.lower()
