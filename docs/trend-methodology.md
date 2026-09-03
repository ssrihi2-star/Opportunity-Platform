# Trend Methodology (Phase 3)

This document covers how the system decides that *something is changing*. It is a
separate question from whether that change is an opportunity, which is Phase 4 and
does not exist yet. Nothing in this pipeline emits a buy, import or start-a-business
instruction, and no language model participates in producing any number on this page.

Implementation:

| Concern | Module |
|---|---|
| Growth, momentum, acceleration, persistence, spikes, seasonality | `backend/app/analytics/growth.py` |
| Independent-vs-syndicated confirmation | `backend/app/analytics/confirmation.py` |
| Near-duplicate headline detection | `backend/app/analytics/dedup.py` |
| Trend Score, Confidence, stage, lifecycle | `backend/app/analytics/trend_scoring.py` |
| Series loading and aggregation across sources | `backend/app/services/trends.py` |
| Deterministic topic clustering | `backend/app/services/topics.py` |
| Entity resolution and the review queue | `backend/app/services/entities.py` |
| Narration of an already-computed result | `backend/app/ai/trend_explainer.py` |

`TREND_FORMULA_VERSION` is `1.0.0` and is stamped on every `trend_snapshots` row,
so a score computed today stays reproducible after the formula changes.

---

## 1. Entity resolution: identifiers, never names

Two records are joined into one entity only in this order:

1. **A shared official identifier.** SEC CIK, GitHub repository id, ticker plus
   exchange, ISO country code, contract address plus chain, HS code, Wikipedia
   page id. A composite identifier is stored composite (`chain_address` is
   `ethereum:0xabc…`), so the same address on two chains never collides.
2. **An exact alias** already recorded for that entity.
3. **A decision a human already made** — confirmed, rejected, or kept separate.

If none of those apply, a **new entity is created**, and if the name resembles an
existing one (similarity ≥ 0.6) a row is written to `entity_match_candidates` for a
person to decide. There is deliberately no "merge automatically above X%" threshold.
`Apple` and `Apple Bank` share 60% of their name and nothing else; the system says so
and waits.

Every decision is stored permanently with who made it, when, and any note. Re-posting
a decision to an already-decided candidate returns HTTP 409 rather than overwriting it.

## 2. Topics: clustering first, naming second

Topics are built by union-find over distinctive tokens shared between entity names,
plus co-occurrence within the same source runs. A token that appears in more than a
fixed share of the corpus is ignored as boilerplate. Two-character tokens are kept
deliberately, because `AI`, `EV` and `5G` are the whole point.

A language model may be asked to *name or describe* a cluster the algorithm already
found. It cannot create, merge or split one. `topics.label_is_ai_generated` records
which labels were model-written.

## 3. Time series: a gap is not a zero

`signal_observations.value` is nullable. Each row carries a `status`:

