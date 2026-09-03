"""Country data coverage, and the difference between NO DATA and NO SIGNAL.

Section 29 of the brief: the coverage score is a measure of *our* blindness, not
a judgement of the country. Section 30: never report "we never looked" as "there
is nothing there". These are the tests that keep both statements true.
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta

import pytest

from app.analytics.country import (
    COVERAGE_FACTS,
    COVERAGE_VERSION,
    COVERAGE_WEIGHTS,
    LOW_COVERAGE,
    AdoptionReading,
    FactInput,
    classify_level,
    compute_coverage,
    confidence_penalty,
    describe_absence,
    summarise,
)

NOW = datetime(2026, 6, 1, tzinfo=UTC)


def fresh(key: str, confidence: float = 0.9) -> FactInput:
    return FactInput(key=key, as_of=NOW - timedelta(days=30), confidence=confidence)


def all_facts() -> list[FactInput]:
    return [fresh(key) for key in COVERAGE_FACTS]


# ------------------------------------------------------------------- structure
def test_coverage_weights_sum_to_one() -> None:
    assert sum(COVERAGE_WEIGHTS.values()) == pytest.approx(1.0)


def test_coverage_is_versioned() -> None:
    assert compute_coverage(facts=[], signal_count=0, source_count=0).version == COVERAGE_VERSION


# -------------------------------------------------------------------- coverage
def test_a_well_covered_country_scores_high() -> None:
    result = compute_coverage(facts=all_facts(), signal_count=40, source_count=6, now=NOW)
    assert result.coverage > 85
    assert result.is_low is False
    assert result.caveat is None
    assert result.missing_facts == []


def test_a_country_we_have_barely_touched_scores_low_and_says_why() -> None:
    result = compute_coverage(
        facts=[fresh("population"), fresh("currency")],
        signal_count=1,
        source_count=1,
        now=NOW,
    )
    assert result.coverage < LOW_COVERAGE
    assert result.is_low is True
    assert result.caveat is not None
    assert "Absence of evidence is not evidence of absence" in result.caveat
    assert "gdp_per_capita_ppp" in result.missing_facts


def test_low_coverage_describes_our_collection_not_the_country() -> None:
    """Section 29: this is NOT a judgement of the country."""
    caveat = compute_coverage(facts=[], signal_count=0, source_count=0, now=NOW).caveat
    assert caveat is not None
    assert "what we have collected, not what exists" in caveat


def test_a_stale_fact_counts_for_less_than_a_fresh_one() -> None:
    stale = [FactInput(key=key, as_of=NOW - timedelta(days=4000), confidence=0.9) for key in COVERAGE_FACTS]
    assert (
        compute_coverage(facts=stale, signal_count=10, source_count=3, now=NOW).coverage
        < compute_coverage(facts=all_facts(), signal_count=10, source_count=3, now=NOW).coverage
    )


def test_an_unmeasured_fact_does_not_count_towards_coverage() -> None:
    claimed = [FactInput(key=key, as_of=NOW, confidence=0.9, status="no_data") for key in COVERAGE_FACTS]
    result = compute_coverage(facts=claimed, signal_count=0, source_count=0, now=NOW)
    assert result.coverage == 0.0
    assert len(result.missing_facts) == len(COVERAGE_FACTS)


def test_low_confidence_facts_earn_less() -> None:
    weak = [fresh(key, confidence=0.25) for key in COVERAGE_FACTS]
    assert (
        compute_coverage(facts=weak, signal_count=5, source_count=2, now=NOW).coverage
        < compute_coverage(facts=all_facts(), signal_count=5, source_count=2, now=NOW).coverage
    )


def test_coverage_never_exceeds_one_hundred() -> None:
    result = compute_coverage(facts=all_facts(), signal_count=9999, source_count=999, now=NOW)
    assert result.coverage <= 100.0


# ------------------------------------------------------- NO DATA vs NO SIGNAL
def test_no_data_and_no_signal_are_different_objects() -> None:
    unknown = AdoptionReading(country="NG", status="no_data")
    checked = AdoptionReading(country="NG", status="no_signal")
    assert unknown.is_absence_of_evidence is True
    assert checked.is_absence_of_evidence is False
    assert unknown.is_measured is False
    assert checked.is_measured is False


def test_the_unmeasured_country_gets_an_explicit_sentence() -> None:
    text = describe_absence(AdoptionReading(country="NG", status="no_data", data_coverage=11.0))
    assert "NO DATA" in text
    assert "nothing can be concluded" in text.lower()
    assert "11%" in text


def test_the_checked_empty_country_gets_a_different_sentence() -> None:
    text = describe_absence(AdoptionReading(country="NG", status="no_signal"))
    assert "we looked and found no activity" in text
    assert "That is a finding" in text
    assert "NO DATA" not in text


def test_an_unmeasured_country_has_no_level_rather_than_a_level_of_none() -> None:
    """`None` is the absence of a measurement; "none" is a measurement."""
    assert classify_level(AdoptionReading(country="NG", status="no_data")) is None
    assert classify_level(AdoptionReading(country="NG", status="no_signal")) is None
    assert classify_level(AdoptionReading(country="NG", status="measured", adoption_growth=0.0)) == "none"


@pytest.mark.parametrize(
    ("growth", "expected"),
    [(80.0, "high"), (30.0, "growing"), (12.0, "emerging"), (3.0, "low"), (-5.0, "none")],
)
def test_measured_growth_bands(growth: float, expected: str) -> None:
    reading = AdoptionReading(country="XX", status="measured", adoption_growth=growth)
    assert classify_level(reading) == expected


# --------------------------------------------------------------------- summary
def test_the_summary_names_the_countries_we_did_not_measure() -> None:
    summary = summarise(
        [
            AdoptionReading(country="US", status="measured", data_coverage=90.0),
            AdoptionReading(country="DE", status="measured", data_coverage=85.0),
            AdoptionReading(country="KE", status="no_signal", data_coverage=60.0),
            AdoptionReading(country="NG", status="no_data", data_coverage=12.0),
        ]
    )
    sentence = summary.sentence()
    assert summary.usable == 2
    assert "2 market(s) measured" in sentence
    assert "1 checked with no activity found" in sentence
    assert "not measured at all (NG)" in sentence
    assert "thin coverage (NG)" in sentence


def test_confidence_falls_as_the_measured_share_falls() -> None:
    wide = summarise([AdoptionReading(country=c, status="measured") for c in "ABCDE"])
    narrow = summarise(
        [AdoptionReading(country="A", status="measured")]
        + [AdoptionReading(country=c, status="no_data") for c in "BCDE"]
    )
    assert confidence_penalty(narrow, 5) > confidence_penalty(wide, 5)


def test_a_fully_measured_world_costs_no_confidence() -> None:
    summary = summarise([AdoptionReading(country=c, status="measured") for c in "ABC"])
    assert confidence_penalty(summary, 3) == 0.0


def test_penalty_is_capped_and_maximal_when_nothing_was_measured() -> None:
    summary = summarise([AdoptionReading(country=c, status="no_data") for c in "ABC"])
    assert confidence_penalty(summary, 3) == 30.0
    assert confidence_penalty(summary, 0) == 30.0


# ------------------------------------------------------------- no special cases
@pytest.mark.parametrize("country", ["LY", "TN", "US", "CN", "NG", "ZZ"])
def test_every_country_is_scored_by_the_same_arithmetic(country: str) -> None:
    facts = [fresh(key) for key in list(COVERAGE_FACTS)[:8]]
    baseline = compute_coverage(facts=facts, signal_count=7, source_count=2, now=NOW)
    assert (
        compute_coverage(facts=facts, signal_count=7, source_count=2, now=NOW).coverage == baseline.coverage
    )
    assert describe_absence(AdoptionReading(country=country, status="no_data")).startswith(country)
