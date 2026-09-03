"""Deterministic time-series statistics.

No LLM participates in any function here. Every result is reproducible.
All functions tolerate short or ragged series and say so rather than guessing.
"""

from __future__ import annotations

from dataclasses import dataclass

import numpy as np


@dataclass(slots=True)
class SeriesStats:
    n: int
    pct_change_last: float | None
    pct_change_window: float | None
    moving_average: float | None
    zscore_last: float | None
    ewma_last: float | None
    acceleration: float | None
    changepoint_index: int | None
    is_anomaly: bool
    direction: str
    strength: float
    note: str = ""


def pct_change(current: float, previous: float | None) -> float | None:
    if previous is None or previous == 0:
        return None
    return (current - previous) / abs(previous) * 100.0


def moving_average(values: list[float], window: int = 7) -> float | None:
    if len(values) < window:
        return None
    return float(np.mean(values[-window:]))


def zscore(values: list[float], window: int = 30) -> float | None:
    """Z-score of the last point against the preceding window (excluding itself)."""
    if len(values) < 8:
        return None
    history = np.asarray(values[-(window + 1) : -1], dtype=float)
    if history.size < 7:
        return None
    sd = float(history.std(ddof=1))
    if sd == 0:
        return None
    return float((values[-1] - history.mean()) / sd)


def ewma(values: list[float], alpha: float = 0.3) -> float | None:
    if not values:
        return None
    result = values[0]
    for v in values[1:]:
        result = alpha * v + (1 - alpha) * result
    return float(result)


def acceleration(values: list[float], window: int = 7) -> float | None:
    """Growth-of-growth: compares the latest window's growth to the prior window's.

    Returned in percentage points. Positive means the series is speeding up, which
    is the property this product cares about far more than absolute level.
    """
    if len(values) < window * 2 + 1:
        return None
    recent = values[-window:]
    prior = values[-2 * window : -window]
    g_recent = pct_change(recent[-1], recent[0])
    g_prior = pct_change(prior[-1], prior[0])
    if g_recent is None or g_prior is None:
        return None
    return float(g_recent - g_prior)


def changepoint(values: list[float], min_segment: int = 5) -> int | None:
    """Cheap single changepoint via maximum mean-shift t-statistic.

    Not as principled as PELT, but deterministic, dependency-free and adequate for
    daily series of a few hundred points. Returns the index of the shift, or None.
    """
    n = len(values)
    if n < min_segment * 2:
        return None
    arr = np.asarray(values, dtype=float)
    best_idx, best_stat = None, 0.0
    for i in range(min_segment, n - min_segment):
        left, right = arr[:i], arr[i:]
        pooled = np.sqrt(left.var(ddof=1) / left.size + right.var(ddof=1) / right.size)
        if pooled == 0:
            continue
        stat = abs(right.mean() - left.mean()) / pooled
        if stat > best_stat:
            best_idx, best_stat = i, float(stat)
    return best_idx if best_stat >= 3.0 else None


def analyse_series(values: list[float], *, window: int = 7, z_threshold: float = 2.5) -> SeriesStats:
    n = len(values)
    if n == 0:
        return SeriesStats(
            0,
            None,
            None,
            None,
            None,
            None,
            None,
            None,
            False,
            "unknown",
            0.0,
            "Empty series: nothing can be concluded.",
        )
    if n < 3:
        return SeriesStats(
            n,
            None,
            None,
            None,
            None,
            None,
            None,
            None,
            False,
            "unknown",
            0.0,
            f"Only {n} observation(s): insufficient history for statistics.",
        )

    last_change = pct_change(values[-1], values[-2])
    window_change = pct_change(values[-1], values[-min(window + 1, n)])
    z = zscore(values)
    accel = acceleration(values, window=window)
    cp = changepoint(values)

    if window_change is None:
        direction = "unknown"
    elif window_change > 1:
        direction = "up"
    elif window_change < -1:
        direction = "down"
    else:
        direction = "flat"

    is_anomaly = bool(z is not None and abs(z) >= z_threshold) or cp is not None

    # Strength blends deviation and acceleration, both bounded, so no single
    # runaway term can dominate.
    z_part = min(abs(z) / 5.0, 1.0) if z is not None else 0.0
    a_part = min(abs(accel) / 50.0, 1.0) if accel is not None else 0.0
    strength = round(0.6 * z_part + 0.4 * a_part, 4)

    note = ""
    if n < window * 2 + 1:
        note = f"Series has {n} points; acceleration needs {window * 2 + 1}."

    return SeriesStats(
        n=n,
        pct_change_last=last_change,
        pct_change_window=window_change,
        moving_average=moving_average(values, window),
        zscore_last=z,
        ewma_last=ewma(values),
        acceleration=accel,
        changepoint_index=cp,
        is_anomaly=is_anomaly,
        direction=direction,
        strength=strength,
        note=note,
    )
