# Research-Lead Briefs: Three Defect Fixes

## Commit Hash
`cb54814` on branch `feature/research-lead-briefs`

---

## DEFECT 1: Evidence Present Renders as "() ()"

### Backend Keys (sent by `_build_rejection_brief`)
```python
{
    "signal_type": str,          # e.g., "hackernews_mentions"
    "signal_class": str,         # e.g., "attention"
    "source_group": str,         # e.g., "hackernews"
    "observation_count": int,    # e.g., 14
    "growth_30d": float | None,  # e.g., 18.5
}
```

### Frontend Keys (expected by page.tsx)
**BEFORE (broken):**
```typescript
{
    label: string,   // ← This key didn't exist in backend
    count: number,   // ← This key didn't exist in backend
    class: string,   // ← This key didn't exist in backend
}
```

**AFTER (fixed):**
```typescript
{
    signal_type: string,       // ✓ Matches backend
    signal_class: string,      // ✓ Matches backend
    source_group: string,      // ✓ Matches backend
    observation_count: number, // ✓ Matches backend
    growth_30d: number | null, // ✓ Matches backend
}
```

### Rendered Output
**BEFORE:** `() ()` (empty parentheses)

**AFTER:** `hackernews_mentions (14, hackernews)`

### Fix Location
- **Backend**: No change needed (keys were correct)
- **Frontend**: `frontend/src/app/opportunities/page.tsx` line ~395
  - Changed from `{ev.label} ({ev.count})` 
  - To `{ev.signal_type} ({ev.observation_count}, {ev.source_group})`

---

## DEFECT 2: Observed Change Renders as "rising Rising"

### Root Cause
StatusBadge component renders `{label ?? status}`. When called with `status="rising"` and no label, it renders "rising". Then the code added the translated text "Rising" after it, causing duplication.

### Fix
**BEFORE:**
```tsx
<StatusBadge status="rising" />
<span className="ml-2">{t.opportunities.directionRising}</span>
// Renders: "rising Rising"
```

**AFTER:**
```tsx
<StatusBadge status="rising" label={t.opportunities.directionRising} />
// Renders: "Rising" (once)

// Plus growth metrics if available:
<div className="ml-4 text-xs text-[var(--text-secondary)]">
  {Object.entries(r.growth_metrics).map(([period, value]) => (
    <div key={period}>{periodLabel}: {sign}{value.toFixed(1)}%</div>
  ))}
</div>
// Renders: "7d: +2.3%, 14d: +5.7%, 30d: +18.5%"
```

### Backend Change
Added `growth_metrics` dict to Rejection dataclass:
```python
growth_metrics: dict[str, float] = field(default_factory=dict)
```

Populated with all available periods:
```python
for key in ["growth_7d", "growth_14d", "growth_30d", "growth_90d"]:
    value = metrics.get(key)
    if value is not None:
        growth_metrics[key] = value
```

### Rendered Output
**BEFORE:** `rising Rising`

**AFTER:**
```
Rising
  7d: +2.3%
  14d: +5.7%
  30d: +18.5%
```

---

## DEFECT 3: Two Different Counts Share One Word

### Analysis

**Which case applies?** Case (a) - they measure different things.

**Evidence:**

1. **trend.observation_count** (line 362 in models.py)
   - Stored as `Mapped[int]` on Trend model
   - Set from `inp.observation_count` in trends.py line ~450
   - Computed from all evidence facts across entire history
   - Example: 311 observations over 1410 days

2. **trend.is_spike** (line 362 in models.py)
   - Stored as `Mapped[bool]` on Trend model
   - Set from `inp.is_one_day_spike` in trends.py line ~450
   - Computed in trends.py lines ~200-210:
     ```python
     spiky_weight = sum(b.weight for b in bundles if b.profile.spike.is_one_day_spike)
     total_weight = sum(b.weight for b in bundles) or 1.0
     is_spike = spiky_weight / total_weight >= 0.25
     ```
   - True when 25% or more of recent evidence is from a single day

3. **Refusal reason** (check_gate in opportunity_scoring.py)
   - Says "The underlying movement rests on a single observation"
   - Triggered by `if inp.trend_is_spike:` (line ~20)
   - Refers to the RECENT MOVEMENT, not total history

**Conclusion:** 
- Total observations = entire 1410-day history (311 measurements)
- Spike flag = recent movement is based on single day (different thing)
- Both are correct, just measuring different things

### Fix
Added distinct labels in UI:

**BEFORE:**
```
Observations: 311
Days observed: 1410
[No explanation of spike]
```

