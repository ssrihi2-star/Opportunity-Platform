"""Scoring must be deterministic, transparent and impossible to game upward."""

import json
import pathlib

import pytest

from app.analytics.gate import check_gate
from app.analytics.scoring import RiskFlag, ScoringInput, score_opportunity
from app.analytics.scoring_config import FORMULA_VERSION, MAX_MEME_SCORE, MAX_TOTAL_PENALTY

VECTORS = pathlib.Path(__file__).parent / "data" / "scoring_vectors.json"


def strong_input(**overrides) -> ScoringInput:
    base = dict(
        category="technology",
        adoption_growth_pcts=[60.0, 80.0],
        acceleration_pp=30.0,
        market_size_usd=2_000_000_000,
        source_reliabilities=[0.8, 0.9, 0.7],
        independent_source_count=3,
        dominant_source_share=0.4,
        signal_types=["github_contributors", "job_posting_growth", "package_downloads"],
        accessibility="buildable_solo",
        liquidity="not_applicable",
        capital_required_usd=5_000,
        user_capital_max_usd=15_000,
        defensibility_markers=["network_effect", "proprietary_data"],
        catalyst_dated=True,
        catalyst_has_evidence=True,
        satisfied_evidence_fields=["adoption_metric", "developer_activity", "who_is_building", "timeline"],
        cross_source_agreement=0.8,
        newest_evidence_age_days=3,
    )
    base.update(overrides)
    return ScoringInput(**base)


def test_scoring_is_pure_and_repeatable():
    assert score_opportunity(strong_input()).to_dict() == score_opportunity(strong_input()).to_dict()


def test_every_component_carries_a_rationale():
    result = score_opportunity(strong_input())
    assert len(result.components) == 8
    for component in result.components:
        assert component.rationale, f"{component.name} has no explanation"
        assert 0 <= component.points <= component.max_points


def test_unknown_market_size_scores_zero_and_says_so():
    result = score_opportunity(strong_input(market_size_usd=None))
    market = next(c for c in result.components if c.name == "market_size")
    assert market.points == 0
    assert "unknown" in market.rationale.lower()
    assert "rather than estimated" in market.rationale


def test_attention_only_earns_no_adoption_points():
    result = score_opportunity(strong_input(adoption_growth_pcts=[]))
    adoption = next(c for c in result.components if c.name == "real_adoption_growth")
    assert adoption.points == 0
    assert "Attention alone does not count" in adoption.rationale


def test_single_source_is_penalised_and_scores_no_confirmation():
    result = score_opportunity(
        strong_input(independent_source_count=1, source_reliabilities=[0.9], dominant_source_share=1.0)
    )
    confirmation = next(c for c in result.components if c.name == "independent_source_confirmation")
    assert confirmation.points == 0
    assert "single_source_dependence" in result.penalties


def test_deceleration_scores_zero_acceleration():
    result = score_opportunity(strong_input(acceleration_pp=-15.0))
    accel = next(c for c in result.components if c.name == "signal_acceleration")
    assert accel.points == 0
    assert "decelerating" in accel.rationale


def test_capital_above_user_limit_kills_entry_score():
    result = score_opportunity(strong_input(capital_required_usd=100_000))
    entry = next(c for c in result.components if c.name == "entry_attractiveness")
    assert entry.points == 0
    assert "above the configured maximum" in entry.rationale


def test_penalties_are_capped():
    flags = [
        RiskFlag("anonymous_founders", "high"),
        RiskFlag("concentrated_ownership", "high"),
        RiskFlag("thin_liquidity", "high"),
        RiskFlag("extreme_valuation", "high"),
        RiskFlag("no_working_product", "high"),
        RiskFlag("paid_influencer_promotion", "high"),
        RiskFlag("active_enforcement", "high"),
        RiskFlag("dominant_incumbent", "medium"),
        RiskFlag("operational_difficulty", "medium"),
    ]
    result = score_opportunity(strong_input(risk_flags=flags, manipulation_probability=0.9))
    assert result.penalty_total == MAX_TOTAL_PENALTY
    assert any("capped" in n for n in result.notes)


def test_blocking_flag_zeroes_the_score():
    result = score_opportunity(
        strong_input(risk_flags=[RiskFlag("honeypot_behaviour", "blocking", "Sell disabled")])
    )
    assert result.blocked is True
    assert result.adjusted_score == 0.0
    assert result.risk_level == "very_high"


def test_crypto_can_never_be_low_risk():
    result = score_opportunity(strong_input(is_crypto=True, category="public_investment"))
    assert result.risk_level in {"high", "very_high"}


