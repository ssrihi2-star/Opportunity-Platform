export type Overview = {
  counts: Record<string, number>;
  fastest_growing: {
    signal_id: string;
    entity: string;
    source: string;
    signal_type: string;
    signal_class: string;
    is_proxy: boolean;
    pct_change_window: number;
    acceleration: number | null;
    zscore: number | null;
    is_anomaly: boolean;
    strength: number;
    observations: number;
  }[];
  source_health: {
    slug: string;
    name: string;
    adapter_key: string;
    enabled: boolean;
    status: string;
    reliability: number;
    consecutive_failures: number;
    freshness_hours: number | null;
  }[];
  recent_activity: {
    source: string;
    status: string;
    started_at: string;
    fetched: number;
    stored: number;
    duplicates: number;
    http_requests: number;
    error: string | null;
  }[];
  disclaimer: string;
};

export type SignalRow = {
  id: string;
  entity_id: string;
  entity_name: string | null;
  source_slug: string | null;
  signal_type: string;
  signal_class: string;
  geo_scope: string;
  source_id: string;
  unit: string | null;
  is_proxy: boolean;
};

export type Observation = {
  id: string;
  observed_at: string;
  value: number;
  previous_value: number | null;
  pct_change: number | null;
  confidence: number;
  source_reliability: number;
  is_proxy: boolean;
};

export type SeriesStats = {
  n: number;
  pct_change_last: number | null;
  pct_change_window: number | null;
  moving_average: number | null;
  zscore_last: number | null;
  ewma_last: number | null;
  acceleration: number | null;
  changepoint_index: number | null;
  is_anomaly: boolean;
  direction: string;
  strength: number;
  note: string;
};

export type SignalDetail = SignalRow & { observations: Observation[]; stats: SeriesStats };

export type Source = {
  id: string;
  slug: string;
  name: string;
  adapter_key: string;
  source_group: string;
  source_class: string;
  status: string;
  enabled: boolean;
  schedule_cron: string;
  rate_limit_per_minute: number;
  reliability: number;
  consecutive_failures: number;
  last_run_at: string | null;
  last_success_at: string | null;
  notes: string | null;
  config: Record<string, unknown>;
};

export type AdapterInfo = {
  adapter_key: string;
  requires_network: boolean;
  requires_credentials: string[];
  documented_rate_limit: string;
  default_rate_limit_per_minute: number;
  default_source_class: string;
  doc: string;
};

export type SourceRun = {
  id: string;
  status: string;
  trigger: string;
  started_at: string;
  finished_at: string | null;
  records_fetched: number;
  records_stored: number;
  records_duplicate: number;
  records_rejected: number;
  observations_written: number;
  http_requests: number;
  error: string | null;
  duration_ms: number | null;
};

export type SourceHealth = {
  source_id: string;
  slug: string;
  enabled: boolean;
  status: string;
  reliability: number;
  consecutive_failures: number;
  last_success_at: string | null;
  freshness_hours: number | null;
  adapter_available: boolean;
  requires_network: boolean;
  missing_credentials: string[];
  documented_rate_limit: string;
  probe: string | null;
  probe_healthy: boolean | null;
  last_run: SourceRun | null;
};

export type Credential = {
  key: string;
  hint: string | null;
  rotated_at: string | null;
  created_at: string;
};

export type RunResult = {
  run_id: string;
  status: string;
  fetched: number;
  stored: number;
  duplicates: number;
  rejected: number;
  observations: number;
  http_requests: number;
  not_modified: number;
  error: string | null;
};

export type Preferences = {
  countries: string[];
  industries: string[];
  max_risk_level: string;
  min_confidence: number;
  capital_min_usd: number;
  capital_max_usd: number;
  time_horizon: string;
};

export type Page<T> = { items: T[]; total: number; limit: number; offset: number };

export type Trend = {
  id: string;
  subject_type: string;
  entity_id: string | null;
  topic_id: string | null;
  name: string;
  category: string | null;
  geo_scope: string;
  /** Which evidence this evaluation was computed from: "live_only" | "demo_inclusive". */
  analysis_mode: string;
  state: string;
  stage: string;
  trend_score: number;
  confidence: number;
  peak_score: number;
  peak_score_at: string | null;
  first_detected_at: string;
  last_evaluated_at: string;
  last_confirmation_at: string | null;
  independent_source_count: number;
  distinct_signal_types: number;
  observation_count: number;
  missing_observation_count: number;
  history_days: number;
  is_spike: boolean;
  is_seasonal: boolean;
  warnings: string[];
};

export type TrendComponent = { points: number; max: number; why: string };

export type TrendSignal = {
  signal_id: string;
  source_id: string;
  source_slug: string | null;
  /** True when this series came from a source that actually contacts a live upstream. */
  is_live_source: boolean;
  source_group: string;
  signal_type: string;
  growth_30d: number | null;
  acceleration: number | null;
  observation_count: number;
  is_proxy: boolean;
  contribution: number;
};

export type TrendPoint = {
  at: string;
  value: number | null;
  status: string;
  signal_type: string;
  source_slug: string | null;
  unit: string | null;
  currency: string | null;
};

