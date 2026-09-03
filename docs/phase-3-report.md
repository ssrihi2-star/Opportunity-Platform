# Phase 3 Report — Teaching the System to Understand Trends

Written for a non-programmer. Where a technical word is unavoidable, it is explained
the first time it appears.

---

## 1. What was built, in plain language

Phase 1 built the foundation. Phase 2 taught the system to collect real data from the
outside world. **Phase 3 taught it to look at that data and say what is happening.**

It still does not tell you to buy, import, or start anything. That is Phase 4. Right
now it answers five questions and nothing more:

1. What is this thing?
2. Is activity around it going up?
3. Is the increase unusual, or is it ordinary?
4. Do several independent sources agree?
5. What stage does it look like — brand new, growing, mature, or fading?

Here is what had to be built to answer those honestly.

### It only merges two records when it has proof

The dangerous shortcut in a system like this is to decide that two names that look
alike are the same thing. "Apple" and "Apple Bank" share most of their letters and are
completely unrelated companies.

So the system now refuses to merge on names at all. It merges two records only when
they share an **official identifier** — a registration number a real institution
issued. It understands these:

| Kind of thing | Identifier used |
|---|---|
| A public company | SEC filing number (CIK) and stock ticker plus exchange |
| A software project | The GitHub repository's own numeric ID |
| A country | The official ISO country code |
| A crypto asset | The contract address **together with** which blockchain it is on |
| A traded good | The HS customs code |

When two names look similar but share no identifier, the system does **not** guess. It
creates them as two separate things and puts the pair on a review list for you.

### There is a review screen, and your decisions are permanent

The new **Entity review** page shows each uncertain pair with the reason it was
flagged, and gives you three buttons:

- **Same thing — merge**
- **Not the same**
- **Related, keep separate**

Whatever you choose is written down permanently, with your name, the time, and any note
you added. The system will never ask you about that pair again, and it will never
quietly overwrite your decision. If something tries to re-decide an already-decided
pair, it is refused.

### Topics are found by arithmetic, and only *named* by AI

The system groups related things into topics — for example it worked out on its own
that "AI code review", "AI pair programming" and "AI coding agents" belong together.

That grouping is done by counting shared words, not by asking an AI. **An AI is
allowed to give a cluster a nice name. It is not allowed to invent the cluster.** The
database even records which labels an AI wrote, so you can always tell.

### Missing data is stored as missing — never as zero

This one is small to describe and important in practice.

If a data source publishes nothing for a month — a statistics office is late, a website
is down — the old behaviour in most systems is to record a `0`. That is a lie. It makes
a quiet month look like a total collapse, and a collapse looks like news.

Every measurement now carries a label: **ok**, **missing**, or **failed**. A missing
period stores no number at all. The calculations skip it, the trend page tells you how
many periods were not reported, and the confidence score is reduced slightly to reflect
that there is a hole in the record.

You can see this working on the "mycelium packaging" example below: it shows
*"Periods not reported: 3. Gaps are excluded from every calculation, never counted as
zero."*

### Money keeps its own currency

An amount is always stored together with the currency it was published in. Nothing is
converted to dollars, and no original figure is ever overwritten by a converted one.
The ceramic tiles example is stored in Tunisian dinars because that is how the customs
data was published. Conversion can be added later as a display option, on top of the
original — never in place of it.

### It measures change in six different ways

All of this is ordinary arithmetic, done by the computer, with no AI involved:

- **Growth** — over 7, 30, 90 and 365 days. If there is not enough history to answer, it
  says "not enough history" instead of returning a made-up zero.
- **Momentum** — the overall slope of the line.
- **Acceleration** — is the growth rate *itself* rising? A thing growing 10% a month is
  interesting. A thing whose monthly growth went 5%, then 15%, then 40% is a different
  story.
- **Persistence** — did it rise steadily in many small steps, or all at once in a single
  jump? Twenty small rises are more believable than one big one.
- **Spikes** — see below.
- **Seasonality** — does this same rise happen at the same time every single year?

### It can tell a real rise from a one-day spike

