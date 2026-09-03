"use client";

import Link from "next/link";
import { useCallback, useEffect, useState } from "react";
import { Guard } from "@/components/Guard";
import { Card, ScoreBar, StatusBadge, vocab } from "@/components/ui";
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
function ValidationBadge({ status }: { status: string }) {
  const { t } = useApp();
  const label =
    status === "live_validated"
      ? t.opportunities.liveBadge
      : status === "unvalidated"
        ? t.opportunities.unvalidatedBadge
        : t.opportunities.demoBadge;
  const critical = status !== "live_validated";
  return (
    <span
      className="rounded px-1.5 py-0.5 text-[10px] font-semibold tracking-wide"
      style={{
        background: critical ? "var(--status-warning)" : "var(--status-good)",
        color: "#fff",
      }}
    >
      {label}
    </span>
  );
}

function Body() {
  const { t, token } = useApp();
  const [data, setData] = useState<Page<Opportunity> | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);
  const [result, setResult] = useState<GenerateResult | null>(null);

  const [sort, setSort] = useState("score");
  const [type, setType] = useState("");
  const [country, setCountry] = useState("");
  const [risk, setRisk] = useState("");
  const [state, setState] = useState("");

  const load = useCallback(() => {
    if (!token) return;
    const params = new URLSearchParams({ sort, limit: "200" });
    if (type) params.set("opportunity_type", type);
    if (country) params.set("country", country);
    if (risk) params.set("risk_level", risk);
    if (state) params.set("state", state);
    api<Page<Opportunity>>(`/opportunities?${params}`, { token })
      .then(setData)
      .catch((e) => setError(e.message));
  }, [token, sort, type, country, risk, state]);

  useEffect(load, [load]);

  async function regenerate() {
    setBusy(true);
    try {
      const res = await api<GenerateResult>("/opportunities/generate", { method: "POST", token });
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

      <Card>
        <p className="mb-3 text-xs text-[var(--text-secondary)]">{t.opportunities.notAdvice}</p>
        {items.length === 0 ? (
          <p className="py-6 text-sm text-[var(--text-secondary)]">{t.opportunities.empty}</p>
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
          <ul className="mt-3 space-y-2 text-sm">
            {result.rejections.map((r, i) => (
              <li key={`${r.trend_id}-${r.opportunity_type}-${i}`}>
                <span className="font-medium">{r.trend_name}</span>{" "}
                <span className="text-xs text-[var(--text-muted)]">
                  [{vocab(t.opportunities.types, r.opportunity_type)}]
                </span>
                <div className="text-xs text-[var(--text-secondary)]">{r.reasons[0]}</div>
              </li>
            ))}
          </ul>
        </Card>
      )}
    </>
  );
}
