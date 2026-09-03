# Risk Methodology

## 1. Risk taxonomy

Every risk flag has `code`, `category`, `severity` (`info|low|medium|high|blocking`),
`rationale`, and zero or more `evidence_item_id`s. A flag without a rationale
cannot be persisted.

### Market risk
`demand_temporary`, `market_too_small`, `seasonal_artifact`, `substitution_risk`,
`price_already_reflects_news`, `cyclical_peak`

### Execution risk
`no_working_product`, `team_unproven`, `capital_intensity`, `operational_difficulty`,
`certification_required`, `after_sales_burden`, `mvp_not_feasible`

### Competition risk
`dominant_incumbent`, `easy_to_copy`, `commoditised`, `channel_locked`,
`local_competitor_exists`

### Regulatory risk
`single_regulation_dependence`, `active_enforcement`, `import_ban_risk`,
`licence_required`, `sanctions_exposure`, `customs_complexity`

### Liquidity / access risk
`thin_liquidity`, `exchange_concentration`, `not_accessible_from_geography`,
`min_order_quantity_high`, `capital_above_user_range`

### Fraud / manipulation risk
`anonymous_founders`, `unverified_contract`, `missing_audit`, `honeypot_behaviour`,
`ponzi_incentives`, `paid_influencer_promotion`, `bot_amplified`,
`wash_trading_suspected`, `unrealistic_claims`, `concentrated_ownership`,
`sudden_unlock_schedule`

### Evidence risk
`single_source_dependence`, `duplicated_evidence`, `stale_evidence`,
`survivorship_bias`, `already_widely_known`, `self_reported_metrics_only`

### Geographic / supply-chain risk
`shipping_fragility`, `supplier_concentration`, `counterfeit_risk`,
`freight_cost_volatility`, `port_or_logistics_constraint`, `fx_or_transfer_restriction`

## 2. Manipulation probability

A deterministic 0–1 estimate computed **before** any LLM involvement:

```
manipulation_probability = weighted_or(
    0.30 * bot_like_account_ratio,
    0.25 * promotion_marker_density,
    0.20 * evidence_duplication_ratio,
    0.15 * volume_price_divergence,
    0.10 * new_account_share
)
```

Each term is 0 when its input is unavailable, and the report states which terms
were unavailable rather than letting absence read as safety.

## 3. Crypto floor rule

Crypto opportunities are hard-clamped: `risk_level >= high`, always. Any of
`anonymous_founders`, `missing_audit`, `unverified_contract`,
`concentrated_ownership`, `honeypot_behaviour`, `no_working_product` promotes the
level to `very_high`.

Meme-coin classification is a blocking label: such assets may be stored and
displayed, but never scored above `MAX_MEME_SCORE` (25) and never surfaced in
"top opportunities".

## 4. Skeptic status ladder

| Status | Meaning |
|---|---|
| `strong_evidence` | multiple independent sources, no high-severity flags, completeness >= 0.7 |
| `continue_research` | promising, with specific gaps named |
| `watch_only` | plausible but unactionable now |
| `insufficient_evidence` | fails completeness or recency |
| `high_speculation` | narrative-driven, thin fundamentals |
| `possible_manipulation` | manipulation probability > 0.5 |
| `reject` | invalidated by evidence, or a blocking flag |

The skeptic can only downgrade. There is no path by which the adversarial module
makes an opportunity look better.


## Phase 4 risk engine

Implementation: `backend/app/analytics/risk_engine.py`, version `RISK_VERSION`.

Eleven categories: market, execution, financial, regulatory, competition,
liquidity, fraud/manipulation, supply chain, geographic, technology, customer
concentration.

Each risk row carries **severity** (how bad), **confidence** (how sure we are it
is real — a different question), the evidence behind it, a plain-language
rationale, and a mitigation where one honestly exists. A risk we are unsure about
is still listed, at lower confidence, rather than dropped.

### The overall level is not an average

```
any blocking risk                -> very_high
three or more high-severity      -> very_high
any high-severity                -> high
three or more medium             -> moderate
any medium                       -> moderate
none                             -> low
```

Averaging is how a fatal flaw gets diluted by nine comfortable ones. A company
with excellent margins, a strong position and an active fraud investigation is
not "moderate risk".

### Floors

`CRYPTO_MIN_RISK = "high"`. Crypto can never be classified low risk — not as a
default that evidence could overturn, but as a floor applied after the
calculation and reported in the output rather than applied silently. The router
also refuses to examine a crypto asset as any other opportunity type, because
re-labelling is the obvious way to dodge a floor.

### Every penalty has a matching risk

A scoring penalty without an explained risk row would be unaccountable: the
reader would see points deducted with no statement of what for. Every flag raised
anywhere in the pipeline becomes a risk row with a rationale, and a test asserts
it.