This was the specific trap you asked about: the pattern `10, 11, 9, 300, 12`.

Measured naively, that is nearly 3000% growth. It is of course nothing — one loud day
and then silence.

The system calls something a one-day spike only when **all four** of these are true:

1. The peak is at least 5× the normal level of the series, **and**
2. the peak is at least 5× its own immediate neighbours (the days either side), **and**
3. the raised part is at most two data points wide, **and**
4. it either came back down afterwards, or it is the newest point.

Point 2 is the one that took real work. Without it, every genuinely accelerating thing
gets wrongly flagged, because a growing line's highest point is always its newest one.
The test that matters: `700 → 1200` is growth. `9 → 300` is a spike.

### Five newspapers running the same story count as one source

Five outlets republishing one wire story is not five independent confirmations. It is
one story, five times.

The system groups sources by their owner, and it also compares the headlines
themselves. It recognises that *"ZephyrCoin surges as traders pile in — Reuters"* and
*"Traders pile in as ZephyrCoin surges, say analysts | Bloomberg"* are the same story
with the words rearranged, and counts them once. It also distinguishes different
*kinds* of evidence: developer activity and press coverage are two kinds; two different
press metrics are still just one.

### Two separate scores, and the arithmetic is shown

**Trend Score (0–100)** — how unusual and well-supported the movement is. Built from
seven ingredients: growth, acceleration, persistence, source diversity, scale,
geographic spread, and novelty.

**Confidence Score (0–100)** — a completely separate question: *how much can this
judgement be relied on?* Built from how long the history is, how many measurements
there are, how good the sources are, how many are independent, and whether the
different methods agree.

Keeping them apart matters. "Score 90, confidence 40" is a useful and honest sentence:
*this looks strong, and we have only two weeks of data.* A single blended number would
hide that.

Nine penalties can be subtracted — for a one-day spike, a tiny starting number, a
seasonal pattern, republished stories, too little history, weak sources, indirect
measurements only, a single source, and missing data. Penalties are capped at 60 points
in total, so a genuinely strong trend cannot be zeroed by a pile of small complaints;
when the cap is reached, the page says so.

**The whole calculation is printed on the page.** Every ingredient, its maximum, the
sentence explaining it, every penalty, the subtotal before penalties, and the final
number. Nothing is hidden behind the total.

### Seven stages, decided by rules — not by AI

`weak signal` → `emerging` → `early adoption` → `accelerating` → `mainstream` →
`mature` → `declining`.

Each stage has written rules based on numbers. For example, "accelerating" requires the
growth rate to be rising by more than 15 percentage points, growth above 25%, and at
least half the periods moving up. No AI participates in this decision.

### A trend is followed over time, not re-discovered every day

Each thing being watched has **one** record, which gets updated. Every evaluation adds a
dated snapshot to its history, so you can see how a score moved over weeks. Running the
analysis twice does not create duplicates — I tested exactly this.

Its status moves through six states: **candidate → active → confirmed → weakening →
ended**, plus **invalidated** for something that was promoted and then turned out to be
false.

Two rules do most of the work here:

- **One source is never enough to become "active."** A single source can produce a high
  score, but calling that "active" would dress one opinion up as agreement. It stays a
  candidate until something independent confirms it.
- **Spikes and seasonal patterns never rise above "candidate."** Both are real patterns.
  Neither is a trend.

### Two new screens

**Trends** — a sortable, filterable list of everything being watched, with both scores,
the stage, the status, how many independent sources, and the country. You can filter by
category, stage, status, country and kind, and hide one-day spikes and seasonal
patterns entirely.

**Trend detail** — the full picture for one thing: the score tiles, the warnings in
plain sentences, a chart of the actual measurements with a note of how many periods were
not reported, "why the system flagged this", the complete score arithmetic, the
confidence arithmetic, which measurements support it and who published them, the score's
history, and a dated evidence timeline.

Both work in English and Arabic (with the whole layout mirrored right-to-left), in light
and dark mode. The stage and status words are translated too.

### What the AI is and is not allowed to do