| Status | Meaning |
|---|---|
| `ok` | a real measurement |
| `missing` | the source reported no value for that period (FRED's `"."`, an unpublished month) |
| `failed` | the fetch itself failed |

A gap is stored as `value = NULL`, never `0.0`. Growth maths skips gaps, and their
count is shown on the trend page and penalised in both scores. Storing a failed fetch
as zero would manufacture a crash that never happened.

Money carries `amount` **and** `currency`. Values are stored in the currency the source
published. Nothing is silently converted to USD, and no original figure is overwritten
by a converted one.

## 4. Deterministic measurements

Over each series (NumPy / pure Python, no model):

* **Windowed growth** — 7, 30, 90 and 365 days, comparing the mean of the window
  against the mean of the window before it. Returns `None`, not `0`, when history is
  too short to answer.
* **Momentum** — slope of a least-squares regression over the recent window.
* **Acceleration** — recent growth minus earlier growth, in percentage points.
* **Persistence** — the share of consecutive blocks that moved in the same direction.
  A rise that happens in one jump and a rise that happens in twenty steps score
  differently.
* **Spike detection** — see below.
* **Seasonality** — a monthly index plus a year-on-year comparison, and only over at
  least two full cycles (`MIN_DAYS_FOR_SEASONALITY = 400`). With less history the
  system says it cannot tell, rather than guessing.

### Spike vs genuine acceleration

The trap: `10, 11, 9, 300, 12`. Naively that is +2900% growth.

A point is only called a one-day spike when **all** of the following hold:

| Test | Threshold |
|---|---|
| Peak against the series median | ≥ 5× |
| Peak against its **immediate neighbours** | ≥ 5× |
| Width of the elevated region | ≤ 2 points |
| Either the series reverted afterwards, or the peak is the newest point | — |

The neighbour test is the one that matters. Without it every genuinely accelerating
series is flagged, because its maximum is always the newest point: `700 → 1200` is
growth, `9 → 300` is a spike.

## 5. Independent confirmation

Sources are grouped by `source_group`. Five outlets republishing one wire story share
a group and count once. Beyond that, `confirmation.py` measures:

* distinct independent source groups;
* distinct signal **types** and signal **classes** (developer activity and media
  coverage are different kinds of evidence; two attention metrics are not);
* the share of total weight carried by the single dominant source;
* the share carried by proxy measurements.

Near-duplicate articles are found by an order-independent fingerprint over distinctive
title tokens, then merged by Jaccard similarity ≥ 0.7, so *"ZephyrCoin surges as traders
pile in — Reuters"* and *"Traders pile in as ZephyrCoin surges, say analysts | Bloomberg"*
are recognised as one story. Only records that carry a URL are counted, so
machine-generated series names cannot dilute the measurement.

## 6. Trend Score (0–100)

Components, each returning points **and the sentence that explains them**:

| Component | Max |
|---|---|
| Growth | 20 |
| Acceleration | 18 |
| Persistence | 15 |
| Source diversity | 18 |
| Scale | 12 |
| Novelty (against the previous peak) | 10 |
| Geographic spread | 7 |
| **Total** | **100** |

Penalties:

| Penalty | Points | Trigger |
|---|---|---|
| One-day spike | −25 | the spike test above |
| Tiny baseline | −15 | small starting level **and** small absolute move; half when only the baseline is small |
| Seasonality | −15 | the same rise happens every year |
| Duplicate information | −12 | high share of republished stories |
| Insufficient history | −12 | too few days to judge |
| Low-quality sources | −10 | low mean source reliability |
| Proxy only | −10 | nothing but proxy measurements |
| Single source | −10 | one independent source |
| Missing data | −8 | many gaps in the series |

`trend_score = clamp(raw − min(total_penalties, 60), 0, 100)`.

Penalties are capped at 60 so a strong trend is not zeroed by a pile of small flags;
when the cap bites, a warning says so and the confidence score carries the rest.

The `/trends/{id}` page prints this whole table — every component, its maximum, its
rationale, every penalty, the pre-penalty subtotal and the final number. The
calculation is never hidden behind the total.

## 7. Confidence Score (0–100) — a separate question

Confidence answers *how much can this judgement be relied on*, not *how strong is the
movement*. A score of 90 at confidence 40 is a meaningful and honest statement.

| Part | Max |
|---|---|
| History length | 25 |
| Observation count | 20 |
| Source quality | 20 |
| Independent sources | 20 |
| Method agreement | 15 |

minus penalties for missing data and for stale evidence.

## 8. Stage — a deterministic ladder

Seven stages, decided by arithmetic. No model participates.

| Stage | Rule (simplified) |
|---|---|
| `declining` | direction falling, or growth < −10% |
| `weak_signal` | fewer than 8 observations, or under 21 days, or a single source, or a one-day spike |
| `accelerating` | acceleration > 15pp, growth > 25%, persistence ≥ 0.5 |
| `mainstream` | large level (≥ 10,000) still growing ≥ 5% |
| `mature` | large level, essentially flat (|growth| < 5%) |
| `early_adoption` | growth ≥ 15% with persistence ≥ 0.5 |
| `emerging` | movement present but below the above |

## 9. Lifecycle — the same trend, updated

A trend is a row that is followed over time. `evaluate_trends` updates the existing
row and appends a `trend_snapshots` entry; it does not create a new discovery each day.

| State | Entered when |
|---|---|
| `candidate` | detected, but not yet corroborated |
| `active` | score ≥ 35, confidence ≥ 25, **and ≥ 2 independent sources** |
| `confirmed` | score ≥ 60, confidence ≥ 55, ≥ 2 independent sources |
| `weakening` | score has fallen ≥ 20 points from its peak |
| `ended` | no new observation for 60 days |
| `invalidated` | it was promoted, then turned out to be a spike or a season |

Two rules do most of the work here:

* A single source can produce a high score but never reaches `active`. Calling one
  source "active" would dress one opinion up as corroboration.
* Spikes and seasonal patterns never rise above `candidate`. Both are real patterns;
  neither is a trend.

## 10. Where the model is allowed to speak

`POST /trends/{id}/explain` builds a **closed fact set** from the stored numbers, wraps
it in the untrusted-content envelope from Phase 1, and asks for plain-English prose. The
result goes through the Phase 1 grounding verifier at a minimum citation density of 0.9.
An explanation that fails verification is **dropped, not repaired** — the page then shows
the numbers with no prose rather than prose that might be invented.

The model cannot change a score, a stage, a state, or a fact.

## 11. Worked results from the demo scenarios

Reproduce with `python -m scripts.seed` (PostgreSQL, formula 1.0.0):

| Scenario | Score | Confidence | Stage | State | What it demonstrates |
|---|---|---|---|---|---|
| AI coding agents | 67 | 87 | accelerating | confirmed | genuine multi-source acceleration |
| AI Agents (topic) | 63 | 92 | early adoption | confirmed | clustering across 8 sources |
| mycelium packaging | 43 | 84 | emerging | active | steady, unspectacular real growth |
| ceramic floor tiles | 36 | 80 | mature | candidate | seasonality caught and penalised |
| DVD authoring software | 14 | 67 | declining | weakening | decline detected, not ignored |
| ZephyrCoin | **0** | 70 | weak signal | candidate | hype rejected |

ZephyrCoin is the case to read. Its measured 30-day growth is **+197.7%** and its
acceleration is +178pp — both real numbers, both scoring near the component maximum,
for a raw subtotal of 57. It then takes tiny baseline (−15), one-day spike (−25),
low-quality sources (−10) and duplicate information (−12): 62 points of penalty, capped
at 60, giving **0**. Confidence stays at 70, because the data itself is complete — we are
confident that this is not a trend.
