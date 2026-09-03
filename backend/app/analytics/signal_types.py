"""Signal taxonomy in code. Mirrors docs/signal-taxonomy.md."""

from __future__ import annotations

SIGNAL_CLASSES: dict[str, list[str]] = {
    "attention": [
        "search_growth",
        "wiki_pageview_growth",
        "social_discussion_growth",
        "media_coverage_growth",
        "newsletter_mentions",
    ],
    "developer": [
        "github_stars",
        "github_forks",
        "github_contributors",
        "github_commit_velocity",
        "github_issue_velocity",
        "package_downloads",
        "stackoverflow_questions",
        "model_downloads",
    ],
    "talent": ["job_posting_growth", "job_title_emergence", "salary_premium"],
    "capital": [
        "funding_round",
        "funding_total_growth",
        "valuation_change",
        "institutional_activity",
        "insider_buying",
    ],
    "product": [
        "product_launch",
        "app_ranking_growth",
        "website_traffic_growth",
        "review_volume_growth",
        "feature_request_frequency",
    ],
    "commercial": [
        "revenue_acceleration",
        "transaction_growth",
        "customer_count_growth",
        "contract_award",
    ],
    "market": [
        "trading_volume_anomaly",
        "price_momentum",
        "short_interest_change",
        "liquidity_change",
    ],
    "industrial": [
        "manufacturing_expansion",
        "capex_announcement",
        "patent_activity",
        "supply_shortage",
        "lead_time_change",
    ],
    "trade": [
        "import_growth",
        "export_growth",
        "hs_code_volume_change",
        "freight_rate_change",
        "supplier_count_change",
    ],
    "demand_pain": ["customer_complaint_frequency", "unmet_need_mentions", "churn_signal"],
    "policy": ["regulatory_catalyst", "government_subsidy", "tariff_change", "standard_adoption"],
    "macro": [
        "policy_rate",
        "inflation_rate",
        "industrial_production",
        "producer_price_index",
        "housing_starts",
        "unemployment_rate",
        "macro_indicator",
    ],
    "filing": [
        "sec_filing_activity",
        "insider_transaction",
        "annual_report_filed",
        "quarterly_report_filed",
    ],
    "manual": [],
}

CLASS_OF: dict[str, str] = {stype: cls for cls, types in SIGNAL_CLASSES.items() for stype in types}

# Signals that count as evidence of *real adoption* rather than attention.
ADOPTION_SIGNALS: frozenset[str] = frozenset(
    {
        "github_contributors",
        "github_commit_velocity",
        "package_downloads",
        "model_downloads",
        "customer_count_growth",
        "transaction_growth",
        "revenue_acceleration",
        "app_ranking_growth",
        "import_growth",
        "export_growth",
        "hs_code_volume_change",
        "job_posting_growth",
    }
)

# up = rising is bullish, down = rising is bearish, context = depends on the role
DIRECTION: dict[str, str] = {
    **{s: "up" for types in SIGNAL_CLASSES.values() for s in types},
    "supply_shortage": "context",
    "lead_time_change": "context",
    "customer_complaint_frequency": "context",
    "churn_signal": "down",
    "short_interest_change": "context",
    "freight_rate_change": "context",
    "tariff_change": "context",
    "policy_rate": "context",
    "inflation_rate": "context",
    "unemployment_rate": "down",
    "macro_indicator": "context",
}

# Signal types that are inherently a stand-in for something they cannot measure.
PROXY_SIGNALS: frozenset[str] = frozenset(
    {"wiki_pageview_growth", "search_growth", "media_coverage_growth", "social_discussion_growth"}
)


def class_of(signal_type: str) -> str:
    return CLASS_OF.get(signal_type, "manual")


def is_adoption_signal(signal_type: str) -> bool:
    return signal_type in ADOPTION_SIGNALS


def is_proxy_signal(signal_type: str) -> bool:
    return signal_type in PROXY_SIGNALS
