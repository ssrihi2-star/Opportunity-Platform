"""The trend-level growth figure is an average, and the brief must be able to say so.

`services/trends.py::aggregate` computes the trend's growth_7d/14d/30d/90d as a
WEIGHTED MEAN across the trend's signal series, skipping series with no value.
So "+58.9%" is not a reading of anything — no single series carries it, and none
of the series measures money.

The display fixes that by naming it a weighted average, saying how many series
have a measured change, and listing what each series actually did. The middle one
is phrased as a fact about available data rather than about contribution, because
`_weighted` also skips zero-weight series and this payload cannot identify those.
Those three things are only possible
if the brief payload carries per-series growth and preserves a missing value as
missing. That contract is what these tests pin.

The rendering itself has no automated test: this repository has no frontend test
harness, and adding one is out of scope for a display correction.
"""

import pytest

from app.analytics.opportunity_scoring import EvidenceFact
from app.models.models import Trend
from app.services.opportunities import _build_rejection_brief


def _fact(signal_type: str, source_group: str, growth_30d: float | None) -> EvidenceFact:
    return EvidenceFact(
        signal_type=signal_type,
        signal_class="attention",
        source_group=source_group,
        source_reliability=0.7,
        is_proxy=False,
        is_adoption=False,
        observation_count=12,
        growth_30d=growth_30d,
    )


def _trend() -> Trend:
    return Trend(
        id="agg-1",
        name="Aggregate Test",
        trend_score=50.0,
        confidence=40.0,
        state="candidate",
        stage="weak_signal",
        # A blend: no series below actually equals 58.9.
        metrics={"growth_7d": 12.0, "growth_30d": 58.9},
        is_spike=False,
    )


@pytest.mark.asyncio
async def test_every_series_carries_its_own_change():
    """Each evidence row must report the change for THAT series.

    Without this the page can only show the blended number, which is the defect:
    a reader sees +58.9% and has no way to learn what was measured.
    """
    facts = [
        _fact("hackernews_mentions", "hackernews", 72.1),
        _fact("wikipedia_pageviews", "wikipedia", 45.7),
    ]
    brief = _build_rejection_brief(_trend(), facts, ["not enough signal types"])

    rows = brief["evidence_summary"]
    assert len(rows) == 2
    for row in rows:
        assert "growth_30d" in row, "a series with no change figure cannot be described"
        assert row["signal_type"], "an unlabelled series renders as empty parentheses"
        assert row["source_group"]

    assert [r["growth_30d"] for r in rows] == [72.1, 45.7]


@pytest.mark.asyncio
async def test_a_series_with_no_measured_change_stays_none():
    """Missing must survive as None, never as 0.0.

    The aggregate skips these series, and the page prints "not measured" for them.
    Coercing the gap to zero would invent a flat series that was never observed —
    the same failure as showing an absent score as a zero bar.
    """
    facts = [
        _fact("hackernews_mentions", "hackernews", 72.1),
        _fact("github_stars", "github", None),
    ]
    brief = _build_rejection_brief(_trend(), facts, ["not enough signal types"])

    by_type = {r["signal_type"]: r["growth_30d"] for r in brief["evidence_summary"]}
    assert by_type["github_stars"] is None
    assert by_type["github_stars"] != 0.0
    assert by_type["hackernews_mentions"] == 72.1


@pytest.mark.asyncio
async def test_how_many_series_have_a_measured_change_is_derivable():
    """"N of M series have a measured 30-day change" needs no new backend field.

    N is the rows carrying a change, M is every row shown. Here two of three
    have one, so the page may say two — and never three.

    Note what this deliberately does NOT claim: that those N series fed the
    average. `_weighted` also skips zero-weight series, and nothing in this
    payload identifies them, so the wording stays a statement about available
    data rather than about contribution.
    """
    facts = [
        _fact("hackernews_mentions", "hackernews", 72.1),
        _fact("wikipedia_pageviews", "wikipedia", 45.7),
        _fact("github_stars", "github", None),
    ]
    brief = _build_rejection_brief(_trend(), facts, ["not enough signal types"])

    rows = brief["evidence_summary"]
    covered = len([r for r in rows if r["growth_30d"] is not None])
    assert (covered, len(rows)) == (2, 3)


@pytest.mark.asyncio
async def test_the_blended_figure_matches_no_single_series():
    """The reason the label matters: the aggregate is nobody's measurement.

    If a reader could point at a series and say "that is the 58.9%", the bare
    number would be defensible. They cannot.
    """
    facts = [
        _fact("hackernews_mentions", "hackernews", 72.1),
        _fact("wikipedia_pageviews", "wikipedia", 45.7),
    ]
    trend = _trend()
    brief = _build_rejection_brief(trend, facts, ["not enough signal types"])

    blended = brief["growth_metrics"]["growth_30d"]
    per_series = [r["growth_30d"] for r in brief["evidence_summary"]]
    assert blended not in per_series
    assert min(per_series) < blended < max(per_series)
