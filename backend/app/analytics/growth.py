"""How a measured series is changing, and whether the change means anything.

Everything here is deterministic arithmetic over stored observations. No model
is consulted, and none of these numbers is ever produced by one.

Two ideas do most of the work:

* **A gap is not a zero.** Observations whose status is not `ok` are excluded
  from every calculation and counted separately, because "the source did not
  report" and "the value was nought" support opposite conclusions.
* **Percentage growth alone is meaningless at small scale.** One mention
  becoming ten is +900% and almost never matters. Absolute growth, baseline
  size, observation count and duration are carried alongside every percentage
  so the scoring layer can refuse to be impressed.
"""

from __future__ import annotations

import math
from dataclasses import dataclass, field
from datetime import datetime, timedelta
from statistics import median

import numpy as np

#: Below this baseline a percentage is arithmetic noise rather than evidence.
MIN_MEANINGFUL_BASELINE = 25.0
#: And below this absolute change, a big percentage is still a small event.
MIN_MEANINGFUL_ABSOLUTE = 15.0
#: A single point this many times the typical level is a candidate spike.
SPIKE_RATIO = 5.0
#: A raised level lasting at least this many points is a step change, not a spike.
SPIKE_MAX_WIDTH = 2
#: Seasonality needs at least two cycles to be distinguishable from a trend.
MIN_DAYS_FOR_SEASONALITY = 400


@dataclass(slots=True, frozen=True)
class Point:
    at: datetime
    value: float | None
    status: str = "ok"

    @property
    def is_ok(self) -> bool:
        return self.status == "ok" and self.value is not None


@dataclass(slots=True)
class SpikeReport:
    is_one_day_spike: bool = False
    peak_index: int | None = None
    peak_value: float | None = None
    peak_at: datetime | None = None
    ratio_to_typical: float | None = None
    width: int = 0
    reverted: bool = False
    peak_is_latest: bool = False
    note: str = ""


@dataclass(slots=True)
class SeasonalityReport:
    is_seasonal: bool = False
    cycles_observed: int = 0
    month_index: float | None = None  # current month's level vs the annual mean
    yoy_change_pct: float | None = None  # trailing-year total vs the year before
    note: str = ""


@dataclass(slots=True)
class GrowthProfile:
    observation_count: int = 0
    missing_count: int = 0
    history_days: int = 0
    first_at: datetime | None = None
    last_at: datetime | None = None
    latest_value: float | None = None
    baseline_value: float | None = None
    typical_value: float | None = None

    growth_7d: float | None = None
    growth_30d: float | None = None
    growth_90d: float | None = None
    growth_365d: float | None = None
    absolute_growth_30d: float | None = None
    absolute_growth_total: float | None = None

    momentum: float | None = None  # mean daily relative change, recent window
    acceleration_pp: float | None = None  # recent growth minus prior growth, in points
    persistence: float | None = None  # 0-1: share of sub-windows that rose

    direction: str = "unknown"  # rising | falling | flat | unknown
    spike: SpikeReport = field(default_factory=SpikeReport)
    seasonality: SeasonalityReport = field(default_factory=SeasonalityReport)
    notes: list[str] = field(default_factory=list)

    @property
    def has_tiny_baseline(self) -> bool:
        return (self.baseline_value or 0.0) < MIN_MEANINGFUL_BASELINE

    @property
    def has_tiny_absolute_move(self) -> bool:
        return abs(self.absolute_growth_total or 0.0) < MIN_MEANINGFUL_ABSOLUTE

    def to_dict(self) -> dict:
        return {
            "observation_count": self.observation_count,
            "missing_count": self.missing_count,
            "history_days": self.history_days,
            "latest_value": self.latest_value,
            "baseline_value": self.baseline_value,
            "typical_value": self.typical_value,
            "growth_7d": self.growth_7d,
            "growth_30d": self.growth_30d,
            "growth_90d": self.growth_90d,
            "growth_365d": self.growth_365d,
            "absolute_growth_30d": self.absolute_growth_30d,
            "absolute_growth_total": self.absolute_growth_total,
            "momentum": self.momentum,
            "acceleration_pp": self.acceleration_pp,
            "persistence": self.persistence,
            "direction": self.direction,
            "is_one_day_spike": self.spike.is_one_day_spike,
            "spike_ratio": self.spike.ratio_to_typical,
            "is_seasonal": self.seasonality.is_seasonal,
            "seasonal_yoy_change_pct": self.seasonality.yoy_change_pct,
            "notes": self.notes,
        }