There is a button that writes a plain-English paragraph about a trend. Before it runs,
the system assembles a **closed list of facts** from what is already stored, and the AI
is only allowed to use that list. Afterwards, the paragraph is checked: every factual
claim in it must point back to a stored fact.

**If the check fails, the paragraph is thrown away rather than corrected.** The page then
shows the numbers with no prose. That is deliberate — a wrong sentence that is patched up
is more dangerous than no sentence.

The AI cannot change a score, a stage, a status, or a fact. Anywhere.

---

## 2. Five example trends from the demo system

These come from the six built-in demo scenarios, each designed so the *correct* answer
is known in advance. All numbers below were produced by the real engine running on a
real PostgreSQL database — I did not write them by hand.

### Example 1 — "AI coding agents" · Score **67** · Confidence **87**

**Stage: accelerating. Status: confirmed. 3 independent sources.**

Why it scored well:

| Ingredient | Points | The system's own explanation |
|---|---|---|
| Growth | 12.8 / 20 | 30-day growth is +36.3% |
| Acceleration | 16.3 / 18 | growth is running +54.4 percentage points above the earlier period |
| Persistence | 15 / 15 | 100% of consecutive periods moved up — the rise is consistent |
| Source diversity | 11.1 / 18 | 3 independent sources across 2 kinds of evidence |
| Scale | 6.4 / 12 | latest level about 401 |
| Geographic spread | 2.8 / 7 | global evidence only, so no local spread to show |
| Novelty | 3 / 10 | near, but not above, the previous peak |

**Penalties: none.** Confidence is 87 because there are 200 measurements over 199 days
from three separate owners, with no gaps.

This is what a real, growing thing looks like: not one dramatic jump, but a line that
keeps bending upwards, seen by three parties who do not talk to each other.

### Example 2 — "ZephyrCoin" · Score **0** · Confidence **70**

**Stage: weak signal. Status: candidate. Flagged as a one-day spike.**

This is the most important example in the whole report, so read the numbers carefully.

Its measured 30-day growth is **+197.7%** — a real number, correctly calculated. Its
acceleration is +178 percentage points. Both of those score the maximum. Before
penalties it stood at **57 out of 100**.

Then:

| Penalty | Points |
|---|---|
| Tiny starting number (it went from 1 to 4) | −15 |
| One-day spike (one day hit 293 against a normal level of 10 — 29× — then went back down) | −25 |
| Weak sources (average source reliability 0.40) | −10 |
| Republished stories (60% of the articles are the same story reprinted) | −12 |
| **Total 62, capped at 60** | |

**Final score: 0.**

Confidence stays at **70**, and that combination is the point. The data is complete and
we trust it; we are *confident* that this is not a trend.

The warnings shown on its page, word for word:

> Growth is measured from a baseline of 1 and the total move is 3. A large percentage on
> numbers this small is arithmetic, not evidence.
> One observation reached 293 against a typical 10 (29×) and the level then returned to
> normal.
> Mean source reliability is 0.40; the evidence comes mostly from weak sources.
> 60% of the supporting articles are the same story republished elsewhere.

### Example 3 — "ceramic floor tiles" · Score **36** · Confidence **80**

**Stage: mature. Status: candidate. Flagged as seasonal.**

This one is a trap of a different shape. Its acceleration scored the full 18/18 — recent
growth is running 269 percentage points above the earlier period, which on paper looks
spectacular. It is also, in absolute terms, a large business: the latest level is about
3.7 million.

But the system has three years of monthly history, and it can see that this exact rise
happens in this exact month every single year. It applies a **−15 seasonality penalty**,
and — more importantly — the rule that a seasonal pattern can never be promoted above
"candidate". So despite good data (confidence 80) and a large real business, it is not
presented as a trend. It is a season.

Note that seasonality is only tested when there are at least two full years of history.
With less, the system says it cannot tell rather than guessing.

### Example 4 — "mycelium packaging" · Score **43** · Confidence **83**

**Stage: emerging. Status: active. 3 periods not reported.**

