"""Source reliability scoring (docs/data-source-guide.md section 4)."""

from __future__ import annotations

CLASS_PRIORS: dict[str, float] = {
    "official": 0.95,
    "government": 0.95,
    "regulated_filing": 0.90,
    "primary_api": 0.80,
    "aggregator": 0.60,
    "forum_social": 0.40,
    "anonymous_blog": 0.25,
    "manual_import": 0.70,
    "demo": 0.50,
}


def compute_reliability(
    source_class: str,
    recent_failure_rate: float,
    historical_precision: float | None = None,
) -> float:
    """Blend a class prior with observed failures and backtested precision.

    `historical_precision` is None until the backtesting module has data; in that
    case its weight is redistributed to the prior rather than assumed to be good.
    """
    prior = CLASS_PRIORS.get(source_class, 0.5)
    failure_rate = min(max(recent_failure_rate, 0.0), 1.0)
    if historical_precision is None:
        score = 0.7 * prior + 0.3 * (1.0 - failure_rate)
    else:
        score = 0.5 * prior + 0.3 * (1.0 - failure_rate) + 0.2 * historical_precision
    return round(min(max(score, 0.0), 1.0), 4)
