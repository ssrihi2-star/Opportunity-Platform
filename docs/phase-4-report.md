# Phase 4 Report — Turning Trends Into Opportunity Candidates

Written for a non-programmer. Where a technical word is unavoidable, it is
explained the first time it appears.

---

## Read this first

**I could not test the system against the live internet.** The machine this was
built on is behind a network filter that only allows software package downloads.
Every attempt to reach GitHub, Hacker News, Wikipedia, the SEC, FRED and UN
Comtrade was blocked before it left the building.

So everything you see below was produced from **generated demo data**. I have not
hidden this: every opportunity in the database is stamped `DEMO`, every card on
screen carries an orange **DEMO DATA** badge, and every detail page opens with a
banner saying the evidence has not been validated against live sources.

I built you the tool to close that gap — one command, on a machine with internet
access — and section 2 explains exactly how to run it.

---

## 1. What I built, in plain language

Phase 3 answered *is something changing?* Phase 4 answers a much harder question:

> *Given that it is changing — is there a realistic way to benefit, why might it
> matter, and what would prove us wrong?*

It still tells you to buy nothing. The strongest thing the system can say about
anything is **"strong evidence"**, which means *go and check this yourself*.

### The rule everything else is built on

**A trend is not an opportunity.**

"AI coding agents are accelerating" is a fact about the world. It might mean
there's a business to build, a company worth researching, hardware worth
importing, or a skill worth learning — **or it might mean nothing you can act
on at all.**

The system is allowed to say so. In the current demo it looks at 22 trends and
produces **8 candidates and 17 refusals** — and it shows you the refusals with
the reason for each one, because "this is real and there's still nothing here for
you" is a useful answer.

### Four different ways of acting

The same trend gets judged completely differently depending on what you'd
actually do about it:

| Type | Judged on |
|---|---|
| **Business** | is there a problem, a customer, real pain, and will anyone pay? |
| **Import / distribution** | trade flow, local availability, suppliers, shipping cost, margin |
| **Public investment** | is it a good company — and, *separately*, is the price sensible? |
| **Crypto** | real users, who owns the tokens, can you get out, is there an audit, is anyone identifiable |

A crypto asset is only ever examined as crypto. Calling one a "business
opportunity" would be the obvious way to dodge its risk rules, so the system
refuses to do it.

### A gate that mostly says no

Before anything is even considered, the evidence has to clear a bar:

- at least **3 different kinds of measurement** — three news outlets are *one*
  kind, not three;
- at least **2 genuinely different categories of evidence** — developer activity
  and trade volume are two categories; two different press metrics are one;
- at least **2 independent sources** that don't share an owner;
- a trend score of 30+, confidence of 35+, at least 20 measurements over at least
  45 days;
- no more than a third of the data missing, no more than three-quarters of it
  indirect;
- and the trend must not be a one-day spike or a seasonal pattern.

Then two more floors after scoring: confidence must be at least 30, and the score
itself at least 22.

### One deliberate exception, and why it matters

There's one case where a low-scoring candidate is kept anyway: when **the trend is
strong and we had enough evidence to judge it properly**.

Otherwise the system would quietly delete exactly the finding you most need —
"this trend is completely real, and there is still nothing good here". Hiding that
would let you assume the trend implies the opportunity, which is the mistake this
whole product exists to prevent. The ThermaCore example in section 3 is precisely
this case.

### The Opportunity Score (0–100)

Eight ingredients:

| Ingredient | Max | Note |
|---|---|---|
| Real adoption | 20 | actual usage, customers, imports, developer activity. **Attention scores zero here.** |
| Market potential | 15 | banded from evidence — **zero when unknown**, never estimated |
| Evidence diversity | 15 | how many genuinely different kinds of evidence agree |
| Early entry | 15 | low local presence, few competitors, low awareness |
| Accessibility | 10 | can *you* actually do this, given capital and difficulty |
| Defensibility | 10 | evidenced advantages only |
| Catalyst | 10 | full marks only for a dated event linked to stored evidence |
| Timing | 5 | how early the underlying trend is |

Twenty penalties are available — extreme valuation, attention with no adoption,
tiny market, easily copied, one supplier, one customer, concentrated token
ownership, anonymous team, paid promotion, already mainstream, and more. They're
capped at 65 in total so a genuinely good candidate can't be zeroed by a pile of
small complaints; when the cap bites, the page says so.