def test_crypto_escalating_flag_forces_very_high():
    result = score_opportunity(
        strong_input(is_crypto=True, risk_flags=[RiskFlag("anonymous_founders", "medium")])
    )
    assert result.risk_level == "very_high"


def test_meme_asset_score_is_capped():
    result = score_opportunity(strong_input(is_meme=True))
    assert result.adjusted_score <= MAX_MEME_SCORE
    assert any("meme" in n.lower() for n in result.notes)


def test_low_evidence_completeness_triggers_weak_evidence_penalty():
    result = score_opportunity(strong_input(satisfied_evidence_fields=["timeline"]))
    assert result.evidence_completeness == 0.25
    assert "weak_evidence" in result.penalties


def test_confidence_falls_with_stale_evidence():
    fresh = score_opportunity(strong_input(newest_evidence_age_days=1)).confidence
    stale = score_opportunity(strong_input(newest_evidence_age_days=120)).confidence
    assert stale < fresh


def test_scores_stay_inside_bounds():
    result = score_opportunity(strong_input())
    assert 0 <= result.raw_score <= 100
    assert 0 <= result.adjusted_score <= 100
    assert 0 <= result.confidence <= 1
    assert result.formula_version == FORMULA_VERSION


def test_fewer_than_three_signal_types_is_flagged_as_a_candidate():
    result = score_opportunity(strong_input(signal_types=["github_stars", "github_forks"]))
    assert any("candidate, not an opportunity" in n for n in result.notes)


# ------------------------------------------------------------------ gate tests
def test_gate_rejects_single_signal_type():
    gate = check_gate(
        distinct_signal_types=1,
        independent_sources=3,
        confidence=0.9,
        newest_evidence_age_days=1,
        maturity_stage="emerging",
        blocking_flags=[],
    )
    assert not gate.passed
    assert any("signal type" in r for r in gate.reasons)


def test_gate_rejects_single_source():
    gate = check_gate(
        distinct_signal_types=4,
        independent_sources=1,
        confidence=0.9,
        newest_evidence_age_days=1,
        maturity_stage="emerging",
        blocking_flags=[],
    )
    assert not gate.passed
    assert any("independent source" in r for r in gate.reasons)


def test_gate_rejects_mature_stage():
    gate = check_gate(
        distinct_signal_types=4,
        independent_sources=3,
        confidence=0.9,
        newest_evidence_age_days=1,
        maturity_stage="mature",
        blocking_flags=[],
    )
    assert not gate.passed


def test_gate_passes_when_everything_is_satisfied():
    gate = check_gate(
        distinct_signal_types=3,
        independent_sources=2,
        confidence=0.7,
        newest_evidence_age_days=5,
        maturity_stage="early_adoption",
        blocking_flags=[],
    )
    assert gate.passed and gate.reasons == []


# ------------------------------------------------------- golden regression file
@pytest.mark.skipif(not VECTORS.exists(), reason="golden vectors not generated")
def test_golden_vectors_still_match():
    payload = json.loads(VECTORS.read_text())
    assert payload["formula_version"] == FORMULA_VERSION, (
        "The scoring formula changed but the golden vectors were not regenerated. "
        "Run python -m scripts.generate_scoring_vectors and review the diff."
    )
    for case in payload["cases"]:
        result = score_opportunity(
            ScoringInput(
                **{
                    k: ([RiskFlag(**f) for f in v] if k == "risk_flags" else v)
                    for k, v in case["input"].items()
                }
            )
        )
        assert result.adjusted_score == case["expected"]["adjusted_score"], case["name"]
        assert result.raw_score == case["expected"]["raw_score"], case["name"]
        assert result.risk_level == case["expected"]["risk_level"], case["name"]
        assert result.confidence == case["expected"]["confidence"], case["name"]


# ---------------------------------------------------- source reliability priors
def test_reliability_prefers_official_sources_over_forums():
    from app.sources.reliability import compute_reliability

    official = compute_reliability("official", recent_failure_rate=0.0)
    forum = compute_reliability("forum_social", recent_failure_rate=0.0)
    assert official > forum
    assert 0.0 <= forum <= official <= 1.0


def test_reliability_falls_with_failures():
    from app.sources.reliability import compute_reliability

    healthy = compute_reliability("primary_api", recent_failure_rate=0.0)
    flaky = compute_reliability("primary_api", recent_failure_rate=0.9)
    assert flaky < healthy


def test_unknown_backtest_precision_is_not_assumed_good():
    from app.sources.reliability import compute_reliability

    # With no backtest data the prior carries the weight, rather than a made-up
    # precision of 1.0 inflating the score.
    unknown = compute_reliability("aggregator", 0.0, historical_precision=None)
    perfect = compute_reliability("aggregator", 0.0, historical_precision=1.0)
    assert unknown < perfect
