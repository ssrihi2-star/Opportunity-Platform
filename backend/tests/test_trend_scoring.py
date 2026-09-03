"""Trend Score, Confidence and the stage/lifecycle ladders."""

import pytest

from app.analytics.trend_scoring import (
    COMPONENT_MAX,
    MAX_TOTAL_PENALTY,
    TREND_FORMULA_VERSION,
    TrendInput,
    classify_stage,
    compute_confidence,
    next_state,
    score_trend,
)


def strong(**overrides) -> TrendInput:
    base = dict(
        growth_30d=140.0,
        growth_90d=380.0,
        acceleration_pp=65.0,
        persistence=1.0,
        momentum=2.0,
        direction="rising",
        baseline_value=1200.0,
        latest_value=6400.0,
        absolute_growth_total=5200.0,
        prior_maximum=4200.0,
        observation_count=90,
        missing_count=0,
        history_days=180,
        days_since_last_observation=1,
        independent_sources=3,
        distinct_signal_types=3,
        distinct_signal_classes=3,
        non_proxy_classes=2,
        mean_source_reliability=0.72,
        dominant_source_share=0.4,
        geo_scopes=["global", "US"],
        agreeing_methods=3,
    )
    base.update(overrides)
    return TrendInput(**base)


def hype(**overrides) -> TrendInput:
    base = dict(
        growth_30d=900.0,
        acceleration_pp=800.0,
        persistence=0.4,
        direction="rising",
        baseline_value=3.0,
        latest_value=300.0,
        absolute_growth_total=297.0,
        prior_maximum=12.0,
        observation_count=14,
        missing_count=0,
        history_days=14,
        days_since_last_observation=1,
        independent_sources=1,
        distinct_signal_types=1,
        distinct_signal_classes=1,
        non_proxy_classes=0,
        mean_source_reliability=0.4,
        dominant_source_share=1.0,
        duplication_ratio=0.7,
        geo_scopes=["global"],
        is_one_day_spike=True,
        spike_note="One observation reached 300 against a typical 10.",
        agreeing_methods=1,
    )
    base.update(overrides)
    return TrendInput(**base)


# ------------------------------------------------------------------- basics
def test_scoring_is_deterministic():
    assert score_trend(strong()).to_dict() == score_trend(strong()).to_dict()


def test_every_component_is_explained_and_within_bounds():
    result = score_trend(strong())
    assert {c.name for c in result.components} == set(COMPONENT_MAX)
    for component in result.components:
        assert component.rationale
        assert 0 <= component.points <= component.max_points
    assert result.formula_version == TREND_FORMULA_VERSION


def test_component_maxima_sum_to_one_hundred():
    assert sum(COMPONENT_MAX.values()) == 100


def test_a_real_accelerating_trend_scores_well_with_good_confidence():
    result = score_trend(strong())
    assert result.trend_score >= 70
    assert result.confidence >= 70
    assert result.stage == "accelerating"


def test_fake_viral_hype_is_crushed_despite_enormous_growth():
    result = score_trend(hype())
    assert result.trend_score <= 15, "a +900% one-day spike must not look like a trend"
    assert result.stage == "weak_signal"
    assert "one_day_spike" in result.penalties
    assert "tiny_baseline" in result.penalties
    assert "duplicate_information" in result.penalties


def test_growth_from_a_tiny_baseline_is_penalised():
    result = score_trend(strong(baseline_value=3, absolute_growth_total=9, latest_value=12))
    assert "tiny_baseline" in result.penalties
    assert any("arithmetic, not evidence" in w for w in result.warnings)


def test_a_large_absolute_move_from_a_small_base_is_only_half_penalised():
    """1 to 10 is noise. 20 to 900 is real, but 20 is still a thin place to start."""
    noise = score_trend(strong(baseline_value=3, absolute_growth_total=9, latest_value=12))
    real_move = score_trend(strong(baseline_value=20, absolute_growth_total=880))
    big_base = score_trend(strong(baseline_value=1200))

    assert real_move.penalties["tiny_baseline"] < noise.penalties["tiny_baseline"]
    assert "tiny_baseline" not in big_base.penalties
    assert any("overstates" in w for w in real_move.warnings)


def test_single_source_scores_no_diversity_and_takes_a_penalty():
    result = score_trend(strong(independent_sources=1, distinct_signal_classes=1))
    diversity = next(c for c in result.components if c.name == "source_diversity")
    assert diversity.points == 0
    assert "single_source" in result.penalties


def test_penalties_are_capped():
    result = score_trend(
        hype(missing_count=40, observation_count=10, is_seasonal=True, mean_source_reliability=0.1)
    )
    assert result.penalty_total == MAX_TOTAL_PENALTY
    assert any("capped" in w for w in result.warnings)


def test_score_and_confidence_are_independent():
    """Strong-looking movement, almost no history: high score, low confidence."""
    result = score_trend(
        strong(
            history_days=14,
            observation_count=8,
            independent_sources=2,
            mean_source_reliability=0.5,
            agreeing_methods=3,
        )
    )
    assert result.confidence < 60
    assert result.trend_score > result.confidence