**The whole calculation is printed on screen.** Every ingredient, its maximum, the
sentence explaining it, every penalty, the subtotal before penalties, and the
final number.

### Confidence and risk are three separate numbers

- **Opportunity Score** — how attractive this looks.
- **Confidence** — how much you can rely on that judgement.
- **Risk** — how badly it could go wrong.

"Opportunity 82, confidence 52" is an honest and useful sentence: *this could be
attractive, and important information is still missing.* One blended number would
hide that.

Risk is measured across eleven categories, and — this matters — **the overall
level is not an average**. A company with excellent margins, a strong position and
an active fraud investigation is not "moderate risk". One fatal finding dominates
nine comfortable ones. Averaging is how a fatal flaw gets diluted away.

**Crypto can never be rated low risk.** Not as a starting assumption that evidence
could overturn — as a floor, applied after the calculation and stated openly in
the output.

### The skeptic

There is a dedicated component whose only job is to attack the idea. It's not
balanced; it's not supposed to be. It asks all seventeen questions from your
brief and records which it asked, then reports the strongest objection, what
evidence is missing, red flags, alternative explanations, an estimate of the
chance this is manipulation, and reasons it might be too late or out of reach.

**It can only lower confidence. There is no code path that raises it**, and a test
checks that property across the entire range of possible inputs.

Two objections are raised for *every* candidate, because they always apply:

1. **Survivorship bias** — the sources record the things that grew. The ones that
   tried the same and failed leave no trail, so the picture flatters by
   construction.
2. **The trend can succeed while this specific opportunity fails** — because the
   money ends up with an incumbent, a supplier or a platform owner instead.

### What would confirm it, and what would prove us wrong

Every candidate stores both, as checkable items rather than paragraphs — so a
later phase can actually test them against arriving data instead of re-reading
prose.

**No candidate is stored without invalidation conditions.** If we can't say what
would prove us wrong, we don't have a thesis; we have a hope.

### Relevance to you, kept separate

A superb global opportunity can be completely useless to a specific person. So
there's a separate 0–100 **Personal Relevance** score built from geography, your
type priorities, capital, industry experience, supplier and distribution access,
technical ability and regulatory reach.

It reads entirely from stored settings. **Nothing about Libya, Tunisia or you is
written into the code.** A test proves it: change the settings row to Brazil and
the same candidate scores differently.

### Two experimental ideas, deliberately kept out of the score

- **Geographic Opportunity Gap** — how far behind your market is, on adoption,
  attention, supply, competitors and time. It compares prices *only* when the
  currencies match, because this system never converts money. And where your
  local market simply hasn't been measured, it says so rather than treating "no
  data" as "no demand".
- **Adoption vs Attention** — is real use growing faster than the talk?

Both sound right. Neither has been tested against reality. So they're shown
beside the score and contribute **nothing** to it, until Phase 6 can say whether
they predicted anything.

### The research note, and what the AI may do

Every candidate has a full research note with thirteen sections. It is assembled
entirely from stored data and is **complete with no AI configured at all**.

An AI may optionally re-phrase three sections more fluently. Before it runs, it's
handed a closed list of facts. Afterwards its output faces two checks: every claim
must point back to a real fact, and the text is scanned for phrases like
"guaranteed", "buy now", "100x", "next Bitcoin", "risk-free".

**A draft that fails either check is thrown away, not corrected.** A report that
had to be fixed is a report that was willing to be wrong.

The AI cannot change a score, a risk level, a condition or any number, anywhere.

---

## 2. Did I test it with live internet data? No — here's the proof and the fix

I tried. Here's what came back:

```
502  https://api.github.com/rate_limit          builtin injection failed (github)
403  http://hn.algolia.com/api/v1/search        Host not in allowlist: hn.algolia.com
000  https://wikimedia.org/api/rest_v1/...      no route
000  https://data.sec.gov/submissions/...       no route
000  https://api.stlouisfed.org/fred/series     no route
000  https://comtradeapi.un.org/public/v1/...   no route
200  https://pypi.org/simple/                   (software packages — allowed)
```

Only software package downloads are permitted. This is a property of the build
environment, not a bug in the code.

