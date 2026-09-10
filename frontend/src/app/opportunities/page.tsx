"use client";

import Link from "next/link";
import { useCallback, useEffect, useState } from "react";
import { Guard } from "@/components/Guard";
import {
  AnalysisModeBadge,
  AnalysisModeSelect,
  Card,
  ScoreBar,
  StatusBadge,
  ValidationBadge,
  vocab,
} from "@/components/ui";
import { api } from "@/lib/api";
import { useApp } from "@/lib/providers";
import type { GenerateResult, Opportunity, Page } from "@/lib/types";

export default function OpportunitiesPage() {
  return (
    <Guard>
      <Body />
    </Guard>
  );
}

const TYPES = ["business", "import_distribution", "public_investment", "crypto"];
const RISKS = ["low", "moderate", "high", "very_high"];
const STATES = [
  "candidate",
  "researching",
  "watchlist",
  "promising",
  "strong_evidence",
  "weakening",
  "invalidated",
  "archived",
];

/** Where the evidence came from. Shown on every card, without exception. */

function Body() {
  const { t, token } = useApp();
  const [data, setData] = useState<Page<Opportunity> | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);
  const [result, setResult] = useState<GenerateResult | null>(null);

  const [sort, setSort] = useState("score");
  // Which evidence the listed candidates were generated from. Each mode is its
  // own set of stored candidates, not a view over one shared set.
  const [mode, setMode] = useState("demo_inclusive");
  const [type, setType] = useState("");
  const [country, setCountry] = useState("");
  const [risk, setRisk] = useState("");
  const [state, setState] = useState("");

  const load = useCallback(() => {
    if (!token) return;
    const params = new URLSearchParams({ sort, limit: "200", analysis_mode: mode });
    if (type) params.set("opportunity_type", type);
    if (country) params.set("country", country);
    if (risk) params.set("risk_level", risk);
    if (state) params.set("state", state);
    api<Page<Opportunity>>(`/opportunities?${params}`, { token })
      .then(setData)
      .catch((e) => setError(e.message));
  }, [token, sort, mode, type, country, risk, state]);

  useEffect(load, [load]);

  async function regenerate() {
    setBusy(true);
    try {
      const res = await api<GenerateResult>(
        `/opportunities/generate?analysis_mode=${mode}`,
        { method: "POST", token },
      );
      setResult(res);
      load();
    } catch (e) {
      setError(e instanceof Error ? e.message : "failed");
    } finally {
      setBusy(false);
    }
  }

  const countries = Array.from(
    new Set((data?.items ?? []).map((x) => x.country).filter(Boolean)),
  ).sort() as string[];

  if (error) return <p style={{ color: "var(--status-critical)" }}>■ {error}</p>;

  const items = data?.items ?? [];

  return (
    <>
      <div className="flex flex-wrap items-start gap-3">
        <div className="me-auto">
          <h1 className="text-xl font-semibold">{t.opportunities.title}</h1>
          <p className="max-w-3xl text-sm text-[var(--text-secondary)]">
            {t.opportunities.subtitle}
          </p>
        </div>
        <button onClick={regenerate} disabled={busy} className="btn text-sm">
          {busy ? t.opportunities.generating : t.opportunities.generate}
        </button>
      </div>

      <div className="flex flex-wrap items-end gap-3 text-sm">
        <AnalysisModeSelect value={mode} onChange={setMode} />
        <label className="text-xs">
          {t.opportunities.sortBy}
          <select
            value={sort}
            onChange={(e) => setSort(e.target.value)}
            className="field mt-1 block text-sm"
          >
            <option value="score">{t.opportunities.sortScore}</option>
            <option value="confidence">{t.opportunities.sortConfidence}</option>
            <option value="relevance">{t.opportunities.sortRelevance}</option>
            <option value="lowest_risk">{t.opportunities.sortLowestRisk}</option>
            <option value="fastest_trend">{t.opportunities.sortFastestTrend}</option>
            <option value="newest">{t.opportunities.sortNewest}</option>
          </select>
        </label>
        <label className="text-xs">
          {t.opportunities.filterType}
          <select
            value={type}
            onChange={(e) => setType(e.target.value)}
            className="field mt-1 block text-sm"
          >
            <option value="">{t.opportunities.all}</option>
            {TYPES.map((x) => (
              <option key={x} value={x}>
                {vocab(t.opportunities.types, x)}
              </option>
            ))}
          </select>
        </label>
        <label className="text-xs">
          {t.opportunities.filterCountry}
          <select
            value={country}
            onChange={(e) => setCountry(e.target.value)}
            className="field mt-1 block text-sm"
          >
            <option value="">{t.opportunities.all}</option>
            {countries.map((c) => (
              <option key={c} value={c}>
                {c}
              </option>
            ))}
          </select>
        </label>
        <label className="text-xs">
          {t.opportunities.filterRisk}
          <select
            value={risk}
            onChange={(e) => setRisk(e.target.value)}
            className="field mt-1 block text-sm"
          >
            <option value="">{t.opportunities.all}</option>
            {RISKS.map((x) => (
              <option key={x} value={x}>
                {vocab(t.opportunities.risks_levels, x)}
              </option>
            ))}
          </select>
        </label>
        <label className="text-xs">
          {t.opportunities.filterState}
          <select
            value={state}
            onChange={(e) => setState(e.target.value)}
            className="field mt-1 block text-sm"
          >
            <option value="">{t.opportunities.all}</option>
            {STATES.map((x) => (
              <option key={x} value={x}>
                {vocab(t.opportunities.states, x)}
              </option>
            ))}
          </select>
        </label>
      </div>

      {result?.insufficient_live_evidence && (
        <Card>
          <h2 className="text-sm font-semibold">{t.analysis.insufficient}</h2>
          <p className="mt-1 text-sm text-[var(--text-secondary)]">
            {result.detail ?? t.analysis.noLiveOpportunities}
          </p>
        </Card>
      )}

      <Card actions={<AnalysisModeBadge mode={mode} />}>
        <p className="mb-1 text-xs text-[var(--text-muted)]">
          {mode === "live_only" ? t.analysis.liveOnlyHint : t.analysis.demoInclusiveHint}
        </p>
        <p className="mb-3 text-xs text-[var(--text-secondary)]">{t.opportunities.notAdvice}</p>
        {items.length === 0 ? (
          <p className="py-6 text-sm text-[var(--text-secondary)]">
            {mode === "live_only" ? t.analysis.noLiveOpportunities : t.opportunities.empty}
          </p>
        ) : (
          <div className="overflow-x-auto">
            <table className="w-full text-sm">
              <thead>
                <tr className="border-b border-[var(--border)] text-start text-xs text-[var(--text-secondary)]">
                  <th className="py-2 text-start">{t.opportunities.title}</th>
                  <th className="py-2 ps-3 text-start">{t.opportunities.type}</th>
                  <th className="py-2 ps-3 text-start">{t.opportunities.score}</th>
                  <th className="py-2 ps-3 text-start">{t.opportunities.confidence}</th>
                  <th className="py-2 ps-3 text-start">{t.opportunities.relevance}</th>
                  <th className="py-2 ps-3 text-start">{t.opportunities.risk}</th>
                  <th className="py-2 ps-3 text-start">{t.opportunities.trendScore}</th>
                  <th className="py-2 ps-3 text-start">{t.opportunities.status}</th>
                  <th className="py-2 ps-3 text-start">{t.opportunities.geography}</th>
                  <th className="py-2 ps-3 text-start">{t.opportunities.detected}</th>
                </tr>
              </thead>
              <tbody>
                {items.map((row) => (
                  <tr key={row.id} className="border-b border-[var(--border)] align-top">
                    <td className="py-2 pe-3">
                      <Link href={`/opportunities/${row.id}`} className="link">
                        {row.title}
                      </Link>
                      <div className="mt-1 flex flex-wrap items-center gap-1.5">
                        <AnalysisModeBadge mode={row.analysis_mode} />
                        <ValidationBadge status={row.validation_status} />
                        {row.trend_name && (
                          <span className="text-[11px] text-[var(--text-muted)]">
                            {row.trend_name}
                          </span>
                        )}
                      </div>
                    </td>
                    <td className="py-2 ps-3 text-[var(--text-secondary)]">
                      {vocab(t.opportunities.types, row.opportunity_type)}
                    </td>
                    <td className="py-2 ps-3">
                      <ScoreBar value={row.opportunity_score} />
                    </td>
                    <td className="py-2 ps-3">
                      <ScoreBar value={row.confidence} />
                    </td>
                    <td className="py-2 ps-3">
                      <ScoreBar value={row.user_relevance ?? 0} />
                    </td>
                    <td className="py-2 ps-3">
                      <StatusBadge
                        status={`risk_${row.risk_level}`}
                        label={vocab(t.opportunities.risks_levels, row.risk_level)}
                      />
                    </td>
                    <td className="py-2 ps-3 tabular" dir="ltr">
                      {row.trend_score === null ? "—" : row.trend_score.toFixed(0)}
                    </td>
                    <td className="py-2 ps-3">
                      <StatusBadge
                        status={row.state}
                        label={vocab(t.opportunities.states, row.state)}
                      />
                    </td>
                    <td className="py-2 ps-3 text-[var(--text-secondary)]">
                      {row.country ?? row.geo_scope}
                    </td>
                    <td className="py-2 ps-3 text-xs text-[var(--text-secondary)]" dir="ltr">
                      {row.detected_at.slice(0, 10)}
                    </td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        )}
      </Card>

      {result && result.rejections.length > 0 && (
        <Card>
          <h2 className="text-sm font-semibold">{t.opportunities.refused}</h2>
          <p className="mt-1 text-xs text-[var(--text-secondary)]">
            {t.opportunities.refusedNote}
          </p>
          <div className="mt-4 space-y-4">
            {result.rejections.map((r, i) => (
              <div
                key={`${r.trend_id}-${r.opportunity_type}-${i}`}
                className="rounded-lg border border-[var(--border)] bg-[var(--surface-variant)] p-4"
              >
                <div className="flex items-start justify-between gap-3">
                  <div className="flex-1">
                    <div className="flex items-center gap-2">
                      <StatusBadge status={r.trend_state} />
                      <h3 className="text-sm font-semibold">{r.trend_name}</h3>
                      <span className="rounded bg-[var(--surface-muted)] px-2 py-0.5 text-xs text-[var(--text-secondary)]">
                        {t.opportunities.researchBrief}
                      </span>
                    </div>
                    <div className="mt-1 text-xs text-[var(--text-muted)]">
                      [{vocab(t.opportunities.types, r.opportunity_type)}]
                    </div>
                  </div>
                  <a
                    href={`/trends/${r.trend_id}`}
                    className="rounded border border-[var(--border)] px-3 py-1 text-xs hover:bg-[var(--surface-muted)]"
                  >
                    {t.opportunities.viewTrendDetail}
                  </a>
                </div>

                <div className="mt-3 grid grid-cols-2 gap-x-4 gap-y-2 text-xs">
                  <div>
                    <span className="text-[var(--text-muted)]">{t.opportunities.trendScore}: </span>
                    <span className="font-medium">{r.trend_score.toFixed(1)}</span>
                  </div>
                  <div>
                    <span className="text-[var(--text-muted)]">{t.opportunities.confidence}: </span>
                    <span className="font-medium">{r.trend_confidence.toFixed(1)}</span>
                  </div>
                  <div>
                    <span className="text-[var(--text-muted)]">
                      {t.opportunities.signalTypes}:{" "}
                    </span>
                    <span className="font-medium">{r.distinct_signal_types}</span>
                  </div>
                  <div>
                    <span className="text-[var(--text-muted)]">{t.opportunities.sources}: </span>
                    <span className="font-medium">{r.independent_source_count}</span>
                  </div>
                  <div>
                    <span className="text-[var(--text-muted)]">
                      {t.opportunities.observations}:{" "}
                    </span>
                    <span className="font-medium">{r.observation_count}</span>
                  </div>
                  <div>
                    <span className="text-[var(--text-muted)]">
                      {t.opportunities.historyDays}:{" "}
                    </span>
                    <span className="font-medium">{r.history_days}</span>
                  </div>
                </div>

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

                <div className="mt-3">
                  <div className="text-xs font-medium text-[var(--text-muted)]">
                    {t.opportunities.observedChange}
                  </div>
                  <div className="mt-1 text-xs">
                    {r.direction === "unknown" ? (
                      <span>{t.opportunities.directionNotMeasured}</span>
                    ) : (
                      <div className="space-y-1">
                        <StatusBadge
                          status={
                            r.direction === "rising"
                              ? "rising"
                              : r.direction === "declining"
                                ? "declining"
                                : "flat"
                          }
                          label={
                            r.direction === "rising"
                              ? t.opportunities.directionRising
                              : r.direction === "declining"
                                ? t.opportunities.directionDeclining
                                : t.opportunities.directionFlat
                          }
                        />
                        {/* Show measurement details if available */}
                        {Object.keys(r.growth_metrics).length > 0 && (
                          <div className="ml-4 text-xs text-[var(--text-secondary)]">
                            {Object.entries(r.growth_metrics).map(([period, value]) => {
                              const periodLabel = period.replace("growth_", "");
                              const sign = value > 0 ? "+" : "";
                              return (
                                <div key={period}>
                                  {periodLabel}: {sign}
                                  {value.toFixed(1)}%
                                </div>
                              );
                            })}
                          </div>
                        )}
                      </div>
                    )}
                  </div>
                </div>

                <div className="mt-3">
                  <div className="text-xs font-medium text-[var(--text-muted)]">
                    {t.opportunities.evidencePresent}
                  </div>
                  {r.evidence_summary.length > 0 ? (
                    <div className="mt-1 flex flex-wrap gap-1">
                      {r.evidence_summary.map((ev, j) => (
                        <span
                          key={j}
                          className="rounded bg-[var(--surface-muted)] px-2 py-0.5 text-xs"
                          title={`${ev.signal_type} (${ev.signal_class}) from ${ev.source_group}: ${ev.observation_count} observations`}
                        >
                          {ev.signal_type} ({ev.observation_count}, {ev.source_group})
                        </span>
                      ))}
                    </div>
                  ) : (
                    <div className="mt-1 text-xs text-[var(--text-secondary)]">
                      {t.opportunities.noEvidenceSummary}
                    </div>
                  )}
                </div>

                <div className="mt-3">
                  <div className="text-xs font-medium text-[var(--text-muted)]">
                    {t.opportunities.evidenceMissing}
                  </div>
                  <ul className="mt-1 space-y-1 text-xs text-[var(--text-secondary)]">
                    {r.reasons.map((reason, j) => (
                      <li key={j}>• {reason}</li>
                    ))}
                  </ul>
                </div>
              </div>
            ))}
          </div>
        </Card>
      )}
    </>
  );
}