# --------------------------------------------------------------------- helpers
def _pct(current: float, previous: float) -> float | None:
    if previous == 0:
        return None
    return (current - previous) / abs(previous) * 100.0


def _value_at_or_before(points: list[Point], target: datetime) -> float | None:
    """The most recent reported value at or before `target`, or None.

    Returning None rather than the nearest available value matters: if the series
    only starts after `target`, there is no 90-day growth, and inventing one from
    a 20-day history would be the single easiest way to manufacture a trend.
    """
    best: float | None = None
    for point in points:
        if point.at <= target:
            best = point.value
        else:
            break
    return best


def windowed_growth(points: list[Point], days: int) -> tuple[float | None, float | None]:
    """(percentage, absolute) change over the trailing `days`, or (None, None)."""
    if len(points) < 2:
        return None, None
    last = points[-1]
    target = last.at - timedelta(days=days)
    if points[0].at > target:
        return None, None  # not enough history to answer this question honestly
    earlier = _value_at_or_before(points, target)
    if earlier is None or last.value is None:
        return None, None
    return _pct(last.value, earlier), last.value - earlier


def detect_spike(points: list[Point]) -> SpikeReport:
    """Distinguish a one-day burst from a genuine step up in level.

    The test case that matters: 10, 11, 9, 300, 12. The percentage growth from
    the third to the fourth point is enormous, and it means nothing.
    """
    values = [p.value for p in points if p.is_ok and p.value is not None]
    if len(values) < 5:
        return SpikeReport(note="Too few observations to separate a spike from a trend.")

    peak_index = max(range(len(values)), key=lambda i: values[i])
    peak = values[peak_index]
    others = values[:peak_index] + values[peak_index + 1 :]
    typical = median(others)
    if typical <= 0:
        return SpikeReport(note="Typical level is zero; a ratio would be meaningless.")

    ratio = peak / typical
    # A spike must also stand apart from its immediate neighbours. Without this
    # check every genuinely accelerating series is flagged, because its maximum is
    # always the newest point: 700 -> 1200 is growth, 9 -> 300 is a spike.
    previous = values[peak_index - 1] if peak_index > 0 else None
    following = values[peak_index + 1] if peak_index + 1 < len(values) else None
    neighbours = [v for v in (previous, following) if v is not None and v > 0]
    local_ratio = peak / min(neighbours) if neighbours else None

    # How many consecutive points around the peak are also elevated? A raised
    # plateau is a level change; a single tall bar is a spike.
    threshold = typical * 3
    width = 1
    i = peak_index - 1
    while i >= 0 and values[i] >= threshold:
        width += 1
        i -= 1
    j = peak_index + 1
    while j < len(values) and values[j] >= threshold:
        width += 1
        j += 1

    after = values[peak_index + 1 :]
    reverted = bool(after) and median(after) <= typical * 2
    peak_is_latest = peak_index == len(values) - 1

    is_spike = (
        ratio >= SPIKE_RATIO
        and local_ratio is not None
        and local_ratio >= SPIKE_RATIO
        and width <= SPIKE_MAX_WIDTH
        and (reverted or peak_is_latest)
    )
    note = ""
    if is_spike and reverted:
        note = (
            f"One observation reached {peak:,.0f} against a typical {typical:,.0f} "
            f"({ratio:.0f}x) and the level then returned to normal."
        )
    elif is_spike and peak_is_latest:
        note = (
            f"The most recent observation is {ratio:.0f}x the typical level with no "
            "history behind it yet - treat as unconfirmed until it holds."
        )
    elif ratio >= SPIKE_RATIO and local_ratio is not None and local_ratio < SPIKE_RATIO:
        note = (
            f"The peak is {ratio:.0f}x the typical level but only {local_ratio:.1f}x its "
            "neighbours, so the series climbed into it rather than jumping."
        )
    elif ratio >= SPIKE_RATIO:
        note = f"A {ratio:.0f}x rise held for {width} observations, so it reads as a step change."

    peak_point = [p for p in points if p.is_ok][peak_index]
    return SpikeReport(
        is_one_day_spike=is_spike,
        peak_index=peak_index,
        peak_value=peak,
        peak_at=peak_point.at,
        ratio_to_typical=round(ratio, 3),
        width=width,
        reverted=reverted,
        peak_is_latest=peak_is_latest,
        note=note,
    )


