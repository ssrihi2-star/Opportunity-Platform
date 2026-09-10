"""Test that _build_rejection_brief correctly handles all four directions."""
import pytest

from app.models.models import Trend
from app.services.opportunities import _build_rejection_brief


@pytest.mark.asyncio
async def test_direction_unknown_when_metrics_none():
    """When trend.metrics is None, direction should be 'unknown'."""
    trend = Trend(
        id="test-1",
        name="Test Trend",
        trend_score=50.0,
        confidence=60.0,
        state="candidate",
        stage="weak_signal",
        metrics=None,
    )
    facts = []
    reasons = ["Test reason"]
    
    brief = _build_rejection_brief(trend, facts, reasons)
    
    assert brief["direction"] == "unknown"


@pytest.mark.asyncio
async def test_direction_unknown_when_growth_30d_missing():
    """When trend.metrics has no growth_30d key, direction should be 'unknown'."""
    trend = Trend(
        id="test-2",
        name="Test Trend",
        trend_score=50.0,
        confidence=60.0,
        state="candidate",
        stage="weak_signal",
        metrics={"other_metric": 10.0},
    )
    facts = []
    reasons = ["Test reason"]
    
    brief = _build_rejection_brief(trend, facts, reasons)
    
    assert brief["direction"] == "unknown"


@pytest.mark.asyncio
async def test_direction_unknown_when_growth_30d_none():
    """When trend.metrics['growth_30d'] is None, direction should be 'unknown'."""
    trend = Trend(
        id="test-3",
        name="Test Trend",
        trend_score=50.0,
        confidence=60.0,
        state="candidate",
        stage="weak_signal",
        metrics={"growth_30d": None},
    )
    facts = []
    reasons = ["Test reason"]
    
    brief = _build_rejection_brief(trend, facts, reasons)
    
    assert brief["direction"] == "unknown"


@pytest.mark.asyncio
async def test_direction_rising():
    """When growth_30d > 5.0, direction should be 'rising'."""
    trend = Trend(
        id="test-4",
        name="Test Trend",
        trend_score=50.0,
        confidence=60.0,
        state="candidate",
        stage="weak_signal",
        metrics={"growth_30d": 10.0},
    )
    facts = []
    reasons = ["Test reason"]
    
    brief = _build_rejection_brief(trend, facts, reasons)
    
    assert brief["direction"] == "rising"


@pytest.mark.asyncio
async def test_direction_flat():
    """When -5.0 <= growth_30d <= 5.0, direction should be 'flat'."""
    trend = Trend(
        id="test-5",
        name="Test Trend",
        trend_score=50.0,
        confidence=60.0,
        state="candidate",
        stage="weak_signal",
        metrics={"growth_30d": 0.0},
    )
    facts = []
    reasons = ["Test reason"]
    
    brief = _build_rejection_brief(trend, facts, reasons)
    
    assert brief["direction"] == "flat"


@pytest.mark.asyncio
async def test_direction_declining():
    """When growth_30d < -5.0, direction should be 'declining'."""
    trend = Trend(
        id="test-6",
        name="Test Trend",
        trend_score=50.0,
        confidence=60.0,
        state="candidate",
        stage="weak_signal",
        metrics={"growth_30d": -10.0},
    )
    facts = []
    reasons = ["Test reason"]
    
    brief = _build_rejection_brief(trend, facts, reasons)
    
    assert brief["direction"] == "declining"
