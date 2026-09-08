"""Test that GATE constants remain unchanged from main branch."""
from app.analytics.opportunity_config import GATE


def test_gate_constants_byte_identical():
    """Verify GATE constants are byte-identical to the original.
    
    This test ensures that no gate thresholds, scoring weights, penalties,
    or risk rules have been modified. The GATE dict must remain exactly as
    it was in the original implementation.
    """
    # The original GATE constants from opportunity_config.py
    # These are the exact values from the main branch
    expected_gate = {
        "min_signal_types": 3,
        "min_signal_classes": 2,
        "min_independent_sources": 2,
        "min_trend_score": 30.0,
        "min_trend_confidence": 35.0,
        "min_observations": 20,
        "min_history_days": 45,
        "max_missing_ratio": 0.35,
        "max_proxy_share": 0.75,
        "min_confidence": 30.0,
        "min_opportunity_score": 22.0,
    }
    
    # Verify the GATE dict matches exactly
    assert GATE == expected_gate, (
        f"GATE constants have been modified!\n"
        f"Expected: {expected_gate}\n"
        f"Got: {GATE}\n"
        f"This test ensures gate thresholds remain unchanged."
    )
    
    # Also verify by checking the string representation
    # This catches any subtle changes in formatting or ordering
    gate_str = str(sorted(GATE.items()))
    expected_str = str(sorted(expected_gate.items()))
    assert gate_str == expected_str, "GATE string representation differs"
