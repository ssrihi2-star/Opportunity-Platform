# Opportunity Methodology (Phase 4)

Phase 3 answers *is something changing?* Phase 4 answers a harder question:
*given that it is changing, is there a realistic way to benefit, why might it
matter, and what would prove us wrong?*

Nothing in this phase tells anyone to buy, invest, import or start a business.
The most confident label the system can reach is `strong_evidence`, which means
"go and check this yourself".

> **Every candidate carries a validation label.** Until the live-data gate has
> been run against the real internet, everything generated here is `DEMO` or
> `UNVALIDATED`, and the UI says so on every card. See §11.

Implementation:

| Concern | Module |
|---|---|
| Versioned thresholds and weights | `backend/app/analytics/opportunity_config.py` |
| Gate, score, penalties, confidence, lifecycle, relevance | `backend/app/analytics/opportunity_scoring.py` |
| Business / import / equity / crypto analyzers | `backend/app/analytics/analyzers.py` |
| The skeptic pass | `backend/app/analytics/skeptic.py` |
| Risk across eleven categories | `backend/app/analytics/risk_engine.py` |
| Geographic gap, adoption-to-attention | `backend/app/analytics/experimental.py` |
| Orchestration | `backend/app/services/opportunities.py` |
| Report and grounded narration | `backend/app/ai/opportunity_report.py` |
| Live-data validation harness | `backend/scripts/live_validation.py` |

Versions stamped on every stored evaluation: `OPPORTUNITY_VERSION`,
`RISK_VERSION`, `SKEPTIC_PROMPT_VERSION`, `REPORT_PROMPT_VERSION`,
`GEO_GAP_VERSION`, `ADOPTION_ATTENTION_VERSION`. Historical scores are never
silently recomputed.

---

## 1. The most important rule: a trend is not an opportunity

"AI coding agents are accelerating" is a trend. It might imply a business to
build, a company to research, hardware to import, or a skill to learn — **or
nothing at all**.

`generate_opportunities` routinely produces nothing from a perfectly real trend
and records why. In the demo build, 22 trends produce 8 candidates and 17
refusals. Those refusals are shown to the user with their reasons, because
"this is real and there is still nothing here for you" is a useful answer.

## 2. Four ways of acting

| Type | Judged on |
|---|---|
| `business` | a problem, a customer, evidenced pain, willingness to pay, defensibility |
| `import_distribution` | trade flow, local availability, suppliers, shipping economics, margin |
| `public_investment` | business quality **and, separately**, price |
| `crypto` | real usage, token distribution, liquidity, audits, identifiable team |

A crypto asset is examined *only* as a crypto opportunity. Re-labelling one as a
"business" is how a risk floor gets dodged, so the router refuses to do it.

## 3. The gate

A candidate is only created when **all** of these hold:

| Requirement | Default |
|---|---|
| Distinct signal **types** | ≥ 3 |
| Distinct signal **classes** | ≥ 2 |
| Independent source families | ≥ 2 |
| Trend score / confidence | ≥ 30 / ≥ 35 |
| Observations / history | ≥ 20 / ≥ 45 days |
| Missing-data share | ≤ 35% |
| Proxy share | ≤ 75%, and at least one non-proxy class |
| Trend state | `active`, `confirmed` or `weakening` |
| Trend flags | not a one-day spike, not seasonal |

Three news outlets are **one** signal type. Three attention metrics are **one**
class. This is the rule that stops a pile of press coverage from looking like
corroboration.

Two further floors apply after scoring:

* **Confidence ≥ 30** after the skeptic pass.
* **Score ≥ 22**, with one deliberate exception: when the underlying trend is
  strong (≥ 40) *and* the analyzer actually had the evidence to judge (≥ 50%
  completeness), a low-scoring candidate is kept and flagged. "This is real and
  there is nothing good here" is a finding; hiding it would let a reader assume
  the trend implies the chance.
* **No accessible way to take part** → refused outright. A trend nobody in this
  position can act on is a fact about the world, not an opportunity.

## 4. Opportunity Score (0–100)

| Component | Max | Notes |
|---|---|---|
| Real adoption | 20 | users, customers, transactions, imports, developer activity. **Attention scores zero here.** |
| Market potential | 15 | banded from evidence; **0 when unknown**, never estimated |
| Evidence diversity | 15 | distinct non-proxy classes, independent families, mean reliability |
| Early entry | 15 | low local penetration, few competitors, low awareness, early stage |
| Accessibility | 10 | capital against the configured ceiling, technical difficulty, barriers |
| Defensibility | 10 | evidenced advantages only |
| Catalyst | 10 | full marks only for a dated catalyst linked to stored evidence |
| Timing | 5 | how early the underlying trend is |

Twenty penalties are available (extreme valuation, attention without adoption,
tiny market, easily copied, single supplier, single customer, concentrated
ownership, anonymous team, promotional manipulation, already mainstream, …),
capped at **65** in total. When the cap bites, a warning says so and the risk
level carries the rest. Nothing is hidden: the UI prints every component with its
maximum and its sentence, every penalty, the pre-penalty subtotal and the total.

## 5. Confidence — a separate question

| Part | Max |
|---|---|
| Evidence volume | 20 |
| Evidence quality | 20 |
| Independent confirmation | 20 |
| Evidence freshness | 15 |
| Source agreement | 15 |
| Real commercial data | 10 |