**So I wrote you the harness.** On a computer with internet access:

```bash
docker compose up --build -d
docker compose exec api python -m scripts.seed

# see the plan without fetching anything
docker compose exec api python -m scripts.live_validation --dry-run

# add the one credential that's genuinely required (free, instant):
#   FRED api_key   from fred.stlouisfed.org
# and, strongly recommended:
#   GitHub token   a read-only personal access token

docker compose exec api python -m scripts.live_validation
```

It runs the complete path — live source → stored record → entity → measurement →
time series → trend — and checks fourteen things, including:

- does each API still return the shape the code expects;
- does pagination work;
- do the rate limits and request ceilings engage;
- **is a failing source reported clearly rather than silently skipped**;
- **does fetching the same thing twice store zero new records** (it fetches each
  source twice on purpose);
- do entities resolve to one record rather than several;
- are timestamps real, ordered and not in the future;
- **do unreported periods stay missing rather than becoming zero**;
- are the resulting trend numbers sensible rather than absurd.

It prints a pass/fail table and exits with an error code if anything fails, so it
can go into automated testing.

**It never adjusts a threshold to make live data look better.** If the live result
is boring, that's the result. `docs/live-validation.md` has the full checklist,
including which failures to expect on a first run and what each one means.

---

## 3. Example opportunities

All eight below came out of the real engine. I did not write these numbers by
hand. Every one is `DEMO` data.

| # | Opportunity | Type | Trend | **Opp.** | Conf. | Risk | Relevance | Status |
|---|---|---|---|---|---|---|---|---|
| 1 | Edge inference tooling — build or serve | business | 45 | **56** | 69 | high | 54 | promising |
| 2 | Solar water pumps — import into Libya | import | 53 | **39** | 77 | high | 60 | watchlist |
| 3 | Solar water pumps — build a business | business | 53 | **25** | 41 | high | 65 | candidate |
| 4 | Liquid cooling systems — business | business | 55 | **22** | 35 | high | 60 | candidate |
| 5 | Sanitary ware — import distribution | import | 50 | **22** | 36 | high | 83 | candidate |
| 6 | **ThermaCore Industries — equity** | investment | 44 | **13** | 54 | **very high** | 50 | candidate |
| 7 | Quantum wellness devices — business | business | 45 | **11** | 42 | high | 65 | candidate |
| 8 | **Luna9 Token** | crypto | **60** | **0** | 30 | **very high** | 50 | candidate |
| — | **EUV lithography capacity** | — | 48 | **refused** | — | — | — | no accessible way in |

### The one you asked for: high trend, low opportunity

**#6 ThermaCore Industries. Trend 44. Opportunity 13.**

The data-centre liquid cooling market is genuinely growing — capital spending
announcements, import volumes and hiring are all rising, and that industry trend
scores 55 on its own. ThermaCore sells into that market. On paper it looks like
the obvious way to participate.

The system reaches **48 raw points** — large market (15/15), diverse evidence
(11/15), accessible (8/10). Then:

| Penalty | Points | Why |
|---|---|---|
| Extreme valuation | −15 | the price already assumes the good outcome |
| Poor economics | −10 | operating margin −4%, debt-to-equity 3.4, cash flow −$38m |
| Single customer | −10 | 41% of revenue comes from one buyer |

**Final: 13 out of 100. Very high risk.**

And the page says, in words, at the top:

> *The underlying trend scores 44, but this particular way of participating scores
> only 13. It is kept visible because a real trend with no attractive way in is
> worth knowing about.*

The adoption-vs-attention reading for this one is **0.06** — *"Attention is
growing +16% while real use grows −0%. The talk is ahead of the doing."*

That is the entire point of Phase 4 in one screen: **being right about the
industry and wrong about the company loses money just as effectively as being
wrong about both.**

### The one that never became an opportunity at all

**EUV lithography capacity.** Trend score 48 — capital spending, patent filings
and export volumes all rising together. Completely real.

Refused outright:

> *The trend is real, but there is no accessible way to take part: One
> manufacturer worldwide, with a multi-year order book; Export controls restrict
> who may buy; Capital requirement is four orders of magnitude above any stated
> ceiling. Recorded as a trend to watch rather than as an opportunity.*

