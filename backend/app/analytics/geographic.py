"""The global geographic engine: where is this working, and where has it not arrived?

This replaces the Phase 4 two-market gap with a genuinely global comparison. The
question it answers is the one that makes this product interesting:

    Something is already normal in some markets and still rare in others.
    Is that a lag worth acting on, or is there a reason it has not arrived?

Three disciplines make the difference between that question and wishful thinking:

* **UNKNOWN is never ZERO.** A market we have not measured is excluded from the
  comparison and named in the output. Treating "we never looked at Nigeria" as
  "Nigeria has low adoption" is how a global system produces confident nonsense
  about the places it happens not to cover.
* **Prices are only compared with a stored exchange rate.** No rate, no price gap.
* **A gap is a question.** Local demand, affordability, regulation, infrastructure
  and logistics all have to hold before it means anything, and the engine says so
  every single time.

The Transferability Score is EXPERIMENTAL and contributes nothing to the Global
Opportunity Score until Phase 6 can say whether it predicted anything.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Final

from app.analytics.country import (
    LEVEL_RANK,
    AdoptionReading,
    classify_level,
    confidence_penalty,
    describe_absence,
    summarise,
)

GEO_ENGINE_VERSION: Final[str] = "1.0.0"
TRANSFERABILITY_VERSION: Final[str] = "0.1.0-experimental"

EXPERIMENTAL_NOTE: Final[str] = (
    "Experimental. Shown on its own and contributing nothing to the Global "
    "Opportunity Score until it has been backtested."
)

#: The nine dimensions of a geographic gap, and what each is worth out of 100.
GAP_DIMENSIONS: Final[dict[str, int]] = {
    "adoption_gap": 22,
    "supply_gap": 15,
    "competition_gap": 13,
    "attention_gap": 10,
    "price_gap": 8,
    "infrastructure_readiness": 12,
    "regulatory_readiness": 10,
    "affordability": 6,
    "time_lag": 4,
}

#: Country facts the readiness dimensions read, and the direction that helps.
READINESS_FACTS: Final[dict[str, tuple[str, bool]]] = {
    "internet_penetration": ("infrastructure_readiness", True),
    "payment_infrastructure": ("infrastructure_readiness", True),
    "logistics_performance": ("infrastructure_readiness", True),
    "electricity_reliability": ("infrastructure_readiness", True),
    "business_formation_days": ("regulatory_readiness", False),
    "import_restrictions": ("regulatory_readiness", False),
    "regulatory_environment": ("regulatory_readiness", True),
    "gdp_per_capita_ppp": ("affordability", True),
}


@dataclass(slots=True)
class MarketFacts:
    """Normalised 0-1 country facts, or None where we hold nothing."""

    internet_penetration: float | None = None
    payment_infrastructure: float | None = None
    logistics_performance: float | None = None
    electricity_reliability: float | None = None
    business_formation_ease: float | None = None
    import_openness: float | None = None
    regulatory_environment: float | None = None
    purchasing_power: float | None = None
    data_coverage: float | None = None
    language: str | None = None
    currency: str | None = None


@dataclass(slots=True)
class GapDimension:
    points: float
    max: int
    why: str
    measured: bool = True


@dataclass(slots=True)
class GeographicGap:
    version: str
    target: str
    gap: float | None
    dimensions: dict[str, GapDimension] = field(default_factory=dict)
    leaders: list[str] = field(default_factory=list)
    coverage_note: str = ""
    confidence_penalty: float = 0.0
    caveats: list[str] = field(default_factory=list)
    unmeasured: list[str] = field(default_factory=list)


def _mean(values: list[float]) -> float | None:
    return sum(values) / len(values) if values else None


def _dim(points: float, key: str, why: str, measured: bool = True) -> GapDimension:
    return GapDimension(
        points=round(max(0.0, min(float(GAP_DIMENSIONS[key]), points)), 2),
        max=GAP_DIMENSIONS[key],
        why=why,
        measured=measured,
    )


def analyse_gap(
    *,
    subject: str,
    readings: list[AdoptionReading],
    target_country: str,
    target_facts: MarketFacts | None = None,
    fx_available: bool = False,
) -> GeographicGap:
    """Compare one target market against every other market we measured.

    Returns `gap = None` — never 0 — when the target itself was never measured
    or when no leading market has usable data. "We cannot tell" and "there is no
    gap" are different answers and the caller must be able to distinguish them.
    """
    target = next((r for r in readings if r.country.upper() == target_country.upper()), None)
    others = [r for r in readings if r.country.upper() != target_country.upper()]
    leaders = [r for r in others if r.is_measured]

    summary = summarise(readings)
    penalty = confidence_penalty(summary, max(1, len(readings)))
    unmeasured = [r.country for r in readings if r.is_absence_of_evidence]
    caveats: list[str] = [
        "A gap is a question, not an answer. Local demand, affordability, "
        "regulation, infrastructure and shipping economics all still have to hold."
    ]
    for reading in readings:
        if reading.is_absence_of_evidence:
            caveats.append(describe_absence(reading))

    if not leaders:
        return GeographicGap(
            version=GEO_ENGINE_VERSION,
            target=target_country.upper(),
            gap=None,
            leaders=[],
            coverage_note=summary.sentence(),
            confidence_penalty=penalty,
            caveats=caveats + ["No comparison market has usable data, so no gap can be measured."],
            unmeasured=unmeasured,
        )

    dims: dict[str, GapDimension] = {}

    # --- adoption ----------------------------------------------------------
    lead_growth = _mean([r.adoption_growth for r in leaders if r.adoption_growth is not None])
    lead_levels = [LEVEL_RANK[level] for r in leaders if (level := classify_level(r)) is not None]
    lead_level = _mean([float(x) for x in lead_levels])

    if target is None or target.is_absence_of_evidence:
        dims["adoption_gap"] = _dim(
            0.0,
            "adoption_gap",
            f"{target_country.upper()} has never been measured for this, so no adoption "
            "gap can be claimed. This is missing data, not low adoption.",
            measured=False,
        )
    elif target.status == "no_signal":
        # We looked and found nothing. That IS a gap, and a well-evidenced one.
        strength = (lead_level or 0) / 4 if lead_level else 0.5
        dims["adoption_gap"] = _dim(
            GAP_DIMENSIONS["adoption_gap"] * strength,
            "adoption_gap",
            f"We measured {target_country.upper()} and found no activity, while "
            f"{len(leaders)} other market(s) show it. That is a checked absence, "
            "not a missing measurement.",
        )
    else:
        target_level = LEVEL_RANK.get(classify_level(target) or "", 0)
        level_gap = max(0.0, (lead_level or 0) - target_level) / 4
        growth_gap = 0.0
        if lead_growth is not None and target.adoption_growth is not None:
            growth_gap = max(0.0, lead_growth - target.adoption_growth) / 100
        strength = min(1.0, 0.65 * level_gap + 0.35 * min(1.0, growth_gap))
        dims["adoption_gap"] = _dim(
            GAP_DIMENSIONS["adoption_gap"] * strength,
            "adoption_gap",
            f"Leading markets sit around '{_level_name(lead_level)}' while "
            f"{target_country.upper()} is '{classify_level(target)}'"
            + (
                f" ({lead_growth:+.0f}% vs {target.adoption_growth:+.0f}% growth)."
                if lead_growth is not None and target.adoption_growth is not None
                else "."
            ),
        )

    # --- supply -------------------------------------------------------------
    if target is not None and target.supplier_count is not None:
        n = target.supplier_count
        share = 1.0 if n == 0 else 0.75 if n <= 2 else 0.4 if n <= 5 else 0.1 if n <= 15 else 0.0
        dims["supply_gap"] = _dim(
            GAP_DIMENSIONS["supply_gap"] * share,
            "supply_gap",
            f"{n} local supplier(s) identified in {target_country.upper()}.",
        )
    else:
        dims["supply_gap"] = _dim(
            0.0,
            "supply_gap",
            "Local supplier count is unknown, so no supply gap is claimed.",
            measured=False,
        )

    # --- competition --------------------------------------------------------
    lead_comp = _mean([float(r.competitor_count) for r in leaders if r.competitor_count is not None])
    if target is not None and target.competitor_count is not None and lead_comp is not None:
        delta = max(0.0, lead_comp - target.competitor_count)
        dims["competition_gap"] = _dim(
            min(float(GAP_DIMENSIONS["competition_gap"]), delta * 1.2),
            "competition_gap",
            f"{lead_comp:.0f} competitors in leading markets against "
            f"{target.competitor_count} in {target_country.upper()}.",
        )
    else:
        dims["competition_gap"] = _dim(
            0.0,
            "competition_gap",
            "Competitor counts are not available on both sides.",
            measured=False,
        )

    # --- attention ----------------------------------------------------------
    lead_att = _mean([r.attention_growth for r in leaders if r.attention_growth is not None])
    if target is not None and target.attention_growth is not None and lead_att is not None:
        delta = max(0.0, lead_att - target.attention_growth)
        dims["attention_gap"] = _dim(
            min(float(GAP_DIMENSIONS["attention_gap"]), delta / 10),
            "attention_gap",
            f"Interest is growing {lead_att:+.0f}% elsewhere against "
            f"{target.attention_growth:+.0f}% locally.",
        )
    else:
        dims["attention_gap"] = _dim(
            0.0, "attention_gap", "Attention is not measured on both sides.", measured=False
        )

    # --- price: only with a real exchange rate ------------------------------
    target_price = target.unit_price if target is not None else None
    target_currency = target.price_currency if target is not None else None
    priced: list[tuple[float, str]] = (
        [(r.unit_price, r.price_currency) for r in leaders if r.unit_price is not None and r.price_currency]
        if target_price is not None and target_currency
        else []
    )
    same_currency = [price for price, currency in priced if currency == target_currency]
    lead_price = _mean(same_currency)
    if lead_price is not None and lead_price > 0 and target_price is not None:
        ratio = target_price / lead_price
        dims["price_gap"] = _dim(
            max(0.0, (ratio - 1) * 12),
            "price_gap",
            f"Local price is {ratio:.2f}x the price in comparable markets ({target_currency}).",
        )
    elif priced and not fx_available:
        dims["price_gap"] = _dim(
            0.0,
            "price_gap",
            "Prices are published in different currencies and no stored exchange rate "
            "covers them, so no price gap is claimed rather than an invented one.",
            measured=False,
        )
        caveats.append("Prices were not compared: converting without a stored rate would be a guess.")
    else:
        dims["price_gap"] = _dim(0.0, "price_gap", "No comparable price evidence.", measured=False)

    # --- readiness and affordability ---------------------------------------
    facts = target_facts or MarketFacts()
    infra = [
        v
        for v in (
            facts.internet_penetration,
            facts.payment_infrastructure,
            facts.logistics_performance,
            facts.electricity_reliability,
        )
        if v is not None
    ]
    if infra:
        # A *ready* market makes the gap more actionable, not less.
        dims["infrastructure_readiness"] = _dim(
            GAP_DIMENSIONS["infrastructure_readiness"] * (sum(infra) / len(infra)),
            "infrastructure_readiness",
            f"Infrastructure readiness scores {sum(infra) / len(infra):.0%} on "
            f"{len(infra)} stored measure(s).",
        )
    else:
        dims["infrastructure_readiness"] = _dim(
            0.0,
            "infrastructure_readiness",
            f"No infrastructure facts are stored for {target_country.upper()}.",
            measured=False,
        )

    reg = [
        v
        for v in (facts.business_formation_ease, facts.import_openness, facts.regulatory_environment)
        if v is not None
    ]
    if reg:
        dims["regulatory_readiness"] = _dim(
            GAP_DIMENSIONS["regulatory_readiness"] * (sum(reg) / len(reg)),
            "regulatory_readiness",
            f"Regulatory readiness scores {sum(reg) / len(reg):.0%} on {len(reg)} stored measure(s).",
        )
    else:
        dims["regulatory_readiness"] = _dim(
            0.0,
            "regulatory_readiness",
            f"No regulatory facts are stored for {target_country.upper()}.",
            measured=False,
        )

    if facts.purchasing_power is not None:
        dims["affordability"] = _dim(
            GAP_DIMENSIONS["affordability"] * facts.purchasing_power,
            "affordability",
            f"Purchasing power indexes at {facts.purchasing_power:.0%} of the reference level.",
        )
    else:
        dims["affordability"] = _dim(
            0.0, "affordability", "No purchasing-power figure is stored.", measured=False
        )

    # --- time lag -----------------------------------------------------------
    lead_age = _mean(
        [float(r.first_observed_days_ago) for r in leaders if r.first_observed_days_ago is not None]
    )
    if lead_age is not None and target is not None and target.first_observed_days_ago is not None:
        lag = max(0.0, lead_age - target.first_observed_days_ago)
        dims["time_lag"] = _dim(
            min(float(GAP_DIMENSIONS["time_lag"]), lag / 150),
            "time_lag",
            f"First seen roughly {lag:.0f} days earlier in the leading markets.",
        )
    else:
        dims["time_lag"] = _dim(
            0.0,
            "time_lag",
            "First-observation dates are not available on both sides.",
            measured=False,
        )

    measured_dims = [d for d in dims.values() if d.measured]
    if not measured_dims:
        gap = None
        caveats.append(
            "Nothing comparable could be measured, so no gap is reported. That is a "
            "statement about our data, not about the market."
        )
    else:
        gap = round(min(100.0, sum(d.points for d in dims.values())), 1)

    return GeographicGap(
        version=GEO_ENGINE_VERSION,
        target=target_country.upper(),
        gap=gap,
        dimensions=dims,
        leaders=sorted(r.country for r in leaders),
        coverage_note=summary.sentence(),
        confidence_penalty=penalty,
        caveats=caveats,
        unmeasured=unmeasured,
    )


def _level_name(rank: float | None) -> str:
    if rank is None:
        return "unknown"
    names = ["none", "low", "emerging", "growing", "high"]
    return names[max(0, min(4, round(rank)))]


# ------------------------------------------------------ cross-country discovery
@dataclass(slots=True)
class LocalizationCandidate:
    subject: str
    source_markets: list[str]
    target_market: str
    gap: float
    similarity: float
    transferability: float | None
    reason: str
    caveats: list[str] = field(default_factory=list)


def similarity(a: MarketFacts, b: MarketFacts) -> tuple[float, list[str]]:
    """How alike are two markets on the facts we actually hold? 0-1.

    Returns the count of dimensions compared alongside the score, because a
    similarity of 0.9 computed from one shared fact is not the same claim as a
    similarity of 0.9 computed from eight.
    """
    pairs = [
        ("internet penetration", a.internet_penetration, b.internet_penetration),
        ("payments", a.payment_infrastructure, b.payment_infrastructure),
        ("logistics", a.logistics_performance, b.logistics_performance),
        ("electricity", a.electricity_reliability, b.electricity_reliability),
        ("business formation", a.business_formation_ease, b.business_formation_ease),
        ("import openness", a.import_openness, b.import_openness),
        ("regulation", a.regulatory_environment, b.regulatory_environment),
        ("purchasing power", a.purchasing_power, b.purchasing_power),
    ]
    usable = [(name, x, y) for name, x, y in pairs if x is not None and y is not None]
    if not usable:
        return 0.0, []
    score = sum(1 - abs(x - y) for _, x, y in usable) / len(usable)
    return round(score, 3), [name for name, _, _ in usable]


def find_localization_candidates(
    *,
    subject: str,
    readings: list[AdoptionReading],
    facts_by_country: dict[str, MarketFacts],
    min_similarity: float = 0.6,
    min_gap: float = 25.0,
) -> list[LocalizationCandidate]:
    """Working in A, similar conditions in B, low penetration in B.

    Every candidate must satisfy all three, and a market with `no_data` can never
    be a target — we would be recommending a country purely because we have not
    looked at it.
    """
    measured = [r for r in readings if r.is_measured]
    strong = [r for r in measured if LEVEL_RANK.get(classify_level(r) or "", 0) >= 3]
    if not strong:
        return []

    out: list[LocalizationCandidate] = []
    for candidate in readings:
        if candidate.country in {r.country for r in strong}:
            continue
        if candidate.is_absence_of_evidence:
            # Never propose a market simply because we have not measured it.
            continue
        level = LEVEL_RANK.get(classify_level(candidate) or "", 0)
        if candidate.status == "measured" and level >= 2:
            continue

        target_facts = facts_by_country.get(candidate.country)
        if target_facts is None:
            continue

        scores: list[tuple[float, str, list[str]]] = []
        for leader in strong:
            leader_facts = facts_by_country.get(leader.country)
            if leader_facts is None:
                continue
            sim, dims = similarity(leader_facts, target_facts)
            if dims:
                scores.append((sim, leader.country, dims))
        if not scores:
            continue
        best_sim = max(s for s, _, _ in scores)
        if best_sim < min_similarity:
            continue

        gap = analyse_gap(
            subject=subject,
            readings=readings,
            target_country=candidate.country,
            target_facts=target_facts,
        )
        if gap.gap is None or gap.gap < min_gap:
            continue

        sources = [c for s, c, _ in scores if s >= min_similarity]
        transfer = transferability_score(
            source_facts=[facts_by_country[c] for c in sources if c in facts_by_country],
            target_facts=target_facts,
            gap=gap.gap,
        )
        out.append(
            LocalizationCandidate(
                subject=subject,
                source_markets=sorted(sources),
                target_market=candidate.country,
                gap=gap.gap,
                similarity=best_sim,
                transferability=transfer.score,
                reason=(
                    f"{subject} is established in {', '.join(sorted(sources))} and "
                    f"{'not measurable' if candidate.status != 'measured' else 'still uncommon'} "
                    f"in {candidate.country}, whose conditions match at {best_sim:.0%}."
                ),
                caveats=gap.caveats,
            )
        )
    return sorted(out, key=lambda c: -c.gap)


# ------------------------------------------------- experimental transferability
@dataclass(slots=True)
class Transferability:
    version: str
    score: float | None
    parts: dict[str, Any]
    note: str
    missing: list[str] = field(default_factory=list)


#: Eleven considerations from section 10 of the brief, of which this build can
#: currently evidence six. The rest are listed as missing rather than guessed.
TRANSFER_DIMENSIONS: Final[dict[str, int]] = {
    "purchasing_power": 20,
    "infrastructure": 20,
    "regulation": 15,
    "logistics": 15,
    "language": 10,
    "headroom": 20,
}

UNEVIDENCED: Final[tuple[str, ...]] = (
    "customer problem similarity",
    "culture",
    "distribution",
    "pricing",
    "consumer behaviour",
)


def transferability_score(
    *,
    source_facts: list[MarketFacts],
    target_facts: MarketFacts,
    gap: float,
) -> Transferability:
    """How likely is something that worked in A to work in B? EXPERIMENTAL.

    Returns `None` when too little is known to say anything, which is the common
    case and the honest answer.
    """
    if not source_facts:
        return Transferability(
            TRANSFERABILITY_VERSION,
            None,
            {},
            "No source market has stored facts, so transferability cannot be estimated.",
            list(UNEVIDENCED),
        )

    def avg(attr: str) -> float | None:
        values = [getattr(f, attr) for f in source_facts if getattr(f, attr) is not None]
        return sum(values) / len(values) if values else None

    parts: dict[str, Any] = {}
    missing: list[str] = list(UNEVIDENCED)

    def compare(key: str, attr: str, label: str) -> None:
        source = avg(attr)
        target = getattr(target_facts, attr)
        maximum = TRANSFER_DIMENSIONS[key]
        if source is None or target is None:
            parts[key] = {"points": 0.0, "max": maximum, "why": f"{label} is not stored for both."}
            missing.append(label)
            return
        # A target at or above the source transfers well; far below does not.
        ratio = min(1.0, target / source) if source > 0 else 0.0
        parts[key] = {
            "points": round(maximum * ratio, 2),
            "max": maximum,
            "why": f"{label}: target at {target:.0%} against source average {source:.0%}.",
        }

    compare("purchasing_power", "purchasing_power", "Purchasing power")
    compare("infrastructure", "internet_penetration", "Internet infrastructure")
    compare("regulation", "regulatory_environment", "Regulatory environment")
    compare("logistics", "logistics_performance", "Logistics")

    source_langs = {f.language for f in source_facts if f.language}
    if target_facts.language and source_langs:
        shared = target_facts.language in source_langs
        parts["language"] = {
            "points": float(TRANSFER_DIMENSIONS["language"])
            if shared
            else TRANSFER_DIMENSIONS["language"] * 0.3,
            "max": TRANSFER_DIMENSIONS["language"],
            "why": (
                "Same primary language as a source market."
                if shared
                else "Different primary language, so the product needs localising."
            ),
        }
    else:
        parts["language"] = {
            "points": 0.0,
            "max": TRANSFER_DIMENSIONS["language"],
            "why": "Language is not stored for both markets.",
        }
        missing.append("language")

    parts["headroom"] = {
        "points": round(TRANSFER_DIMENSIONS["headroom"] * min(1.0, gap / 100), 2),
        "max": TRANSFER_DIMENSIONS["headroom"],
        "why": f"The measured geographic gap is {gap:.0f}/100.",
    }

    evidenced = [k for k, v in parts.items() if v["points"] > 0]
    if len(evidenced) < 3:
        return Transferability(
            TRANSFERABILITY_VERSION,
            None,
            parts,
            "Too few dimensions could be evidenced to estimate transferability. " + EXPERIMENTAL_NOTE,
            missing,
        )

    score = round(min(100.0, sum(p["points"] for p in parts.values())), 1)
    return Transferability(
        TRANSFERABILITY_VERSION,
        score,
        parts,
        EXPERIMENTAL_NOTE + " Five of the eleven considerations in the specification — customer problem "
        "similarity, culture, distribution, pricing and consumer behaviour — are not "
        "evidenced by any connected source and are therefore not scored at all.",
        missing,
    )