minus penalties for missing required evidence, assumption-heavy reasoning and
stale evidence.

"Opportunity 82, confidence 52" is a meaningful sentence: *this could be
attractive, and important information is still missing.*

## 6. The skeptic pass

A dedicated agent whose job is to destroy weak ideas. It asks all seventeen
questions from the brief, records which were asked, and outputs the strongest
counterargument, missing evidence, red flags, alternative explanations, a
manipulation probability, reasons it may be too late, and reasons it may be out
of reach.

**It can only reduce confidence.** There is no code path that raises it, and a
parametrised test asserts that property across every input.

Two objections are raised for *every* candidate, because they always apply:

* survivorship bias — the sources record what grew, not what tried and failed;
* the trend can succeed while this particular way of participating fails, because
  the value accrues to someone else in the chain.

## 7. Risk — dominated, not averaged

Eleven categories: market, execution, financial, regulatory, competition,
liquidity, fraud/manipulation, supply chain, geographic, technology, customer
concentration. Each risk carries severity, confidence, evidence, an explanation
and a mitigation where one honestly exists.

The overall level is **not** an average:

* any **blocking** risk → `very_high`;
* three or more high-severity risks → `very_high`;
* any high-severity risk → `high`;
* otherwise moderate or low.

Averaging is how a fatal flaw gets diluted by nine comfortable ones.

**Crypto can never be classified low risk.** Not as a default evidence could
overturn — as a floor, applied after the calculation and reported in the output.

## 8. Thesis, counter-thesis, and the two condition sets

Every candidate stores a thesis and a counter-thesis, both shown side by side.

Every candidate also stores, as rows rather than prose so a later phase can check
them mechanically:

* **confirmation conditions** — what would make the case stronger;
* **invalidation conditions** — what would tell us we were wrong.

The second set is the more important one, and no candidate is stored without it.

## 9. Personal relevance

A separate 0–100 score: geography (25), type priority (20), capital fit (15),
industry experience (10), supplier access (8), distribution access (8), technical
fit (7), regulatory access (7).

It is computed from **stored preferences**. Nothing about any particular person
or country is in the domain model — change the row, and the same candidate scores
differently. A test asserts exactly that.

## 10. Two experimental measures

Both are displayed on their own and contribute **nothing** to the Opportunity
Score until Phase 6 can say whether they predicted anything.

* **Geographic Opportunity Gap** — adoption, attention, supply, competitor and
  time-lag differences between leading markets and the target one. Prices are
  compared only when the currencies match, because this system never converts.
  A gap is a question, not an answer: local demand, affordability, regulation and
  shipping economics all still have to hold. Where the target market is simply
  unmeasured, the output says so rather than treating absence of data as absence
  of demand.
* **Adoption-to-Attention Ratio** — is real use growing faster than the talk?
  Developer activity +120% against media +15% is a more interesting shape than
  media +500% against use +5%.

## 11. Validation status

Every opportunity carries one of:

| Status | Meaning |
|---|---|
| `demo` | generated scenario data |
| `unvalidated` | real adapters, but replayed fixtures only |
| `live_validated` | collected from the live internet |

**At the time of writing, every candidate in this build is `demo`**, because the
build environment has no outbound network access. `scripts/live_validation.py`
is the harness that changes that; see `docs/live-validation.md`.

## 12. Where the model is allowed to speak

The report is assembled deterministically from stored rows and is complete with no
model configured at all. A model may optionally re-phrase three sections. Its
output is checked against a closed fact set at a citation density of 0.9, and
scanned for forbidden phrasing (`guaranteed`, `buy now`, `100x`, `next bitcoin`,
`risk-free`, …). A draft that fails either check is **dropped, not repaired**.

The model cannot change a score, a risk level, a condition, a status or a number.

## 13. Worked results from the demo scenarios

Reproduce with `python -m scripts.seed` (PostgreSQL, opportunity formula 1.0.0):

| Scenario | Trend | Opportunity | Confidence | Risk | Relevance | Verdict |
|---|---|---|---|---|---|---|
| A — edge inference tooling (business) | 45 | **56** | 69 | high | 54 | promising |
| D — solar water pumps (import into LY) | 53 | **39** | 77 | high | 60 | watchlist |
| C — ThermaCore Industries (equity) | 44 | **13** | 54 | very high | 50 | candidate |
| B — quantum wellness devices (business) | 45 | **11** | 42 | high | 65 | candidate |
| F — Luna9 Token (crypto) | 60 | **0** | 31 | very high | 50 | candidate |
| E — EUV lithography capacity | 48 | — | — | — | — | **refused: no accessible way to take part** |

Read C and F carefully.

**C** is the case the brief calls "extremely important": the industry trend is
real and the equity is not. It reaches 48 raw points — the market is large, the
evidence is diverse — and then loses 35 to extreme valuation (−15), poor
economics (−10) and customer concentration (−10), landing at 13 with very high
risk. Being right about the industry and wrong about the company loses money just
as effectively as being wrong about both.

**F** clears the *trend* layer on real, non-proxy, adoption-class on-chain
numbers — which is what makes it a hard case rather than an easy one — and is
then rejected on what it *is*: 78% of supply in ten wallets, 41% held by insiders,
$90k of liquidity, no audit, no identifiable team, 61% bot activity, paid
promotion, and returns that depend on new buyers. Nine automatic flags, a
blocking fraud risk, a skeptic status of `possible_manipulation`, and a score of
zero.