The machine costs about $400 million and there's one company on Earth that makes
it. That's a fact about the world, not an opportunity, and the system says so
instead of scoring it low and leaving it in the list.

---

## 4. One example of each type you asked for

### IMPORT / DISTRIBUTION — Solar water pumps into Libya (39 / 77 / high risk)

Exports from China are up 88%, European imports up 52%, supplier counts growing —
and measured local import volume is close to zero. Diesel pumping is the
incumbent and fuel supply is unreliable.

- Scoring highlights: real adoption 14/20, early entry 12.6/15 (low local
  penetration, 2 known competitors, low awareness), defensibility 6.7/10.
- Penalties: regulatory uncertainty (−10), supply chain fragility (−8, because
  production is concentrated in one country).
- Geographic gap: **32**. Adoption vs attention: **1.31** — use is growing faster
  than coverage.
- Capital: about $18,000 for a 50-unit minimum order. Your stated ceiling is
  $15,000, so accessibility scored **0/10** and capital fit scored **0** — the
  system does not pretend you can afford it.

**Skeptic's strongest objection:** *"Certification or approval is unresolved, and
that is the kind of thing that turns a six-month plan into a two-year one."*

**Would confirm it:** imports rise for two consecutive quarters; landed cost lands
below local retail with a workable margin; the local distributor count stays low
for another six months.

**Would prove us wrong:** a large distributor enters and takes shelf space; the
product fails local certification; shipping or customs cost erases the margin;
import activity falls for 90 days.

### BUSINESS — Edge inference tooling (56 / 69 / high risk)

Contributor counts, package downloads and job postings are all rising while press
coverage stays flat — **adoption vs attention is 3.34**, the most interesting
shape this measure can take. Teams hit the same deployment problems repeatedly
and nobody sells a solution.

- Real adoption 14/20, market potential 5.3/15 (banded *small* from observed
  download volume — not from a market report), early entry 12.6/15.
- One penalty: **weak adoption evidence −15**, because nobody has been shown to
  pay.

**Skeptic's strongest objection:** *"Nobody has been shown to pay for this. Until
someone does, the demand is hypothetical."*

**Would confirm it:** at least 5 of 15 interviewed customers state a price they'd
pay; adoption keeps rising for another 90 days; a paying customer signs before the
product is finished.

**Would prove us wrong:** fewer than 2 of 15 will name any price; adoption falls
for 90 consecutive days; an incumbent ships the same thing for free.

The system also generated the experiment itself: *"Interview 15 small engineering
teams shipping on-device features and record how many already pay for a
workaround, and how much."* — and marks it **"not yet performed"**, so nobody
mistakes the plan for the result.

### PUBLIC INVESTMENT — ThermaCore Industries (13 / 54 / very high risk)

Covered in detail above. Two things worth adding:

**Trend exposure is never assumed.** The system does not accept "cooling is
growing, therefore this cooling company is good". It records that only **23%** of
ThermaCore's revenue actually comes from the trend.

**A valuation is never formed from a stale price.** If the newest price is more
than five days old, the system reports *"NOT ASSESSED — a valuation from a stale
price would be misleading, so none is given."* Here the price was one day old, so
it did assess it, and found it extreme.

**Would confirm it:** segment revenue grows two quarters running; gross margin
holds while revenue grows; debt falls or cash flow turns positive.

**Would prove us wrong:** revenue growth stalls for two quarters; gross margin
collapses; the largest customer doesn't renew; a competitor cuts price across the
segment.

### REJECTED HIGH-RISK — Luna9 Token (0 / 30 / very high risk)

This is the hardest test in the set, and deliberately so. Unlike the Phase 3 fake
token — which was rejected because its chart was one loud day — **Luna9 clears the
trend layer honestly**, on real on-chain transaction counts that rise steadily
over six months. Its trend score is **60**, the highest of any candidate here.

It then gets destroyed on what it *is*:

| Finding | Value |
|---|---|
| Top 10 wallets hold | **78%** of supply |
| Insiders hold | **41%** |
| Liquidity | **$90,000** — you could not exit |
| Smart-contract audit | none |
| Identifiable founders | none |
| Bot share of activity | **61%** |
| Paid influencer promotion | yes |
| Returns depend on new buyers | yes |
| Unlock schedule | 40% releases in one tranche in 60 days |

