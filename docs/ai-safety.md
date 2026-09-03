# AI Safety and Grounding

## 1. Non-negotiable rules

1. The model may never state a number, name, date, price, funding round, user
   count, market size, regulation or quote that is not present in a retrieved
   `evidence_item`.
2. Every factual sentence carries `evidence_ids`. A sentence without evidence must
   be phrased as `Unknown`, `Insufficient evidence`, `Estimate`, `Unverified` or
   `Requires additional research`, and tagged `claim_type = "unverified"`.
3. Report generation is **retrieval-scoped**: the prompt contains only evidence
   already stored for that opportunity. There is no open web access at generation
   time.
4. Output is JSON validated by Pydantic. Prose that fails validation is retried
   once, then the report is abandoned — never repaired by a second pass that could
   invent content.
5. `verify_report` rejects the whole report if any cited id is not in the allowed
   evidence set, or if citation density falls below `MIN_CITATION_DENSITY` (0.9).

## 2. Prompt injection defence

| Layer | Control |
|---|---|
| Storage | raw text kept verbatim for audit; a sanitised copy is what goes downstream |
| Sanitiser | neutralises `ignore previous instructions`, `system:`, `</system>`, tool-call syntax, markdown exfiltration patterns, zero-width characters, long base64 blobs |
| Envelope | untrusted text wrapped in `<<<UNTRUSTED_CONTENT {nonce}>>> … <<<END… {nonce}>>>` with a per-call random nonce, so injected closers cannot match |
| Prompt | the system prompt is a constant string, never templated with external text |
| Tools | the report and skeptic models have **no tools** |
| Output | JSON schema; URLs not present in evidence are stripped |
| Logging | every neutralised marker increments a counter and writes a `system_audit_logs` row |

`backend/tests/test_security.py` runs a corpus of injection payloads through the
sanitiser and asserts each is flagged and defanged.
`backend/tests/test_grounding.py` asserts a report citing a fabricated evidence id
is rejected with that id named in the error.

## 3. Model use policy

| Task | Allowed model tier |
|---|---|
| Numeric anomaly detection | **none** — deterministic only |
| Scoring | **none** — deterministic only |
| Risk flag thresholds | **none** — deterministic only |
| Category / subcategory classification | small |
| Entity disambiguation fallback | small |
| Skeptic narrative | large, shortlisted only |
| Report narrative | large, shortlisted only |

## 4. Cost ledger

Every call writes a `model_runs` row: provider, model, prompt version, input and
output tokens, USD cost, latency, cache hit, opportunity id. `app/ai/budget.py`
refuses a call that would exceed the daily or monthly cap and raises
`BudgetExceededError`, which degrades the pipeline to deterministic-only mode
rather than failing the run.

The default provider is `echo`: an offline stand-in that returns a valid, empty,
`insufficient_evidence` envelope. It invents nothing, costs nothing, and lets the
whole system run with no API keys. Vendor adapters that lack a key raise a clear
error naming the missing variable rather than silently degrading.


## Phase 4: what the model may and may not do with an opportunity

The opportunity report is built in two layers, and the split is the safety story.

**The deterministic layer** assembles every section from stored rows. It runs with
no model configured at all and produces a complete, correct report. Nothing in it
can be invented, because nothing in it is generated. The test suite asserts that a
report with no provider configured still contains all thirteen required sections.

**The narration layer** may ask a model to phrase three of them — what is
happening, why it could matter, and a summary. It is handed a *closed fact set*
built from the stored numbers, wrapped in the untrusted-content envelope, and its
output faces two checks:

1. the grounding verifier, at a citation density of 0.9, rejecting any claim that
   cites a fact id that does not exist;
2. a forbidden-phrase scan: `guaranteed`, `sure thing`, `buy now`, `invest now`,
   `100x`, `next bitcoin`, `risk-free`, `no risk`, `can't lose`.

A draft that fails either is **discarded, not repaired**. The page then shows the
deterministic prose instead. A report that had to be corrected is a report that
was willing to be wrong.

The model may:

* explain and compare stored evidence;
* state the thesis and counter-thesis the facts support;
* summarise risks and suggest further research questions.

The model may **not**:

* introduce a statistic, company, price, market size, supplier, regulation or
  adoption number that is not in the fact set;
* change any score, confidence, risk level, condition or status;
* say that anyone should buy, invest in, import or start anything, or that a
  profit is likely.

The skeptic agent is deterministic in Phase 4. Its findings come from arithmetic
over stored evidence; a model may later phrase them more fluently but cannot add,
remove, or reweight one. Structurally, it can only **reduce** confidence — there
is no code path that raises it, and a parametrised test asserts that property
across the whole input range.
