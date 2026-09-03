# Scoring Methodology

The scoring engine is **pure, deterministic and versioned**. Given the same inputs
it always produces the same output. No LLM participates in producing a number. The
formula version is stored on every `opportunity_scores` row so old scores remain
reproducible after the formula changes.

Implementation: `backend/app/analytics/scoring.py`
Config: `backend/app/analytics/scoring_config.py`
Tests: `backend/tests/test_scoring.py` + golden vectors in `tests/data/scoring_vectors.json`

## 1. Eligibility gate (before any score exists)

| Gate | Default | Setting |
|---|---|---|
| Distinct supporting signal types | >= 3 | `MIN_SIGNAL_TYPES` |
| Distinct independent sources | >= 2 | `MIN_INDEPENDENT_SOURCES` |
| Aggregate confidence | >= 0.50 | `MIN_CONFIDENCE` |
| Newest evidence age | <= 30 days | `MAX_EVIDENCE_AGE_DAYS` |
| Maturity stage | not `mature` / `declining` | — |
| Blocking risk flags absent | see below | — |

Blocking flags: `confirmed_fraud`, `honeypot_behaviour`, `delisted`, `sanctions_exposure`.

Two sources count as independent only if they do not share a `source_group`. This
matters in practice: the two demo generators use different groups on purpose, and
three news outlets syndicating one wire story should share one group.

## 2. Component scores (raw, 0–100)

| Component | Max | Deterministic input |
|---|---|---|
| Real adoption growth | 20 | median pct-change of adoption-class signals; 0 if none — attention alone does not count |
| Market size | 15 | banded from evidence; **0 if unknown**, never estimated |
| Signal acceleration | 15 | growth-of-growth in percentage points, capped at 40pp |
| Independent source confirmation | 15 | `min(1, (n-1)/3)` weighted by mean source reliability; 0 for a single source |
| Entry attractiveness | 10 | accessibility band, scaled by headroom against the user's capital cap; 0 if it exceeds the cap |
| Defensibility | 10 | evidenced moat markers (patent, network effect, switching cost, exclusivity, licence, proprietary data) |
| Clear catalyst | 10 | full marks only when the dated catalyst is linked to a stored evidence item; 3 if merely claimed |
| Accessibility and liquidity | 5 | tradability / importability / buildability |

Each component returns `(points, rationale)`. The rationale is shown in the UI next
to the number. Nothing is hidden.

## 3. Penalties

| Penalty | Points | Trigger |
|---|---|---|
| Manipulation risk | up to -25 | scaled by manipulation probability |
| Anonymous team | -10 | `anonymous_founders` |
| Concentrated ownership | -10 | `concentrated_ownership` |
| Thin liquidity | -10 | `thin_liquidity` |
| Extreme valuation | -10 | `extreme_valuation` |
| No working product | -10 | `no_working_product` |
| Paid-promotion dependence | -10 | `paid_influencer_promotion` |
| Weak evidence | -10 | evidence completeness < 0.4 |
| Regulatory danger | -10 | enforcement / ban risk / single-regulation dependence |
| Single-source dependence | -8 | one source > 70% of weight, or only one source |
| Unsustainable growth | -8 | one-off event or incentive-driven |
| High competition | -6 | dominant incumbent or existing local competitor |
| Operational difficulty | -6 | certification, heavy logistics, licensing |

`adjusted_score = clamp(raw_score - min(total_penalties, 60), 0, 100)`

Penalties are capped at 60 so a genuinely strong opportunity is not driven to zero
by many small flags; when the cap bites, a note says so and the risk level carries
the rest.

## 4. Confidence and evidence completeness

```
confidence = 0.40 * mean_source_reliability
           + 0.25 * evidence_completeness
           + 0.20 * cross_source_agreement
           + 0.15 * recency_factor
```

`evidence_completeness = satisfied_required_fields / total_required_fields`, where
the required set differs per category (`scoring_config.REQUIRED_EVIDENCE`). Missing
fields are named explicitly in the report as "Insufficient evidence".

The skeptic agent may only **reduce** confidence, never increase it.

## 5. Risk level

Derived, not scored:

| Level | Rule |
|---|---|
| `low` | penalties <= 5, confidence >= 0.75, no medium+ flags. **Never available to crypto.** |
| `medium` | penalties <= 20 and no high-severity flag |
| `high` | any high-severity flag, or penalties <= 40 |
| `very_high` | manipulation probability > 0.5, or a blocking flag |

## 6. Golden vectors (from `tests/data/scoring_vectors.json`)

| Case | Raw | Penalties | Adjusted | Confidence | Risk |
|---|---|---|---|---|---|
| strong_technology | 72.92 | 0 | 72.92 | 0.88 | low |
| solid_business | 39.09 | 6 | 33.09 | 0.70 | medium |
| thin_evidence_import | 11.04 | 18 | 0.00 | 0.35 | medium |
| hyped_crypto | 49.72 | 55.5 | 0.00 | 0.41 | very_high |

The crypto case is the one to read carefully: 400% adoption growth and 90pp
acceleration still produce an adjusted score of zero, because manipulation
probability, an anonymous team and thin liquidity together exceed the raw score.
That is the intended behaviour, and the golden vector locks it in — changing the
formula requires bumping `FORMULA_VERSION` and regenerating the file in the same
commit, which the test enforces.

## 7. What the score is not

The score is **not** a prediction of return, and the product never converts a score
into a buy instruction. It is a ranking of *how much this deserves your research
time*, given the evidence currently in the database.