export type TrendSnapshot = {
  evaluated_at: string;
  trend_score: number;
  confidence: number;
  stage: string;
  state: string;
};

export type EvidenceEvent = {
  at: string;
  kind: string;
  detail: string;
  url: string | null;
  source_slug: string | null;
};

export type TrendDetail = Trend & {
  components: Record<string, TrendComponent>;
  penalties: Record<string, number>;
  metrics: Record<string, unknown>;
  explanation: string | null;
  signals: TrendSignal[];
  series: TrendPoint[];
  history: TrendSnapshot[];
  evidence: EvidenceEvent[];
  related_entities: { id: string; name: string; type: string; external_ids: Record<string, unknown> }[];
};

export type MatchCandidate = {
  id: string;
  observed_name: string;
  entity_type: string;
  candidate_entity_id: string;
  candidate_entity_name: string | null;
  candidate_external_ids: Record<string, unknown>;
  created_entity_id: string | null;
  confidence: number;
  reason: string;
  decision: string;
  decided_at: string | null;
  note: string | null;
  created_at: string;
};

export type Topic = {
  id: string;
  label: string;
  description: string | null;
  category: string | null;
  keywords: string[];
  first_seen_at: string | null;
  last_seen_at: string | null;
  label_is_ai_generated: boolean;
  entity_names: string[];
};

// ------------------------------------------------------------------ phase 4
export type Opportunity = {
  id: string;
  slug: string;
  title: string;
  summary: string | null;
  opportunity_type: string;
  category: string | null;
  industry: string | null;
  geo_scope: string;
  country: string | null;
  state: string;
  validation_status: string;
  /** Which evidence this candidate was generated from: "live_only" | "demo_inclusive". */
  analysis_mode: string;
  maturity_stage: string;
  risk_level: string;
  /** THE GLOBAL OPPORTUNITY SCORE — identical for every user. */
  opportunity_score: number;
  confidence: number;
  peak_score: number;
  detected_at: string;
  last_evaluated_at: string | null;
  independent_source_count: number;
  distinct_signal_types: number;
  evidence_count: number;
  geographic_gap: number | null;
  adoption_attention_ratio: number | null;
  algorithm_version: string;
  /** THE USER RELEVANCE SCORE — this viewer only. Never merged with the above. */
  user_relevance: number | null;
  best_path: string | null;
  outside_profile: boolean;
  relevance_version: string | null;
  warnings: string[];
  trend_id: string | null;
  trend_score: number | null;
  trend_confidence: number | null;
  trend_stage: string | null;
  trend_name: string | null;
};

export type OpportunityRisk = {
  code: string;
  category: string;
  severity: string;
  confidence: number;
  rationale: string;
  mitigation: string | null;
  is_blocking: boolean;
};

export type OpportunityCondition = {
  id: string;
  kind: string;
  description: string;
  measurable: Record<string, unknown>;
  state: string;
  checked_at: string | null;
};

export type Participation = {
  kind: string;
  description: string;
  difficulty: string;
  capital_hint: string | null;
};

export type Skeptic = {
  strongest_counterargument: string | null;
  counterarguments: string[];
  missing_evidence: string[];
  risk_flags: string[];
  alternative_explanations: string[];
  too_late_reasons: string[];
  inaccessible_reasons: string[];
  manipulation_probability: number;
  confidence_reduction: number;
  status: string;
  questions_asked: string[];
  prompt_version: string;
  reviewed_at: string | null;
};

export type OpportunityEvidence = {
  kind: string;
  signal_type: string;
  source_slug: string;
  source_group: string;
  reliability: number;
  is_proxy: boolean;
  growth_30d: number | null;
  observation_count: number;
  signal_id: string | null;
};

export type OpportunityDecision = {
  id: string;
  interest: string;
  note: string | null;
  decided_at: string;
  score_at_decision: number;
  confidence_at_decision: number;
  risk_at_decision: string;
  relevance_at_decision: number | null;
  state_at_decision: string;
  algorithm_version: string;
};

export type OpportunityScoreSnapshot = {
  formula_version: string;
  raw_score: number;
  penalty_total: number;
  adjusted_score: number;
  confidence: number;
  risk_level: string;
  computed_at: string;
};

export type OpportunityDetail = Opportunity & {
  thesis: string | null;
  counter_thesis: string | null;
  mechanism: string | null;
  why_early: string[];
  missing_evidence: string[];
  next_research_steps: string[];
  analysis: Record<string, unknown>;
  components: Record<string, TrendComponent>;
  penalties: Record<string, number>;
  confidence_parts: Record<string, number>;
  user_relevance_parts: Record<string, { points: number; max: number; why: string }>;
  path_relevance: Record<string, number>;
  relevance_notes: string[];
  metrics: Record<string, unknown>;
  raw_score: number;
  penalty_total: number;
  capital_required_usd: number | null;
  geographic_gap_parts: Record<string, unknown>;
  adoption_attention_parts: Record<string, unknown>;
  risks: OpportunityRisk[];
  conditions: OpportunityCondition[];
  participation: Participation[];
  evidence: OpportunityEvidence[];
  skeptic: Skeptic | null;
  decisions: OpportunityDecision[];
  history: OpportunityScoreSnapshot[];
};

