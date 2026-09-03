import math

import pytest

from app.analytics.statistics import (
    acceleration,
    analyse_series,
    changepoint,
    ewma,
    moving_average,
    pct_change,
    zscore,
)


def test_pct_change_basic():
    assert pct_change(110, 100) == pytest.approx(10.0)
    assert pct_change(90, 100) == pytest.approx(-10.0)


def test_pct_change_guards_zero_and_none():
    assert pct_change(10, 0) is None
    assert pct_change(10, None) is None


def test_moving_average_needs_full_window():
    assert moving_average([1, 2, 3], window=7) is None
    assert moving_average([1] * 7, window=7) == 1.0


def test_zscore_flags_a_spike():
    flat = [100.0] * 30
    assert zscore(flat) is None  # zero variance -> undefined, not infinite
    series = [100.0 + (i % 3) for i in range(30)] + [200.0]
    z = zscore(series)
    assert z is not None and z > 5


def test_zscore_needs_history():
    assert zscore([1.0, 2.0, 3.0]) is None


def test_ewma_tracks_recent_values():
    assert ewma([1, 1, 1]) == pytest.approx(1.0)
    assert ewma([0, 0, 0, 10], alpha=0.5) == pytest.approx(5.0)


def test_acceleration_positive_when_growth_speeds_up():
    values = [100.0] * 8 + [100 * math.exp(0.1 * i) for i in range(1, 9)]
    accel = acceleration(values, window=7)
    assert accel is not None and accel > 0


def test_acceleration_none_on_short_series():
    assert acceleration([1, 2, 3], window=7) is None


def test_changepoint_finds_the_shift():
    values = [10.0] * 20 + [50.0] * 20
    idx = changepoint(values)
    assert idx is not None and 15 <= idx <= 25


def test_changepoint_none_on_stationary_series():
    values = [10.0 + (i % 2) * 0.1 for i in range(40)]
    assert changepoint(values) is None


def test_analyse_series_reports_insufficient_history_instead_of_guessing():
    stats = analyse_series([1.0])
    assert stats.direction == "unknown"
    assert "insufficient" in stats.note.lower()
    assert stats.is_anomaly is False


def test_analyse_series_is_deterministic():
    values = [float(i) ** 1.2 for i in range(1, 60)]
    assert analyse_series(values) == analyse_series(values)