Unspectacular, steady, real growth: +8.7% over 30 days, but **100% of consecutive
periods moved up**, so persistence scored the full 15/15. Two independent sources of two
different kinds. No penalties at all.

This is also the example that demonstrates the missing-data rule. The customs stream has
three days where nothing was published. Those three periods are stored as *missing*, the
page says so, and the confidence score carries a small penalty of −0.3 for the gap.
Nothing was invented to fill the hole.

A score of 43 is the honest answer here: this is genuinely moving and nothing is wrong
with the evidence, but it is not moving fast.

### Example 5 — "DVD authoring software" · Score **14** · Confidence **67**

**Stage: declining. Status: weakening.**

The opposite case, included to prove that decline is detected rather than ignored:

| Ingredient | Points | Explanation |
|---|---|---|
| Growth | 0 / 20 | 30-day change is −16.4%; nothing is growing |
| Acceleration | 0 / 18 | growth is not speeding up |
| Persistence | 0 / 15 | 0% of consecutive periods moved up |
| Novelty | 0 / 10 | current level is 56% below the previous peak |

It keeps 8.6 points for having two decent independent sources, which is why it is 14
rather than 0. Confidence is a respectable 67 — we have good data, and the good data
says this is going away.

---

## 3. What was tested against real internet data, and what was not

I want to be exact about this, because it is the part that is easiest to overstate.

**Tested against real infrastructure:**

- A real **PostgreSQL 16** database with the pgvector extension — not a lightweight
  stand-in. All the numbers in this report come from it.
- The database migrations were run forwards, then backwards, then forwards again on
  PostgreSQL, creating all 37 tables cleanly.
- The web application was driven through a **real browser** — signing in, the trends
  list, three different trend detail pages, the entity review screen, Arabic with
  right-to-left layout, and dark mode — while watching for errors. There were none.

**Tested against realistic but generated data:**

- The six demo scenarios are series with deliberately known shapes: one genuinely
  accelerating, one steady, one declining, one seasonal, one hype spike with syndicated
  press coverage, and one topic cluster. This is on purpose — you can only check that
  the system got the right answer if you know what the right answer is.

**Tested against recorded real responses:**

- The data collectors from Phase 2 (GitHub, Hacker News, RSS, Wikipedia, FRED, SEC
  EDGAR, UN Comtrade) are tested against saved copies of those services' real replies,
  including their awkward cases — FRED's "." placeholder for an unpublished month, a
  feed returning an error page instead of data, a website that forbids automated access.

**NOT tested — and I want this stated plainly:**

- **The trend engine has never run on live data pulled from the internet during this
  build.** This environment has no outbound access to GitHub, FRED, SEC or the others.
  The collectors work against recorded responses; the trend engine works on the
  scenarios. The joint has been exercised end to end, but with generated series at the
  input.
- **The AI explanation has never been run against a real language model**, because no
  provider key is configured. The safety machinery around it — the fact checker that
  rejects invented citations — *is* tested, including against deliberately fabricated
  citations. But the prose quality of a real model's output is unknown.
- **The Docker setup has not been run.** There is no Docker available in this
  environment. Everything was run natively instead. The configuration file is valid but
  unexecuted.

---

## 4. Important limitations

1. **Everything above is measurement, not judgement.** The system can tell you that
   something is growing unusually fast with good evidence. It cannot tell you whether
   that is a business worth being in. That gap is the whole of Phase 4.

2. **A high Trend Score is not a prediction.** It is a statement about the recent past
   and about how well-evidenced it is. Things that grow fast frequently stop.

3. **Scale comparisons are coarse.** Different sources measure in different units —
   commits per week, tonnes, pageviews. The "scale" ingredient can only place a series
   in a rough size band, and the page says so.

4. **Seasonality needs two full years.** With less history, a seasonal pattern may pass
   as a trend. The system knows this and says it cannot tell, but it cannot fix it
   without more data.

5. **Source reliability is a hand-set number today.** Each source has a reliability
   figure that a human chose. Learning it from how often a source turned out to be right
   requires Phase 6, which needs elapsed calendar time.

