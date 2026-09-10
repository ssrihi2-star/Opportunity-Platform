# Acceptance: Rendered Brief for Trend with Long History and Thin Recent Movement

This document shows the verbatim rendered brief for a trend with:
- 311 observations over 1410 days (long history)
- is_spike=True (recent movement rests on a single day)
- Multiple growth periods available
- Evidence from multiple sources

---

## Research Brief: Tesla Charging Infrastructure

**Status**: candidate  
**Type**: [product]

### Metrics

| Metric | Value |
|--------|-------|
| Trend score | 42.3 |
| Confidence | 28.1 |
| Signal types | 2 |
| Sources | 3 |
| **Total observations** | **311** |
| **Total days observed** | **1410** |

### ⚠️ Single-day spike

The trend has many observations in its history, but the recent movement rests on a single day. The total observation count and the evidence behind the flagged change are different things.

### Observed change

**Rising**
- 7d: +2.3%
- 14d: +5.7%
- 30d: +18.5%

### Evidence present

- hackernews_mentions (14, hackernews)
- github_stars (8, github)
- wikipedia_pageviews (12, wikipedia)

### Evidence missing

- Signal types of 2 is below the minimum of 3. Only 2 distinct signal types were observed: hackernews_mentions, github_stars. Need at least 3 different kinds of evidence.
- Confidence of 28.1 after the skeptic pass is below the minimum of 30. Strongest objection: Market is highly competitive with established players; recent spike may be noise.

[View trend detail →](/trends/abc123)

---

## Key Points

1. **Total observations (311)**: This is the count of all measurements in the trend's 1410-day history.

2. **Single-day spike warning**: Despite having 311 observations, the recent movement that triggered the trend flag rests on a single day. This is a critical distinction.

3. **Growth metrics**: Multiple periods are shown (7d, 14d, 30d) with actual measured values, not just a generic "Rising" label.

4. **Evidence with real names**: Each evidence item shows the signal type, observation count, and source group (e.g., "hackernews_mentions (14, hackernews)").

5. **Distinct labels**: The metrics grid shows "Total observations: 311" and "Total days observed: 1410", while the spike warning explains that the recent movement is different from the total history.

A reader can now say:
- **What changed**: Tesla charging infrastructure
- **By how much**: +18.5% over 30 days
- **Over what period**: 1410 days of history, with recent movement in the last 30 days
- **What evidence supports it**: 14 Hacker News mentions, 8 GitHub stars, 12 Wikipedia pageviews
- **Why it does not qualify**: Only 2 signal types (need 3), confidence below threshold, and recent movement is a single-day spike