def detect_seasonality(points: list[Point]) -> SeasonalityReport:
    """Is the current rise just the same rise that happens every year?

    Requires two full cycles. With less history, the honest answer is "cannot
    tell", not "no" - a first-year construction peak looks exactly like a new
    trend, and only a second year separates them.
    """
    ok = [p for p in points if p.is_ok and p.value is not None]
    if len(ok) < 24:
        return SeasonalityReport(note="Fewer than 24 observations; seasonality is untestable.")
    span_days = (ok[-1].at - ok[0].at).days
    if span_days < MIN_DAYS_FOR_SEASONALITY:
        return SeasonalityReport(
            cycles_observed=0,
            note=f"Only {span_days} days of history; two yearly cycles are needed.",
        )

    by_month: dict[int, list[float]] = {}
    for point in ok:
        by_month.setdefault(point.at.month, []).append(float(point.value))  # type: ignore[arg-type]
    overall = float(np.mean([v for values in by_month.values() for v in values]))
    if overall <= 0:
        return SeasonalityReport(note="Mean level is zero; no seasonal index can be formed.")

    current_month = ok[-1].at.month
    month_index = float(np.mean(by_month.get(current_month, [overall]))) / overall

    last_year = [p for p in ok if p.at > ok[-1].at - timedelta(days=365)]
    prior_year = [p for p in ok if ok[-1].at - timedelta(days=730) < p.at <= ok[-1].at - timedelta(days=365)]
    yoy = None
    if last_year and prior_year:
        recent_total = sum(float(p.value) for p in last_year)  # type: ignore[arg-type]
        prior_total = sum(float(p.value) for p in prior_year)  # type: ignore[arg-type]
        yoy = _pct(recent_total, prior_total)

    cycles = span_days // 365
    # Seasonal means: this month is reliably above average, and the year as a whole
    # is not actually growing.
    is_seasonal = bool(month_index >= 1.15 and yoy is not None and abs(yoy) < 15.0)
    note = ""
    if is_seasonal:
        note = (
            f"{current_month:02d} is normally {(month_index - 1) * 100:.0f}% above this series' "
            f"average, and the year-on-year total moved only {yoy:+.1f}%. The rise looks seasonal."
        )
    elif yoy is not None:
        note = (
            f"Year-on-year total moved {yoy:+.1f}%; the current month runs "
            f"{(month_index - 1) * 100:+.0f}% against the series average."
        )
    return SeasonalityReport(
        is_seasonal=is_seasonal,
        cycles_observed=int(cycles),
        month_index=round(month_index, 3),
        yoy_change_pct=round(yoy, 3) if yoy is not None else None,
        note=note,
    )