Nine automatic flags. Seven penalties totalling 86 points, capped at 65. Twelve
risk rows, one of them **blocking** (ponzi-like incentives), which alone forces
"very high" regardless of everything else.

**Final score: 0. Skeptic verdict: possible manipulation.**

**Skeptic's strongest objection:** *"The visible enthusiasm here may be
manufactured: promotional or coordinated coverage, anonymous team, concentrated
ownership. If that is what this is, every other number on the page is downstream
of someone's marketing budget."*

**Would confirm it** (i.e. what would make it less suspicious): active users grow
for 90 days *without* a matching price move; a published audit from a recognised
firm appears; top-10 concentration falls below 30%.

**Would prove us wrong:** liquidity falls below the level needed to exit;
a large unlock hits the market; developer activity stops for 60 days.

The only participation paths offered are **watch** and *"if the underlying
technology matters, look for a regulated company exposed to it instead"*.

### And the hype case — Quantum wellness devices (11 / 42)

Six months of enormous, sustained press and social growth over an order book that
barely moves. Adoption vs attention: **0.13**. Skeptic: *"22 competitors are
already here. Being right about the trend does not help if you are the sixteenth
to arrive."* Status: watch only.

---

## 5. Which numbers are real and which are demo

This is the part I most want to be exact about.

**Real infrastructure, really exercised:**

- A real **PostgreSQL 16** database. Every number in this report came out of it.
- The full migration chain runs forwards, all the way back to empty, and forwards
  again — creating all 42 tables cleanly.
- The web application was driven through a **real browser**: the opportunities
  list, three detail pages, the research note, recording a decision, Arabic with
  right-to-left layout, and dark mode. No console errors.

**Realistic but generated:**

- All six Phase 4 scenarios. Their shapes are designed so the *correct* answer is
  known in advance — that's the only way to check whether the system got it right.
- The analyzer facts a time series can't supply: supplier counts, ThermaCore's
  debt and margins, Luna9's token distribution. In production these come from
  filings, trade registries and chain explorers. They're in one clearly-marked
  file, and anything absent from it stays UNKNOWN and scores nothing — the same
  behaviour real missing data produces.

**Recorded real responses:**

- The Phase 2 collectors are still tested against saved copies of the real APIs'
  replies, including their awkward cases.

**Not tested at all — stated plainly:**

- **Nothing here has touched the live internet.** Not the collectors, not the
  trend engine, not the opportunity engine.
- **The AI narration has never run against a real language model.** No provider is
  configured. The report is fully deterministic. The *safety machinery* around
  narration — the citation checker and the forbidden-phrase scan — is tested
  against deliberately fabricated and deliberately promotional input, and both
  reject correctly.
- **Docker has not been run.** No Docker daemon in this environment; the stack was
  run natively instead.

---

## 6. Test results

| Measure | Result |
|---|---|
| Tests | **442 passing** |
| Failures | **0** |
| Coverage | **87%** of statements |
| Lint (backend) | clean |
| Lint + types (frontend) | clean; builds with 12 pages |
| Migrations | full chain round-trips on PostgreSQL 16 → 42 tables |

Coverage on the Phase 4 decision-making code specifically: scoring 98%, analyzers
98%, risk engine 97%, experimental measures 95%, skeptic 94%, opportunity engine
93%, report 85%. The uncovered remainder is mostly the background job runner,
which has no work to do until Phase 5.

**The three tests that encode the product's judgement** — the ones whose deletion
would let it quietly become dishonest:

1. `test_a_high_trend_score_does_not_imply_a_high_opportunity_score` — a trend of
   90 in a saturated, expensive, undefensible market must score below 40.
2. `test_the_skeptic_can_only_reduce_confidence` — checked across the whole 0–100
   range against three different input shapes.
3. `test_no_stored_text_ever_tells_anyone_to_buy` — a blunt sweep over every
   title, thesis, warning, risk explanation and report body for "guaranteed",
   "sure thing", "100x", "next bitcoin", "buy now" and "risk-free".

### Bugs found by testing during this phase