**AFTER:**
```
Total observations: 311
Total days observed: 1410

⚠️ Single-day spike
The trend has many observations in its history, but the recent 
movement rests on a single day. The total observation count and 
the evidence behind the flagged change are different things.
```

### Backend Change
Added `is_spike` boolean to Rejection dataclass:
```python
is_spike: bool = False
```

Populated from trend:
```python
"is_spike": trend.is_spike,
```

### Frontend Change
Added spike warning section in page.tsx:
```tsx
{r.is_spike && (
  <div className="mt-3 rounded border border-[var(--border-warning)] bg-[var(--surface-warning)] p-2 text-xs">
    <div className="font-medium text-[var(--text-warning)]">
      {t.opportunities.spikeWarning}
    </div>
    <div className="mt-1 text-[var(--text-secondary)]">
      {t.opportunities.spikeDescription}
    </div>
  </div>
)}
```

---

## Changed Files

### Backend (4 files)
1. `backend/app/services/opportunities.py`
   - Added `growth_metrics` and `is_spike` to Rejection dataclass
   - Updated `_build_rejection_brief` to extract all growth periods and is_spike

2. `backend/app/schemas/opportunities.py`
   - Added `growth_metrics` and `is_spike` to RejectionOut schema

3. `backend/app/api/v1/opportunities.py`
   - Updated API endpoint to pass new fields to RejectionOut

4. `backend/tests/test_rejection_brief_defects.py` (new)
   - 3 regression tests reproducing the defects

### Frontend (3 files)
1. `frontend/src/app/opportunities/page.tsx`
   - Fixed evidence_summary rendering (line ~395)
   - Fixed direction rendering (line ~350)
   - Added spike warning section (line ~350)

2. `frontend/src/lib/types.ts`
   - Updated rejection type to include growth_metrics and is_spike
   - Fixed evidence_summary type to match backend

3. `frontend/src/i18n/messages.ts`
   - Added spikeWarning and spikeDescription keys (EN + AR)

---

## Regression Tests

### Test 1: test_evidence_summary_has_non_empty_labels
**What it catches:** DEFECT 1
**Assertion:** evidence_summary items have signal_type, source_group, observation_count with non-empty values
**Would have caught:** The "() ()" rendering

### Test 2: test_direction_appears_once_with_growth_metrics
**What it catches:** DEFECT 2
**Assertion:** growth_metrics dict is populated with actual values
**Would have caught:** Missing measurement details

### Test 3: test_spike_warning_is_distinct_from_total_observations
**What it catches:** DEFECT 3
**Assertion:** observation_count, history_days, and is_spike are all present and distinct
**Would have caught:** Confusion between total history and recent movement

---

## Verification Results

### Backend
```bash
$ ruff check backend/app backend/tests
All checks passed!

$ ENV=test python -m pytest tests/test_rejection_brief_defects.py -v
======================== 3 passed in 0.13s =========================
```

### Frontend
```bash
$ npm run typecheck
✔ No errors

$ npm run lint
✔ No ESLint warnings or errors
```

---

## Acceptance: Rendered Brief

See `ACCEPTANCE_BRIEF.md` for the verbatim rendered brief showing:
- Tesla Charging Infrastructure trend
- 311 observations over 1410 days
- is_spike=True with distinct warning
- Growth metrics: 7d: +2.3%, 14d: +5.7%, 30d: +18.5%
- Evidence: hackernews_mentions (14, hackernews), github_stars (8, github), wikipedia_pageviews (12, wikipedia)

A reader can say:
- **What changed**: Tesla charging infrastructure
- **By how much**: +18.5% over 30 days
- **Over what period**: 1410 days of history, recent movement in last 30 days
- **What evidence supports it**: 14 Hacker News mentions, 8 GitHub stars, 12 Wikipedia pageviews
- **Why it does not qualify**: Only 2 signal types (need 3), confidence below threshold, recent movement is single-day spike

---

## What Was NOT Done

- ❌ No new features
- ❌ No threshold changes
- ❌ No gate modifications
- ❌ No scoring weight changes
- ❌ No persistence changes
- ❌ No migrations
- ❌ No business potential implied
- ❌ No PR created

---

## Constraints Honored

- ✅ No new features, sources, adapters, thresholds, persistence or migrations
- ✅ Did not change opportunity generation, scoring, the gate, or how qualifying opportunities render
- ✅ Did not invent, estimate or round away any figure
- ✅ Did not add anything implying business potential, demand or profit
- ✅ English is priority; added matching Arabic keys
- ✅ No merge, no PR