export type ReportSection = {
  key: string;
  title: string;
  body: string | null;
  items: Record<string, unknown>[];
  generated: boolean;
};

export type OpportunityReport = {
  opportunity_id: string;
  sections: ReportSection[];
  narrated: boolean;
  narration_note: string | null;
};

export type GenerateResult = {
  created: number;
  updated: number;
  rejected: number;
  algorithm_version: string;
  validation_status: string;
  analysis_mode: string;
  /** A live-only run that produced nothing because eligible live evidence was too thin. */
  insufficient_live_evidence: boolean;
  detail: string | null;
  rejections: {
    trend_id: string;
    trend_name: string;
    opportunity_type: string;
    reasons: string[];
    trend_score: number;
    trend_confidence: number;
    trend_state: string;
    trend_stage: string;
    observation_count: number;
    history_days: number;
    distinct_signal_types: number;
    independent_source_count: number;
    direction: string;
    growth_metrics: { [key: string]: number };
    is_spike: boolean;
    evidence_summary: {
      signal_type: string;
      signal_class: string;
      source_group: string;
      observation_count: number;
      growth_30d: number | null;
    }[];
  }[];
};

// ------------------------------------------------------------------ phase 5
// Watchlists. NOTE WHAT IS ABSENT: a watchlist and its items carry NO
// opportunity_score, NO user_relevance, NO validation_status and NO state.
// `min_score` below is the user's own filter threshold, not a score, and must
// never be rendered through a score-shaped element. Real scores only ever come
// from hydrating against Opportunity objects fetched from /for-you.

/** The four risk levels exactly as the backend enum spells them.
 *  "moderate", never "medium" — the backend rejects "medium". */
export const RISK_LEVELS = ["low", "moderate", "high", "very_high"] as const;
export type RiskLevel = (typeof RISK_LEVELS)[number];

/** Every kind of thing a watchlist can follow. */
export const WATCH_ITEM_TYPES = [
  "opportunity",
  "trend",
  "company",
  "technology",
  "product",
  "country",
  "industry",
  "keyword",
] as const;
export type WatchItemType = (typeof WATCH_ITEM_TYPES)[number];

export type WatchlistItem = {
  id: string;
  created_at: string;
  item_type: string;
  opportunity_id: string | null;
  trend_id: string | null;
  entity_id: string | null;
  country_code: string | null;
  industry: string | null;
  keyword: string | null;
  label: string | null;
};

export type Watchlist = {
  id: string;
  name: string;
  description: string | null;
  /** THE USER'S FILTER THRESHOLD, 0-100. NOT a score. Never render as one. */
  min_score: number | null;
  max_risk_level: string | null;
  created_at: string;
  items: WatchlistItem[];
};

/** Request body for POST / PUT /me/watchlists.
 *  PUT applies exclude_unset server-side, so it behaves as PATCH: a key must be
 *  present with an explicit null to clear it, and `name` is always required. */
export type WatchlistIn = {
  name: string;
  description: string | null;
  min_score: number | null;
  max_risk_level: RiskLevel | null;
};

/** Request body for POST /me/watchlists/{id}/items.
 *  The backend does NO cross-field validation, so the form is what guarantees
 *  that item_type and its identifying field agree. */
export type WatchlistItemIn = {
  item_type: WatchItemType;
  opportunity_id?: string | null;
  trend_id?: string | null;
  entity_id?: string | null;
  country_code?: string | null;
  industry?: string | null;
  keyword?: string | null;
  label?: string | null;
};

// --------------------------------------------------------------- notifications
export type DigestFrequency = "off" | "daily" | "weekly";

/**
 * The profile fields this UI reads and writes.
 *
 * `PUT /me/profile` applies only the fields present in the body, so the
 * notifications panel sends `digest_frequency` alone and leaves the rest of the
 * profile — countries, capital, risk tolerance, skills — exactly as it was.
 */
export type Profile = {
  digest_frequency: DigestFrequency;
  [key: string]: unknown;
};

export type ChannelLink = {
  id: string;
  channel: string;
  verified: boolean;
  verified_at: string | null;
  /** Present only in the response that created the link. Never echoed back. */
  link_code?: string | null;
  instructions?: string | null;
  /**
   * The deadline the webhook itself enforces, straight from the row. NULL once
   * the link is verified, so the UI shows a real expiry or none at all.
   */
  link_code_expires_at?: string | null;
};

export type DigestItem = {
  opportunity_id: string;
  title: string;
  kind: string;
  summary: string | null;
};

export type DigestAlert = { title: string; body: string; at: string };

export type DigestSections = {
  period?: { start?: string; end?: string; frequency?: string; key?: string; timezone?: string };
  alerts?: DigestAlert[];
  watchlist_changes?: DigestItem[];
  other_changes?: DigestItem[];
  note?: string;
  empty_note?: string;
};

export type Digest = {
  id: string;
  frequency: string;
  period_start: string;
  period_end: string;
  item_count: number;
  sections: DigestSections;
  generated_at: string;
};
