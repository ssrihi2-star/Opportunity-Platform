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
  rejections: { trend_id: string; trend_name: string; opportunity_type: string; reasons: string[] }[];
};

// ------------------------------------------------------------------ watchlists
/** What a watchlist item follows. Only the matching field is ever sent. */
export type WatchTargetKind =
  | "opportunity"
  | "trend"
  | "company"
  | "technology"
  | "product"
  | "country"
  | "industry"
  | "keyword";

/**
 * A watchlist item carries NO score of its own. When `item_type` is
 * "opportunity" the caller joins `opportunity_id` against the hydrated feed to
 * show the opportunity's real scores; every other type has none by definition.
 */
export type WatchlistItem = {
  id: string;
  created_at: string;
  item_type: WatchTargetKind;
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
  /** The user's filter threshold (0..100), NOT a score. */
  min_score: number | null;
  max_risk_level: string | null;
  created_at: string;
  items: WatchlistItem[];
};

export type WatchlistIn = {
  name: string;
  description: string | null;
  min_score: number | null;
  max_risk_level: string | null;
};

export type WatchlistItemIn = {
  item_type: WatchTargetKind;
  opportunity_id: string | null;
  trend_id: string | null;
  entity_id: string | null;
  country_code: string | null;
  industry: string | null;
  keyword: string | null;
  label: string | null;
};