def _persistence(values: list[float]) -> float | None:
    """Share of consecutive block-to-block comparisons that rose.

    Blocks rather than raw points, so ordinary noise does not read as a reversal.
    The block count adapts to the series length; a short series still deserves an
    answer, just a coarser one.
    """
    if len(values) < 6:
        return None
    blocks = min(5, max(3, len(values) // 3))
    chunks = np.array_split(np.asarray(values, dtype=float), blocks)
    means = [float(c.mean()) for c in chunks if c.size]
    if len(means) < 2:
        return None
    rises = sum(1 for a, b in zip(means, means[1:], strict=False) if b > a)
    return round(rises / (len(means) - 1), 4)


def _momentum(points: list[Point], days: int = 30) -> float | None:
    """Mean daily relative change over the recent window, as a percentage per day."""
    ok = [p for p in points if p.is_ok]
    if len(ok) < 4:
        return None
    cutoff = ok[-1].at - timedelta(days=days)
    window = [p for p in ok if p.at >= cutoff]
    if len(window) < 3:
        window = ok[-min(len(ok), 8) :]
    xs = np.array([(p.at - window[0].at).total_seconds() / 86400.0 for p in window])
    ys = np.array([float(p.value) for p in window])  # type: ignore[arg-type]
    if xs.max() == xs.min() or ys.mean() == 0:
        return None
    slope = float(np.polyfit(xs, ys, 1)[0])
    return round(slope / abs(float(ys.mean())) * 100.0, 4)


def analyse_growth(points: list[Point]) -> GrowthProfile:
    """The full deterministic picture of one series."""
    ordered = sorted(points, key=lambda p: p.at)
    ok = [p for p in ordered if p.is_ok]
    missing = [p for p in ordered if not p.is_ok]

    profile = GrowthProfile(
        observation_count=len(ok),
        missing_count=len(missing),
        first_at=ok[0].at if ok else None,
        last_at=ok[-1].at if ok else None,
    )
    if not ok:
        profile.notes.append("No usable observations: every point is missing or failed.")
        return profile

    profile.history_days = (ok[-1].at - ok[0].at).days
    values = [float(p.value) for p in ok]  # type: ignore[arg-type]
    profile.latest_value = values[-1]
    profile.typical_value = float(median(values))
    # The baseline is the early level, not the single first reading, so one odd
    # first day cannot define the whole comparison.
    head = values[: max(1, min(5, len(values) // 4))]
    profile.baseline_value = float(median(head))

    profile.growth_7d, _ = windowed_growth(ok, 7)
    profile.growth_30d, profile.absolute_growth_30d = windowed_growth(ok, 30)
    profile.growth_90d, _ = windowed_growth(ok, 90)
    profile.growth_365d, _ = windowed_growth(ok, 365)
    profile.absolute_growth_total = values[-1] - profile.baseline_value

    profile.momentum = _momentum(ok)
    profile.persistence = _persistence(values)

    # Acceleration: is the recent growth rate higher than the one before it?
    if len(values) >= 6:
        half = len(values) // 2
        early, late = values[:half], values[half:]
        g_early = _pct(early[-1], early[0])
        g_late = _pct(late[-1], late[0])
        if g_early is not None and g_late is not None:
            profile.acceleration_pp = round(g_late - g_early, 4)

    reference = profile.growth_30d if profile.growth_30d is not None else profile.growth_90d
    if reference is None:
        total = _pct(values[-1], values[0])
        reference = total
    if reference is None:
        profile.direction = "unknown"
    elif reference > 5:
        profile.direction = "rising"
    elif reference < -5:
        profile.direction = "falling"
    else:
        profile.direction = "flat"

    profile.spike = detect_spike(ordered)
    profile.seasonality = detect_seasonality(ordered)

    if missing:
        profile.notes.append(
            f"{len(missing)} of {len(ordered)} periods were not reported and are excluded "
            "from every calculation."
        )
    if profile.has_tiny_baseline:
        profile.notes.append(
            f"Baseline is only {profile.baseline_value:,.0f}; percentage growth from such a "
            "small starting point is not strong evidence."
        )
    if profile.history_days < 30:
        profile.notes.append(
            f"Only {profile.history_days} days of history; longer-window growth cannot be measured."
        )
    if math.isclose(profile.typical_value or 0.0, 0.0):
        profile.notes.append("Typical level is zero, so percentages are undefined.")
    return profile
