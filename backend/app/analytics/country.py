"""Country intelligence, data coverage, and the NO DATA / NO SIGNAL distinction.

Two rules govern this whole module.

**No country gets special code.** There is no branch anywhere for Libya, Tunisia,
the United States or anywhere else. Every country is a row with the same shape,
and a country the system knows nothing about behaves exactly like one it knows a
great deal about — it simply has a lower coverage score.

**NO DATA is not NO SIGNAL.** This is the mandatory fairness rule from section 30
of the Phase 5 brief. "We looked and found nothing happening in Country X" and
"we have never looked at Country X" are completely different statements, and
reporting the second as the first is how a global system quietly writes off half
the world. Every adoption reading carries a status, the geographic engine refuses
to treat `no_data` as low adoption, and low coverage reduces confidence rather
than producing a confident negative.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import UTC, datetime
from typing import Any, Final

COVERAGE_VERSION: Final[str] = "1.0.0"

#: The facts a country profile is considered "complete" for. Weighted because a
#: missing currency matters less than a missing measure of purchasing power.
COVERAGE_FACTS: Final[dict[str, float]] = {
    "population": 1.0,
    "gdp_per_capita_ppp": 1.5,
    "currency": 0.5,
    "internet_penetration": 1.0,
    "ecommerce_maturity": 1.0,
    "digital_adoption": 1.0,
    "business_formation_days": 1.0,
    "import_restrictions": 1.5,
    "customs_environment": 1.0,
    "relevant_taxes": 1.0,
    "logistics_performance": 1.0,
    "payment_infrastructure": 1.0,
    "regulatory_environment": 1.0,
    "key_industries": 1.0,
    "labour_cost_index": 1.0,
    "electricity_reliability": 1.0,
    "trade_partners": 1.0,
}

#: How much of the coverage score comes from stored facts versus from actually
#: having collected signals about that country.
COVERAGE_WEIGHTS: Final[dict[str, float]] = {
    "facts": 0.5,
    "signals": 0.3,
    "sources": 0.2,
}

#: A fact older than this is counted at half weight: a ten-year-old figure is
#: evidence of something, but not of today.
FACT_STALE_DAYS: Final[int] = 1095

#: Below this, any geographic conclusion about the country must be caveated.
LOW_COVERAGE: Final[float] = 40.0


@dataclass(slots=True)
class FactInput:
    key: str
    as_of: datetime
    confidence: float = 0.5
    status: str = "measured"


@dataclass(slots=True)
class CoverageResult:
    version: str
    coverage: float
    parts: dict[str, Any]
    missing_facts: list[str]
    is_low: bool
    caveat: str | None


def compute_coverage(
    *,
    facts: list[FactInput],
    signal_count: int,
    source_count: int,
    now: datetime | None = None,
) -> CoverageResult:
    """How much evidence do we hold about this country? 0-100.

    This is a measure of **our** blindness, not of the country. A country can be
    enormous, sophisticated and score 15% here simply because nobody has yet
    connected a source that covers it.
    """
    now = now or datetime.now(UTC)

    total_weight = sum(COVERAGE_FACTS.values())
    earned = 0.0
    present: set[str] = set()
    for fact in facts:
        weight = COVERAGE_FACTS.get(fact.key)
        if weight is None or fact.status != "measured":
            continue
        present.add(fact.key)
        age_days = max(0, (now - fact.as_of).days)
        freshness = 1.0 if age_days <= FACT_STALE_DAYS else 0.5
        earned += weight * freshness * max(0.2, min(1.0, fact.confidence))
    fact_share = earned / total_weight if total_weight else 0.0

    # Twenty measured series is a reasonable working knowledge of a market; more
    # helps, but with diminishing returns.
    signal_share = min(1.0, signal_count / 20)
    source_share = min(1.0, source_count / 5)

    coverage = 100.0 * (
        COVERAGE_WEIGHTS["facts"] * fact_share
        + COVERAGE_WEIGHTS["signals"] * signal_share
        + COVERAGE_WEIGHTS["sources"] * source_share
    )
    coverage = round(min(100.0, coverage), 1)

    missing = sorted(set(COVERAGE_FACTS) - present)
    is_low = coverage < LOW_COVERAGE
    caveat = None
    if is_low:
        caveat = (
            f"Data coverage for this country is {coverage:.0f}%. Any conclusion drawn "
            "here describes what we have collected, not what exists. Absence of "
            "evidence is not evidence of absence."
        )

    return CoverageResult(
        version=COVERAGE_VERSION,
        coverage=coverage,
        parts={
            "facts": {
                "share": round(fact_share, 3),
                "weight": COVERAGE_WEIGHTS["facts"],
                "present": len(present),
                "possible": len(COVERAGE_FACTS),
            },
            "signals": {
                "share": round(signal_share, 3),
                "weight": COVERAGE_WEIGHTS["signals"],
                "count": signal_count,
            },
            "sources": {
                "share": round(source_share, 3),
                "weight": COVERAGE_WEIGHTS["sources"],
                "count": source_count,
            },
        },
        missing_facts=missing,
        is_low=is_low,
        caveat=caveat,
    )


# ------------------------------------------------- NO DATA versus NO SIGNAL
@dataclass(slots=True)
class AdoptionReading:
    """What we know about one subject in one country, and how sure we are of it."""

    country: str
    status: str = "no_data"  # measured | no_signal | no_data
    level: str | None = None  # none | low | emerging | growing | high
    adoption_growth: float | None = None
    attention_growth: float | None = None
    supplier_count: int | None = None
    competitor_count: int | None = None
    unit_price: float | None = None
    price_currency: str | None = None
    first_observed_days_ago: int | None = None
    observation_count: int = 0
    source_count: int = 0
    data_coverage: float | None = None

    @property
    def is_measured(self) -> bool:
        return self.status == "measured"

    @property
    def is_absence_of_evidence(self) -> bool:
        """True when we have not looked — which is not the same as finding nothing."""
        return self.status == "no_data"


def describe_absence(reading: AdoptionReading) -> str:
    """The sentence a reader must see instead of a silent zero."""
    if reading.status == "measured":
        return f"{reading.country}: measured."
    if reading.status == "no_signal":
        return f"{reading.country}: we looked and found no activity. That is a finding."
    coverage = (
        f" Our overall coverage of {reading.country} is {reading.data_coverage:.0f}%."
        if reading.data_coverage is not None
        else ""
    )
    return (
        f"{reading.country}: NO DATA — we have not measured this here, so nothing "
        f"can be concluded about adoption either way.{coverage}"
    )


LEVEL_RANK: Final[dict[str, int]] = {
    "none": 0,
    "low": 1,
    "emerging": 2,
    "growing": 3,
    "high": 4,
}


def classify_level(reading: AdoptionReading) -> str | None:
    """Band a measured reading. Returns None when there is nothing to band.

    Deliberately returns `None` rather than "none" for an unmeasured country:
    "none" is a measurement and `None` is its absence, and the difference is the
    entire point of this module.
    """
    if not reading.is_measured:
        return None
    if reading.level:
        return reading.level
    growth = reading.adoption_growth
    if growth is None:
        return None
    if growth >= 60:
        return "high"
    if growth >= 25:
        return "growing"
    if growth >= 8:
        return "emerging"
    if growth > 0:
        return "low"
    return "none"


@dataclass(slots=True)
class CoverageSummary:
    """What a reader needs before trusting any cross-country statement."""

    measured: list[str] = field(default_factory=list)
    no_signal: list[str] = field(default_factory=list)
    no_data: list[str] = field(default_factory=list)
    low_coverage: list[str] = field(default_factory=list)

    @property
    def usable(self) -> int:
        return len(self.measured)

    def sentence(self) -> str:
        bits = [f"{len(self.measured)} market(s) measured"]
        if self.no_signal:
            bits.append(f"{len(self.no_signal)} checked with no activity found")
        if self.no_data:
            bits.append(f"{len(self.no_data)} not measured at all ({', '.join(sorted(self.no_data))})")
        if self.low_coverage:
            bits.append(
                f"{len(self.low_coverage)} with thin coverage ({', '.join(sorted(self.low_coverage))})"
            )
        return "; ".join(bits) + "."


def summarise(readings: list[AdoptionReading]) -> CoverageSummary:
    summary = CoverageSummary()
    for reading in readings:
        if reading.status == "measured":
            summary.measured.append(reading.country)
        elif reading.status == "no_signal":
            summary.no_signal.append(reading.country)
        else:
            summary.no_data.append(reading.country)
        if reading.data_coverage is not None and reading.data_coverage < LOW_COVERAGE:
            summary.low_coverage.append(reading.country)
    return summary


def confidence_penalty(summary: CoverageSummary, total_markets: int) -> float:
    """How much to reduce confidence because of what we could not see.

    Returns points to subtract, 0-30. A conclusion drawn across ten markets of
    which two were measured deserves far less confidence than one drawn across
    ten of which nine were.
    """
    if total_markets <= 0:
        return 30.0
    measured_share = summary.usable / total_markets
    penalty = 30.0 * (1 - measured_share)
    if summary.low_coverage:
        penalty += min(10.0, 2.0 * len(summary.low_coverage))
    return round(min(30.0, penalty), 2)
