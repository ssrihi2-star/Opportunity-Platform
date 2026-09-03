"""The shapes a series can take, and what the engine must say about each."""

from datetime import UTC, datetime, timedelta

import pytest

from app.analytics.growth import (
    MIN_MEANINGFUL_BASELINE,
    Point,
    analyse_growth,
    detect_seasonality,
    detect_spike,
    windowed_growth,
)

END = datetime(2026, 9, 1, tzinfo=UTC)


def series(values, step_days=1, end=END, statuses=None):
    n = len(values)
    start = end - timedelta(days=(n - 1) * step_days)
    return [
        Point(
            at=start + timedelta(days=i * step_days),
            value=values[i],
            status=(statuses[i] if statuses else "ok"),
        )
        for i in range(n)
    ]


# ------------------------------------------------------------- missing vs zero
def test_missing_periods_are_excluded_not_counted_as_zero():
    values = [100, None, 120, None, 140, 160, 180, 200]
    statuses = ["ok", "missing", "ok", "failed", "ok", "ok", "ok", "ok"]
    profile = analyse_growth(series(values, statuses=statuses))
    assert profile.observation_count == 6
    assert profile.missing_count == 2
    assert profile.direction == "rising"
    assert profile.latest_value == 200
    assert any("not reported" in n for n in profile.notes)


def test_a_zero_reading_is_a_real_measurement():
    """Zero means 'we measured nothing happening'; it must not be dropped."""
    profile = analyse_growth(series([50, 40, 0, 30, 20, 10, 0, 0]))
    assert profile.observation_count == 8
    assert profile.missing_count == 0
    assert profile.direction == "falling"


def test_an_all_missing_series_says_so_instead_of_reporting_zero_growth():
    profile = analyse_growth(series([None] * 5, statuses=["missing"] * 5))
    assert profile.observation_count == 0
    assert profile.direction == "unknown"
    assert profile.growth_30d is None
    assert "No usable observations" in profile.notes[0]


# ------------------------------------------------------------------- windows
def test_growth_window_returns_none_without_enough_history():
    """A 20-day series has no 90-day growth. Inventing one manufactures trends."""
    points = series([100 + i for i in range(20)])
    assert windowed_growth(points, 90) == (None, None)
    assert windowed_growth(points, 7)[0] is not None


def test_growth_window_measures_from_the_right_point():
    points = series([100] * 30 + [200])
    pct, absolute = windowed_growth(points, 7)
    assert pct == pytest.approx(100.0)
    assert absolute == pytest.approx(100.0)


# --------------------------------------------------------------------- shapes
def test_real_acceleration_is_recognised_and_is_not_a_spike():
    profile = analyse_growth(series([100, 110, 140, 220, 400, 700, 1200], step_days=7))
    assert profile.direction == "rising"
    assert profile.acceleration_pp > 100
    assert profile.persistence == 1.0
    assert profile.spike.is_one_day_spike is False, (
        "an accelerating series always peaks at its newest point; that is growth, not a spike"
    )


def test_one_day_viral_spike_is_caught():
    profile = analyse_growth(series([10, 11, 9, 300, 12, 11, 10]))
    assert profile.spike.is_one_day_spike is True
    assert profile.spike.ratio_to_typical > 20
    assert profile.spike.reverted is True
    assert "returned to normal" in profile.spike.note


def test_a_spike_on_the_newest_day_is_also_caught():
    profile = analyse_growth(series([10, 11, 9, 12, 11, 10, 300]))
    assert profile.spike.is_one_day_spike is True
    assert profile.spike.peak_is_latest is True


def test_a_sustained_step_up_is_not_called_a_spike():
    profile = analyse_growth(series([10, 11, 9, 300, 290, 310, 305, 295]))
    assert profile.spike.is_one_day_spike is False
    assert "step change" in profile.spike.note or profile.spike.note == ""


def test_steady_growth_is_rising_but_not_accelerating():
    profile = analyse_growth(series([100, 110, 120, 130, 140, 150, 160, 170], step_days=7))
    assert profile.direction == "rising"
    assert profile.persistence == 1.0
    assert profile.acceleration_pp <= 0, (
        "a straight line grows by a shrinking percentage; that is not acceleration"
    )


def test_declining_series_is_recognised():
    profile = analyse_growth(series([500, 470, 440, 410, 380, 350, 320], step_days=7))
    assert profile.direction == "falling"
    assert profile.persistence == 0.0


def test_tiny_baseline_is_flagged_however_large_the_percentage():
    profile = analyse_growth(series([1, 2, 3, 4, 6, 8, 10]))
    assert profile.has_tiny_baseline is True
    assert profile.baseline_value < MIN_MEANINGFUL_BASELINE
    assert profile.has_tiny_absolute_move is True
    assert any("not strong evidence" in n for n in profile.notes)


# ---------------------------------------------------------------- seasonality
def _seasonal_months(years=3, peak_month=9):
    import math

    points = []
    end = datetime(2026, 9, 15, tzinfo=UTC)
    for k in range(years * 12):
        offset = years * 12 - 1 - k
        month_date = end - timedelta(days=30 * offset)
        factor = 1 + 0.55 * math.cos(2 * math.pi * (month_date.month - peak_month) / 12)
        points.append(Point(at=month_date, value=round(900 * factor, 2)))
    return points


def test_seasonal_pattern_is_identified_and_not_called_new():
    report = detect_seasonality(_seasonal_months())
    assert report.is_seasonal is True
    assert report.cycles_observed >= 2
    assert abs(report.yoy_change_pct) < 15
    assert "seasonal" in report.note


def test_seasonality_needs_two_cycles_before_it_will_answer():
    one_year = _seasonal_months(years=1)
    report = detect_seasonality(one_year)
    assert report.is_seasonal is False
    assert "cycles" in report.note or "untestable" in report.note


def test_a_genuinely_growing_series_is_not_called_seasonal():
    points = [
        Point(at=datetime(2024, 1, 15, tzinfo=UTC) + timedelta(days=30 * k), value=100 * (1.06**k))
        for k in range(36)
    ]
    assert detect_seasonality(points).is_seasonal is False


def test_spike_detection_needs_a_few_points_before_it_will_answer():
    report = detect_spike(series([1, 500, 1]))
    assert report.is_one_day_spike is False
    assert "Too few" in report.note
