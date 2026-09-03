# Signal Taxonomy

A **signal** is a definition: *(entity, signal_type, geo_scope, source)*.
A **signal_observation** is one dated measurement of that definition.

## Classes

| Class | Signal types |
|---|---|
| Attention | `search_growth`, `wiki_pageview_growth`, `social_discussion_growth`, `media_coverage_growth`, `newsletter_mentions` |
| Developer | `github_stars`, `github_forks`, `github_contributors`, `github_commit_velocity`, `github_issue_velocity`, `package_downloads`, `stackoverflow_questions`, `model_downloads` |
| Talent | `job_posting_growth`, `job_title_emergence`, `salary_premium` |
| Capital | `funding_round`, `funding_total_growth`, `valuation_change`, `institutional_activity`, `insider_buying` |
| Product | `product_launch`, `app_ranking_growth`, `website_traffic_growth`, `review_volume_growth`, `feature_request_frequency` |
| Commercial | `revenue_acceleration`, `transaction_growth`, `customer_count_growth`, `contract_award` |
| Market | `trading_volume_anomaly`, `price_momentum`, `short_interest_change`, `liquidity_change` |
| Industrial | `manufacturing_expansion`, `capex_announcement`, `patent_activity`, `supply_shortage`, `lead_time_change` |
| Trade | `import_growth`, `export_growth`, `hs_code_volume_change`, `freight_rate_change`, `supplier_count_change` |
| Demand pain | `customer_complaint_frequency`, `unmet_need_mentions`, `churn_signal` |
| Policy | `regulatory_catalyst`, `government_subsidy`, `tariff_change`, `standard_adoption` |
| Macro | `policy_rate`, `inflation_rate`, `industrial_production`, `producer_price_index`, `housing_starts`, `unemployment_rate`, `macro_indicator` |
| Filing | `sec_filing_activity`, `insider_transaction`, `annual_report_filed`, `quarterly_report_filed` |

## Observation fields

`signal_id`, `observed_at`, `period_start`, `period_end`, `value`, `status`,
`currency`, `previous_value`, `pct_change`, `confidence`, `source_reliability`,
`raw_record_id`, `collected_at`, `method`, `is_proxy`.

### `status` — missing data is not zero

| Status | Meaning | `value` |
|---|---|---|
| `ok` | a real measurement | the number |
| `missing` | the source published nothing for this period (FRED's `"."`, an unreleased month) | `NULL` |
| `failed` | the fetch itself failed | `NULL` |

Growth calculations skip non-`ok` rows; their count is shown on the trend page and
penalised in the Trend Score and the Confidence Score. A failed fetch stored as `0`
would look like a collapse that never happened, which is exactly the kind of
invented event this system exists to avoid.

### `currency` — the original unit is kept

A monetary observation stores its amount together with the ISO-4217 code the source
published. Nothing is silently converted to USD, and a converted figure never
replaces the original. Conversion, when it arrives, will be a display-time
calculation over the stored original.

`is_proxy` is surfaced everywhere the number appears — in the signals table, on the
detail page, and in the overview ranking. A proxy signal can never alone satisfy
the requirement for its class in the opportunity gate.

## Which signals count as adoption

`ADOPTION_SIGNALS` in `app/analytics/signal_types.py` is the list the scoring
engine treats as evidence of *real adoption* rather than attention: contributors,
commit velocity, package and model downloads, customer and transaction counts,
revenue, app ranking, import/export volumes, job postings.

Everything else can corroborate an opportunity but cannot earn adoption points.
This is the single most load-bearing distinction in the product: it is what stops
a burst of forum posts from looking like a business.

Phase 4 leans on it twice more: the `real_adoption` component of the Opportunity
Score is zero for attention-only evidence, and the `attention_without_adoption`
penalty fires when interest grows more than 2.5× faster than use. The
experimental adoption-to-attention ratio measures the same relationship as a
number in its own right.

## Direction semantics

Each signal type declares whether an increase is bullish, bearish or
context-dependent (`DIRECTION`). `supply_shortage` rising is an opportunity for a
distributor and a risk for a manufacturer, so it is `context` and the category
analyser must resolve the sign.

## Quality weighting

```
observation_weight = source_reliability * confidence * (0.6 if is_proxy else 1.0)
                     * recency_decay(observed_at, half_life=45d)
```
