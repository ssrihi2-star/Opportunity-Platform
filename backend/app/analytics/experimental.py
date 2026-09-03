"""Two experimental measures, shown separately and NOT folded into any score.

Both are ideas that sound right and have not been backtested. They are displayed
next to the Opportunity Score with an EXPERIMENTAL label, and neither contributes
a single point to it. That stays true until Phase 6 can say whether they actually
predicted anything.

1. **Geographic Opportunity Gap** — something is normal in one country and still
   rare in another. That *may* indicate an import, distribution or localisation
   opportunity. It may equally indicate that the second country cannot afford it,
   does not want it, or forbids it — which is why this is a gap, not a verdict.

2. **Adoption-to-Attention Ratio** — is real use growing faster than the talk?
   Developer activity +120% against media +15% is a more interesting shape than
   media +500% against use +5%.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

from app.analytics.opportunity_config import (
    ADOPTION_ATTENTION_VERSION,
    GEO_GAP_VERSION,
)
from app.analytics.opportunity_scoring import EvidenceFact

EXPERIMENTAL_NOTE = (
    "Experimental measure. It is displayed on its own and contributes nothing to the "
    "Opportunity Score until it has been backtested."
)


# ------------------------------------------------------- geographic opportunity
@dataclass(slots=True)
class GeoObservation:
    """Activity for one subject in one place."""

    geo: str
    adoption_growth: float | None = None
    attention_growth: float | None = None
    latest_level: float | None = None
    supplier_count: int | None = None
    competitor_count: int | None = None
    unit_price: float | None = None
    price_currency: str | None = None
    first_observed_days_ago: int | None = None


@dataclass(slots=True)
class GeoGapResult:
    version: str
    gap: float | None
    parts: dict[str, Any] = field(default_factory=dict)
    notes: list[str] = field(default_factory=list)
    caveats: list[str] = field(default_factory=list)


def geographic_gap(*, leaders: list[GeoObservation], target: GeoObservation) -> GeoGapResult:
    """0-100. How far behind the target market is, on evidence that exists.

    Returns `gap = None` when there is nothing to compare, rather than 0, because
    "we cannot tell" and "there is no gap" are different answers.
    """
    parts: dict[str, Any] = {}
    notes: list[str] = []
    caveats: list[str] = []

    usable = [o for o in leaders if o.geo != target.geo]
    if not usable:
        return GeoGapResult(
            version=GEO_GAP_VERSION,
            gap=None,
            notes=["No comparison market has data, so no gap can be measured."],
        )

    def _mean(values: list[float]) -> float | None:
        return sum(values) / len(values) if values else None

    # --- adoption gap: are they using it and we are not? ---------------------
    lead_adoption = _mean([o.adoption_growth for o in usable if o.adoption_growth is not None])
    if lead_adoption is not None:
        tgt = target.adoption_growth
        if tgt is None:
            parts["adoption_gap"] = {
                "points": 30.0,
                "why": (
                    f"Leading markets show {lead_adoption:+.0f}% growth; the target market "
                    "has no adoption measurement at all."
                ),
            }
            caveats.append(
                "The target market's adoption is unmeasured, not proven to be zero. Absence "
                "of data is not absence of demand — nor evidence of it."
            )
        else:
            delta = max(0.0, lead_adoption - tgt)
            parts["adoption_gap"] = {
                "points": round(min(30.0, delta / 4), 2),
                "why": f"Leading markets {lead_adoption:+.0f}% vs target {tgt:+.0f}%.",
            }

    # --- attention gap -------------------------------------------------------
    lead_attention = _mean([o.attention_growth for o in usable if o.attention_growth is not None])
    if lead_attention is not None and target.attention_growth is not None:
        delta = max(0.0, lead_attention - target.attention_growth)
        parts["attention_gap"] = {
            "points": round(min(15.0, delta / 8), 2),
            "why": (
                f"Interest is growing {lead_attention:+.0f}% elsewhere against "
                f"{target.attention_growth:+.0f}% locally."
            ),
        }

    # --- supply gap ----------------------------------------------------------
    if target.supplier_count is not None:
        n = target.supplier_count
        pts = 25.0 if n == 0 else 18.0 if n <= 2 else 8.0 if n <= 5 else 0.0
        parts["supply_gap"] = {
            "points": pts,
            "why": f"{n} local supplier(s) identified.",
        }
    else:
        notes.append("Local supplier count is unknown, so the supply gap is not scored.")

    # --- competitor gap ------------------------------------------------------
    lead_comp = _mean([float(o.competitor_count) for o in usable if o.competitor_count is not None])
    if target.competitor_count is not None and lead_comp is not None:
        delta = max(0.0, lead_comp - target.competitor_count)
        parts["competitor_gap"] = {
            "points": round(min(15.0, delta * 1.5), 2),
            "why": (
                f"{lead_comp:.0f} competitors in leading markets against {target.competitor_count} locally."
            ),
        }

    # --- price gap: only where the currency is comparable --------------------
    lead_priced = [o for o in usable if o.unit_price is not None and o.price_currency]
    if lead_priced and target.unit_price is not None and target.price_currency:
        same_currency = [o for o in lead_priced if o.price_currency == target.price_currency]
        if same_currency:
            lead_price = _mean([o.unit_price for o in same_currency]) or 0.0
            if lead_price > 0:
                ratio = target.unit_price / lead_price
                parts["price_gap"] = {
                    "points": round(min(10.0, max(0.0, (ratio - 1) * 20)), 2),
                    "why": (
                        f"Local price is {ratio:.2f}x the price in comparable markets "
                        f"({target.price_currency})."
                    ),
                }
        else:
            caveats.append(
                "Prices are published in different currencies and are not converted here, "
                "so no price gap is claimed."
            )

    # --- time lag ------------------------------------------------------------
    lead_age = _mean(
        [float(o.first_observed_days_ago) for o in usable if o.first_observed_days_ago is not None]
    )
    if lead_age is not None and target.first_observed_days_ago is not None:
        lag = max(0.0, lead_age - target.first_observed_days_ago)
        parts["time_lag"] = {
            "points": round(min(5.0, lag / 100), 2),
            "why": f"First seen about {lag:.0f} days earlier in the leading markets.",
        }

    if not parts:
        return GeoGapResult(
            version=GEO_GAP_VERSION,
            gap=None,
            notes=["Not enough comparable evidence to measure a gap."],
            caveats=caveats,
        )

    total = min(100.0, sum(p["points"] for p in parts.values()))
    caveats.append(
        "A gap is a question, not an answer. Local demand, affordability, regulation and "
        "shipping economics all have to hold before it means anything."
    )
    return GeoGapResult(
        version=GEO_GAP_VERSION,
        gap=round(total, 2),
        parts=parts,
        notes=notes or [EXPERIMENTAL_NOTE],
        caveats=caveats,
    )


# ------------------------------------------------------ adoption vs attention
@dataclass(slots=True)
class AdoptionAttentionResult:
    version: str
    ratio: float | None
    adoption_growth: float | None
    attention_growth: float | None
    reading: str
    parts: dict[str, Any] = field(default_factory=dict)


def adoption_to_attention(facts: list[EvidenceFact]) -> AdoptionAttentionResult:
    """Real use over public interest. Above 1.0 means the doing leads the talking."""
    adoption = [f for f in facts if f.is_adoption and not f.is_proxy]
    attention = [f for f in facts if f.signal_class == "attention"]

    def _mean(values: list[float]) -> float | None:
        return sum(values) / len(values) if values else None

    ado = _mean([f.growth_30d for f in adoption if f.growth_30d is not None])
    att = _mean([f.growth_30d for f in attention if f.growth_30d is not None])

    parts = {
        "adoption_signals": [f.signal_type for f in adoption],
        "attention_signals": [f.signal_type for f in attention],
        "note": EXPERIMENTAL_NOTE,
    }

    if ado is None and att is None:
        return AdoptionAttentionResult(
            ADOPTION_ATTENTION_VERSION,
            None,
            None,
            None,
            "Neither adoption nor attention can be measured here.",
            parts,
        )
    if ado is None:
        return AdoptionAttentionResult(
            ADOPTION_ATTENTION_VERSION,
            None,
            None,
            att,
            "Attention is measurable but real use is not measured at all — which is the "
            "least interesting shape this can take.",
            parts,
        )
    if att is None:
        return AdoptionAttentionResult(
            ADOPTION_ATTENTION_VERSION,
            None,
            ado,
            None,
            "Real use is growing with no measurable public attention. That may mean early, "
            "or it may mean nobody has looked.",
            parts,
        )

    # Shift both so a negative growth rate does not invert the ratio's meaning.
    shift = 1.0 + max(0.0, -min(ado, att))
    ratio = (ado + shift) / (att + shift) if (att + shift) != 0 else None

    if ratio is None:
        reading = "The ratio cannot be computed from these numbers."
    elif ratio >= 1.5:
        reading = (
            f"Real use is growing {ado:+.0f}% against {att:+.0f}% attention. The doing is "
            "running ahead of the talking, which is the more interesting shape."
        )
    elif ratio >= 0.9:
        reading = f"Use and attention are growing at similar rates ({ado:+.0f}% vs {att:+.0f}%)."
    else:
        reading = (
            f"Attention is growing {att:+.0f}% while real use grows {ado:+.0f}%. The talk is "
            "ahead of the doing."
        )

    return AdoptionAttentionResult(
        version=ADOPTION_ATTENTION_VERSION,
        ratio=round(ratio, 3) if ratio is not None else None,
        adoption_growth=round(ado, 2),
        attention_growth=round(att, 2),
        reading=reading,
        parts=parts,
    )