6. **The review queue needs a human.** If nobody looks at it, uncertain name matches stay
   unmerged forever. That is the safe failure — two records for one company is a nuisance,
   whereas one record for two companies is a false conclusion — but it is a real cost.

7. **The demo data is generated.** The scores in this report are correct for those
   series. Real-world series are noisier, and thresholds may need tuning once real data
   flows.

8. **Nothing enforces immutability at the database level yet.** The application refuses
   to change records that should never change, and that is tested — but someone with
   direct database access could still do it. A database-level lock is scheduled.

---

## 5. Test results

**270 automated tests pass. Zero failures. Code style checks clean on both the backend
and the web application, which builds without warnings.**

New tests written for Phase 3 cover, specifically:

| Area | What is proven |
|---|---|
| Entity matching | every kind of official identifier correctly merges two records |
| Entity mismatching | similar names never merge on their own; a review item is raised instead |
| Human decisions | a decision is honoured afterwards, and cannot be silently overwritten |
| Missing data | a gap is stored as missing, is skipped by the maths, and is never read as zero |
| Zeros | a real measured zero is kept as a real zero, and not confused with a gap |
| Duplicates | rearranged headlines from different outlets are recognised as one story |
| Tiny-number explosions | a rise from 1 to 4 does not produce a high score |
| Real acceleration | a genuinely accelerating series is *not* flagged as a spike |
| Spikes | `10, 11, 9, 300, 12` is caught |
| Steady growth | a slow, consistent rise scores modestly and is not dismissed |
| Decline | a falling series is classified as declining, not ignored |
| Seasonality | a yearly repeat is detected and penalised, and never promoted |
| Confirmation | independent sources count; syndicated ones do not |
| Trend Score | the arithmetic is stable and every component is explained |
| Confidence | computed separately from the score |
| Lifecycle | the same trend is updated, not duplicated; states move in the right order |
| End to end | all six scenarios produce their expected score, stage and status |

**Two real bugs were found by testing during this phase and fixed:**

- The migration created the two new history tables *before* rebuilding the trends table
  they point at. The lightweight test database does not enforce that kind of link and
  ran it happily; real PostgreSQL refused. This would have failed on your machine on the
  first deployment. It is fixed, and migrations are now checked against real PostgreSQL.
- The spike detector was flagging genuine acceleration as fake, because a growing line's
  highest point is always its newest one. Fixed by the neighbour comparison described
  earlier.

Several smaller issues were also corrected: a crypto asset stopped matching itself after
being re-saved, republished-story detection was accidentally measuring the wrong text and
penalising real trends, a single source could reach "active", and a bad currency code in
an uploaded file was accepted at upload time and only rejected later.

---

## 6. What Phase 4 would add

Phase 3 tells you *what is happening*. Phase 4 is where the system starts telling you
*what might be worth doing about it* — carefully, and still without ever saying "buy".

- **An opportunity generator** that only proposes something when at least three
  different kinds of signal and two genuinely independent sources agree. A trend does not
  automatically become an opportunity.
- **A scoring engine for opportunities** — separate from the Trend Score — weighing real
  adoption over attention, market size, ease of entry, defensibility, and a dated
  catalyst.
- **A skeptic pass**: a deliberate attempt to argue *against* each candidate, list what
  evidence is missing, and estimate the chance the whole thing is manipulation. It is
  allowed to reduce confidence and never to raise it.
- **A risk engine** with explicit flags — anonymous team, concentrated ownership, thin
  liquidity, regulatory danger — and a rule that crypto can never be labelled low risk.
- **Evidence-backed reports** where every single factual claim links to a stored source
  document you can open. A report that cannot prove a claim does not get published.
- **An opportunities dashboard** with the honest ranking, the risk level, and the
  reasons.

That is also the first phase where anything resembling "consider this" appears in the
product, which is why it deserves the same slow, evidence-first treatment this one got.

---

## Where things stand

Phase 3 is complete, tested and documented. The application runs, the six scenarios
behave correctly, and the hype scenario scores zero despite a genuine +197.7% growth
figure — which was the real objective.

**I am stopping here and waiting for your approval before starting Phase 4.**