def test_missing_data_lowers_confidence():
    complete = compute_confidence(strong())[0]
    holed = compute_confidence(strong(missing_count=40))[0]
    assert holed < complete


def test_stale_data_lowers_confidence():
    fresh = compute_confidence(strong())[0]
    stale = compute_confidence(strong(days_since_last_observation=120))[0]
    assert stale < fresh


def test_confidence_parts_are_shown():
    _, parts = compute_confidence(strong())
    assert set(parts) >= {
        "history",
        "observations",
        "source_quality",
        "independent_sources",
        "method_agreement",
    }


# -------------------------------------------------------------------- stages
def test_declining_activity_is_declining():
    stage, evidence = classify_stage(strong(direction="falling", growth_30d=-30, persistence=0.1), 20)
    assert stage == "declining"
    assert evidence


def test_thin_evidence_is_a_weak_signal_however_good_the_numbers():
    stage, _ = classify_stage(strong(observation_count=5, independent_sources=1), 80)
    assert stage == "weak_signal"


def test_steady_growth_lands_in_emerging_or_early_adoption():
    stage, _ = classify_stage(
        strong(growth_30d=18, acceleration_pp=-5, persistence=1.0, latest_value=200), 45
    )
    assert stage in {"emerging", "early_adoption"}


def test_large_and_flat_is_mature():
    stage, _ = classify_stage(strong(growth_30d=1, acceleration_pp=0, latest_value=500_000), 30)
    assert stage == "mature"


def test_large_and_growing_is_mainstream():
    stage, _ = classify_stage(
        strong(growth_30d=9, acceleration_pp=2, latest_value=500_000, persistence=0.8), 50
    )
    assert stage == "mainstream"


# ----------------------------------------------------------------- lifecycle
def test_a_new_trend_starts_as_a_candidate():
    state, reason = next_state(
        current_state=None,
        trend_score=20,
        confidence=20,
        peak_score=20,
        independent_sources=1,
        days_since_last_observation=1,
        is_one_day_spike=False,
        is_seasonal=False,
        stage="emerging",
    )
    assert state == "candidate"
    assert reason


def test_a_strong_multi_source_trend_is_confirmed():
    state, _ = next_state(
        current_state="active",
        trend_score=75,
        confidence=70,
        peak_score=75,
        independent_sources=3,
        days_since_last_observation=1,
        is_one_day_spike=False,
        is_seasonal=False,
        stage="accelerating",
    )
    assert state == "confirmed"


def test_a_high_score_from_one_source_stays_a_candidate():
    state, reason = next_state(
        current_state=None,
        trend_score=80,
        confidence=80,
        peak_score=80,
        independent_sources=1,
        days_since_last_observation=1,
        is_one_day_spike=False,
        is_seasonal=False,
        stage="accelerating",
    )
    assert state == "candidate"
    assert "one independent source" in reason.lower() or "1 independent" in reason


def test_a_spike_never_becomes_active():
    state, _ = next_state(
        current_state=None,
        trend_score=90,
        confidence=90,
        peak_score=90,
        independent_sources=4,
        days_since_last_observation=1,
        is_one_day_spike=True,
        is_seasonal=False,
        stage="weak_signal",
    )
    assert state == "candidate"


def test_a_previously_confirmed_trend_that_turns_out_to_be_a_spike_is_invalidated():
    state, reason = next_state(
        current_state="confirmed",
        trend_score=40,
        confidence=60,
        peak_score=80,
        independent_sources=3,
        days_since_last_observation=1,
        is_one_day_spike=True,
        is_seasonal=False,
        stage="weak_signal",
    )
    assert state == "invalidated"
    assert "single observation" in reason


def test_a_seasonal_pattern_never_becomes_active():
    state, reason = next_state(
        current_state=None,
        trend_score=70,
        confidence=80,
        peak_score=70,
        independent_sources=3,
        days_since_last_observation=1,
        is_one_day_spike=False,
        is_seasonal=True,
        stage="mature",
    )
    assert state == "candidate"
    assert "every year" in reason


def test_a_big_drop_from_the_peak_is_weakening():
    state, reason = next_state(
        current_state="confirmed",
        trend_score=45,
        confidence=70,
        peak_score=80,
        independent_sources=3,
        days_since_last_observation=1,
        is_one_day_spike=False,
        is_seasonal=False,
        stage="emerging",
    )
    assert state == "weakening"
    assert "fallen" in reason


def test_silence_ends_a_trend():
    state, reason = next_state(
        current_state="confirmed",
        trend_score=70,
        confidence=70,
        peak_score=70,
        independent_sources=3,
        days_since_last_observation=200,
        is_one_day_spike=False,
        is_seasonal=False,
        stage="accelerating",
    )
    assert state == "ended"
    assert "gone cold" in reason


@pytest.mark.parametrize("score", [0, 25, 50, 75, 100])
def test_scores_stay_inside_bounds(score):
    result = score_trend(strong(growth_30d=score * 10))
    assert 0 <= result.trend_score <= 100
    assert 0 <= result.confidence <= 100
