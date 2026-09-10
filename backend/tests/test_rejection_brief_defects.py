"""Regression tests for research-lead brief defects.

These tests reproduce the three defects observed on live data and verify they are fixed:
1. Evidence present renders as "() ()" - empty parentheses
2. Direction appears twice ("rising Rising")
3. Two different counts share one word (observations vs spike)
"""
import pytest

from app.models.models import Trend
from app.services.opportunities import _build_rejection_brief


@pytest.mark.asyncio
async def test_evidence_summary_has_non_empty_labels():
    """DEFECT 1: Evidence present should not render as empty parentheses.
    
    The backend must provide signal_type, source_group, and observation_count
    so the frontend can render meaningful labels like "Hacker News mentions (14)".
    """
    # Create a trend with evidence facts
    from app.analytics.opportunity_scoring import EvidenceFact
    
    trend = Trend(
        id="test-1",
        name="Test Trend",
        trend_score=50.0,
        confidence=60.0,
        state="candidate",
        stage="weak_signal",
        metrics={"growth_30d": 10.0},
        is_spike=False,
    )
    
    facts = [
        EvidenceFact(
            signal_type="hackernews_mentions",
            signal_class="attention",
            source_group="hackernews",
            source_reliability=0.7,
            is_proxy=False,
            is_adoption=False,
            observation_count=14,
            growth_30d=18.5,
        ),
        EvidenceFact(
            signal_type="github_stars",
            signal_class="developer_activity",
            source_group="github",
            source_reliability=0.8,
            is_proxy=False,
            is_adoption=False,
            observation_count=8,
            growth_30d=12.3,
        ),
    ]
    
    brief = _build_rejection_brief(trend, facts, ["Test reason"])
    
    # Verify evidence_summary has the required fields
    assert len(brief["evidence_summary"]) == 2
    
    ev1 = brief["evidence_summary"][0]
    assert "signal_type" in ev1
    assert "source_group" in ev1
    assert "observation_count" in ev1
    
    # Verify the values are non-empty and meaningful
    assert ev1["signal_type"] == "hackernews_mentions"
    assert ev1["source_group"] == "hackernews"
    assert ev1["observation_count"] == 14
    
    ev2 = brief["evidence_summary"][1]
    assert ev2["signal_type"] == "github_stars"
    assert ev2["source_group"] == "github"
    assert ev2["observation_count"] == 8


@pytest.mark.asyncio
async def test_direction_appears_once_with_growth_metrics():
    """DEFECT 2: Direction should not appear twice, and should include measurement details.
    
    The backend must provide growth_metrics so the frontend can show
    "30d: +18.5%" instead of just "Rising".
    """
    trend = Trend(
        id="test-2",
        name="Test Trend",
        trend_score=50.0,
        confidence=60.0,
        state="candidate",
        stage="weak_signal",
        metrics={
            "growth_7d": 5.2,
            "growth_14d": 8.7,
            "growth_30d": 18.5,
        },
        is_spike=False,
    )
    
    brief = _build_rejection_brief(trend, [], ["Test reason"])
    
    # Verify growth_metrics is populated
    assert "growth_metrics" in brief
    assert brief["growth_metrics"]["growth_7d"] == 5.2
    assert brief["growth_metrics"]["growth_14d"] == 8.7
    assert brief["growth_metrics"]["growth_30d"] == 18.5
    
    # Verify direction is set correctly
    assert brief["direction"] == "rising"


@pytest.mark.asyncio
async def test_spike_warning_is_distinct_from_total_observations():
    """DEFECT 3: Total observations and spike flag must be distinct.
    
    A trend can have many observations in its history but still be a spike
    if the recent movement rests on a single day. The frontend must show
    both the total count and the spike warning separately.
    """
    # Create a trend with long history but recent spike
    trend = Trend(
        id="test-3",
        name="Test Trend",
        trend_score=50.0,
        confidence=60.0,
        state="candidate",
        stage="weak_signal",
        metrics={"growth_30d": 10.0},
        observation_count=311,  # Total observations in history
        history_days=1410,  # Total days observed
        is_spike=True,  # But recent movement is a spike
    )
    
    brief = _build_rejection_brief(trend, [], ["Test reason"])
    
    # Verify both fields are present and distinct
    assert "observation_count" in brief
    assert "history_days" in brief
    assert "is_spike" in brief
    
    # Total history should be large
    assert brief["observation_count"] == 311
    assert brief["history_days"] == 1410
    
    # But spike flag should be True
    assert brief["is_spike"] is True
    
    # The frontend will show both: "311 observations over 1410 days" AND a spike warning
    # explaining that the recent movement rests on a single day
