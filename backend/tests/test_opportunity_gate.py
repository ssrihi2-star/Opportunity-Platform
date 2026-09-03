"""The evidence gate: what is allowed to become an opportunity candidate at all.

The gate is the most important piece of restraint in Phase 4. Its job is to refuse,
and most of these tests assert that it refuses.
"""

from __future__ import annotations

from app.analytics.opportunity_config import GATE
from app.analytics.opportunity_scoring import EvidenceFact, OpportunityInput, check_gate


def fact(
    signal_type: str,
    signal_class: str,
    group: str,
    *,
    proxy: bool = False,
    adoption: bool = False,
    growth: float | None = 30.0,
    reliability: float = 0.8,
) -> EvidenceFact:
    return EvidenceFact(
        signal_type=signal_type,
        signal_class=signal_class,
        source_group=group,
        source_reliability=reliability,
        is_proxy=proxy,
        is_adoption=adoption,
        growth_30d=growth,
        observation_count=200,
        days_since_latest=1,
    )


def good_input(**overrides) -> OpportunityInput:
    base = {
        "opportunity_type": "business",
        "trend_score": 60.0,
        "trend_confidence": 70.0,
        "trend_stage": "early_adoption",
        "trend_state": "active",
        "trend_history_days": 180,
        "trend_observation_count": 200,
        "facts": [
            fact("github_contributors", "developer", "gh", adoption=True),
            fact("import_growth", "trade", "customs", adoption=True),
            fact("customer_count_growth", "commercial", "vendor", adoption=True),
        ],
    }
    base.update(overrides)
    return OpportunityInput(**base)


def test_a_good_case_passes():
    result = check_gate(good_input())
    assert result.passed, result.reasons


def test_three_measurements_of_the_same_kind_are_not_three_signal_types():
    """Three news feeds are one kind of evidence, not three."""
    inp = good_input(
        facts=[
            fact("media_coverage_growth", "attention", "reuters"),
            fact("media_coverage_growth", "attention", "bloomberg"),
            fact("media_coverage_growth", "attention", "ap"),
        ]
    )
    result = check_gate(inp)
    assert not result.passed
    assert any("signal type" in r for r in result.reasons)


def test_different_kinds_of_evidence_are_required():
    """Three distinct types can still all be attention, which is not corroboration."""
    inp = good_input(
        facts=[
            fact("media_coverage_growth", "attention", "a"),
            fact("social_discussion_growth", "attention", "b"),
            fact("wiki_pageview_growth", "attention", "c"),
        ]
    )
    result = check_gate(inp)
    assert not result.passed
    assert any("kind" in r for r in result.reasons)


def test_one_source_family_fails_even_with_many_signal_types():
    inp = good_input(
        facts=[
            fact("github_contributors", "developer", "same", adoption=True),
            fact("import_growth", "trade", "same", adoption=True),
            fact("customer_count_growth", "commercial", "same", adoption=True),
        ]
    )
    result = check_gate(inp)
    assert not result.passed
    assert any("independent source" in r for r in result.reasons)


def test_a_one_day_spike_never_becomes_an_opportunity():
    result = check_gate(good_input(trend_is_spike=True))
    assert not result.passed
    assert any("single observation" in r for r in result.reasons)


def test_a_seasonal_pattern_never_becomes_an_opportunity():
    result = check_gate(good_input(trend_is_seasonal=True))
    assert not result.passed
    assert any("every year" in r for r in result.reasons)


def test_a_merely_candidate_trend_is_not_ready():
    result = check_gate(good_input(trend_state="candidate"))
    assert not result.passed
    assert any("candidate" in r for r in result.reasons)


def test_an_invalidated_trend_can_never_become_a_business_plan():
    result = check_gate(good_input(trend_state="invalidated"))
    assert not result.passed


def test_a_weak_trend_score_is_refused():
    result = check_gate(good_input(trend_score=GATE["min_trend_score"] - 1))
    assert not result.passed
    assert any("Trend score" in r for r in result.reasons)


def test_too_little_history_is_refused():
    result = check_gate(good_input(trend_history_days=10))
    assert not result.passed
    assert any("days of history" in r for r in result.reasons)


def test_too_few_observations_is_refused():
    result = check_gate(good_input(trend_observation_count=5))
    assert not result.passed
    assert any("observations" in r for r in result.reasons)


def test_mostly_missing_data_is_refused():
    inp = good_input(trend_observation_count=100, trend_missing_count=60)
    result = check_gate(inp)
    assert not result.passed
    assert any("never reported" in r for r in result.reasons)


def test_proxy_only_evidence_cannot_carry_a_case():
    inp = good_input(
        facts=[
            fact("wiki_pageview_growth", "attention", "wiki", proxy=True),
            fact("search_growth", "attention", "search", proxy=True),
            fact("social_discussion_growth", "attention", "social", proxy=True),
            fact("media_coverage_growth", "attention", "news", proxy=True),
        ]
    )
    result = check_gate(inp)
    assert not result.passed
    assert any("proxy" in r for r in result.reasons)


def test_every_refusal_explains_itself():
    """A refusal with no reason is a bug: the reason is the product."""
    result = check_gate(good_input(trend_state="candidate", trend_score=1, facts=[]))
    assert not result.passed
    assert len(result.reasons) >= 3
    assert all(r.endswith(".") and len(r) > 20 for r in result.reasons)


def test_gate_metrics_are_reported_even_on_success():
    result = check_gate(good_input())
    assert result.metrics["signal_types"] == 3
    assert result.metrics["independent_sources"] == 3
    assert result.metrics["adoption_facts"] == 3