**Topic clustering was merging unrelated things.** This is the one worth
understanding. The system treated "these two things were collected in the same
batch" as evidence they were related — so a manual spreadsheet containing a water
heater and an AI tool glued them into a single topic, which the system then
labelled **"AI Water"**. It had chained seven unrelated entities together.

Fixed: clustering now builds one group per shared word and merges two groups only
when half their members overlap. Being collected together may now only *reinforce*
a relationship the names already imply. The demo now produces "AI Agents" and
"Water" as two separate, sensible topics.

**Two migration bugs** that would have failed on your machine, not mine: rolling
the database all the way back failed on a missing index left over from Phase 3,
and the new Phase 4 database link was created without a name, so it could never be
removed. Both fixed, and the whole chain is now verified in both directions.

Also fixed: import opportunities were being proposed for anything measured in a
single country, producing nonsense like "a listed company, imported"; a database
field was receiving the wrong data type on every decision; and analyzer numbers
were being displayed at absurd precision (`10.33362442926142`) — false accuracy on
what is an average of noisy data.

---

## 7. Limitations — honestly

1. **Nothing has been validated against live data.** This is the big one. Every
   score in this report demonstrates that the machinery works; none of them is a
   finding about the real world.

2. **Real results will be duller than these.** The demo scenarios have clean,
   deliberately-shaped curves. Real data is noisy. A live trend scoring 40 where a
   scenario scored 67 is the system working correctly, not failing.

3. **The analyzer facts are hand-entered.** Supplier counts, filings and token
   distributions come from a file, not from a live feed. Wiring those to real
   sources is a substantial piece of work that Phase 4 did not include.

4. **The thresholds are first guesses.** The gate minimums, the eight weights, the
   twenty penalty sizes — all reasonable, none validated. Phase 6 backtesting is
   what would tell us whether a score of 56 means anything. Every threshold is
   versioned and in one file, so changing them is a deliberate, recorded act.

5. **The two experimental measures are unproven.** That's why they're excluded
   from the score. Do not act on the geographic gap number.

6. **Confirmation and invalidation conditions are stored but not yet checked.**
   The system writes down what would prove it wrong; nothing yet watches for it.
   That's Phase 5/6.

7. **"Willingness to pay" is almost always UNKNOWN**, which is correct and also
   means most business candidates carry a −15 penalty they can only shed through
   research you do yourself. The system is honest that it cannot know this.

8. **A candidate can look good and be irrelevant to you.** Solar water pumps needs
   $18,000 against your stated $15,000 ceiling — so it scored 0 for accessibility
   and 0 for capital fit, and still appears at 39. Read the relevance breakdown,
   not just the score.

9. **Currency is preserved but never converted.** The geographic gap refuses to
   compare a price in dinars with a price in yuan rather than guessing at a rate.
   That's the safe behaviour, and it also means some gaps are less informative
   than they could be.

10. **Database-level immutability still isn't enforced.** The application refuses
    to alter records that should never change, and that's tested — but someone
    with direct database access could still do it.

---

## 8. What Phase 5 would add

Phase 4 finds candidates. Phase 5 is about **following them and telling you when
something changes**.

- **Watchlists** — follow an entity, topic, country or keyword with your own
  thresholds.
- **Checking the conditions.** This is the most valuable item on the list. Every
  candidate already says what would confirm it and what would prove it wrong;
  Phase 5 is where the system starts actually watching for those and telling you
  when one triggers.
- **Alerts and a daily Telegram digest** — including, importantly, alerts when a
  candidate you were following gets *worse*.
- **Region-specific relevance for Tunisia and Libya**, going deeper than the
  current relevance score.
- **Deeper import analysis** — supplier discovery, landed-cost calculation,
  customs-code lookup.
- **Notes and decisions in context**, building on the decision log Phase 4 already
  writes.

And then Phase 6 — backtesting — is where we finally find out whether any of this
scoring predicts anything, using the frozen decision records Phase 4 has already
started keeping.

---

## Where things stand

Phase 4 is complete, tested and documented against demo data. 442 tests pass, the
six scenarios all behave as specified, a trend of 60 correctly produces an
opportunity score of 0, and a trend of 44 correctly produces an opportunity score
of 13 with a plain-English explanation of why.

**The live-data gate is not closed, and I have not pretended otherwise.**

**I am stopping here and waiting for your approval before starting Phase 5.**
