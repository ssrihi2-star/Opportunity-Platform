"use client";

import Link from "next/link";
import { useCallback, useEffect, useState } from "react";
import { Guard } from "@/components/Guard";
import { Card, ScoreBar, StatusBadge, vocab } from "@/components/ui";
import { api } from "@/lib/api";
import { useApp } from "@/lib/providers";
import type { Page, Trend } from "@/lib/types";

export default function TrendsPage() {
  return (
    <Guard>
      <Body />
    </Guard>
  );
}

const CATEGORIES = ["technology", "public_investment", "business", "import_distribution", "crypto", "macro"];
const STAGES = [
  "weak_signal",
  "emerging",
  "early_adoption",
  "accelerating",
  "mainstream",
  "mature",
  "declining",
];

function Body() {
  const { t, token } = useApp();
  const [data, setData] = useState<Page<Trend> | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);
  const [message, setMessage] = useState<string | null>(null);

  const [sort, setSort] = useState("score");
  const [category, setCategory] = useState("");
  const [stage, setStage] = useState("");
  const [geo, setGeo] = useState("");
  const [subject, setSubject] = useState("");
  const [hideSpikes, setHideSpikes] = useState(false);
  const [hideSeasonal, setHideSeasonal] = useState(false);

  const load = useCallback(() => {
    if (!token) return;
    const params = new URLSearchParams({ sort, limit: "200" });
    if (category) params.set("category", category);
    if (stage) params.set("stage", stage);
    if (geo) params.set("geo_scope", geo);
    if (subject) params.set("subject_type", subject);
    if (hideSpikes) params.set("include_spikes", "false");
    if (hideSeasonal) params.set("include_seasonal", "false");
    api<Page<Trend>>(`/trends?${params}`, { token })
      .then(setData)
      .catch((e) => setError(e.message));
  }, [token, sort, category, stage, geo, subject, hideSpikes, hideSeasonal]);

  useEffect(load, [load]);

  async function evaluate() {
    setBusy(true);
    setMessage(null);
    try {
      const res = await api<{ detail: string }>("/trends/evaluate", { method: "POST", token });
      setMessage(res.detail);
      load();
    } catch (e) {
      setError(e instanceof Error ? e.message : "failed");
    } finally {
      setBusy(false);
    }
  }

  const geos = Array.from(new Set((data?.items ?? []).map((x) => x.geo_scope))).sort();

  if (error) return <p style={{ color: "var(--status-critical)" }}>■ {error}</p>;

  return (
    <>
      <div className="flex flex-wrap items-start gap-3">
        <div className="me-auto">
          <h1 className="text-xl font-semibold">{t.trends.title}</h1>
          <p className="text-sm text-[var(--text-secondary)] max-w-2xl">{t.trends.subtitle}</p>
        </div>
        <button onClick={evaluate} disabled={busy} className="btn text-sm">
          {busy ? t.trends.evaluating : t.trends.evaluate}
        </button>
      </div>
      {message && <p className="text-sm text-[var(--text-secondary)]">{message}</p>}

      <div className="flex flex-wrap gap-3 items-end text-sm">
        <label className="text-xs">
          {t.trends.sortBy}
          <select value={sort} onChange={(e) => setSort(e.target.value)} className="field mt-1 block text-sm">
            <option value="score">{t.trends.sortScore}</option>
            <option value="confidence">{t.trends.sortConfidence}</option>
            <option value="newest">{t.trends.sortNewest}</option>
            <option value="acceleration">{t.trends.sortAcceleration}</option>
          </select>
        </label>
        <label className="text-xs">
          {t.trends.filterCategory}
          <select value={category} onChange={(e) => setCategory(e.target.value)} className="field mt-1 block text-sm">
            <option value="">{t.trends.all}</option>
            {CATEGORIES.map((c) => (
              <option key={c} value={c}>
                {c.replace(/_/g, " ")}
              </option>
            ))}
          </select>
        </label>
        <label className="text-xs">
          {t.trends.filterStage}
          <select value={stage} onChange={(e) => setStage(e.target.value)} className="field mt-1 block text-sm">
            <option value="">{t.trends.all}</option>
            {STAGES.map((s) => (
              <option key={s} value={s}>
                {vocab(t.trends.stages, s)}
              </option>
            ))}
          </select>
        </label>
        <label className="text-xs">
          {t.trends.filterGeo}
          <select value={geo} onChange={(e) => setGeo(e.target.value)} className="field mt-1 block text-sm">
            <option value="">{t.trends.all}</option>
            {geos.map((g) => (
              <option key={g} value={g}>
                {g}
              </option>
            ))}
          </select>
        </label>
        <label className="text-xs">
          {t.trends.filterSubject}
          <select value={subject} onChange={(e) => setSubject(e.target.value)} className="field mt-1 block text-sm">
            <option value="">{t.trends.all}</option>
            <option value="entity">entity</option>
            <option value="topic">topic</option>
          </select>
        </label>
        <label className="text-xs flex items-center gap-2">
          <input type="checkbox" checked={hideSpikes} onChange={(e) => setHideSpikes(e.target.checked)} />
          {t.trends.hideSpikes}
        </label>
        <label className="text-xs flex items-center gap-2">
          <input type="checkbox" checked={hideSeasonal} onChange={(e) => setHideSeasonal(e.target.checked)} />
          {t.trends.hideSeasonal}
        </label>
      </div>

      <Card note={t.trends.notAdvice}>
        <div className="overflow-x-auto">
          <table className="w-full text-sm">
            <thead className="text-xs text-[var(--text-muted)]">
              <tr>
                <th className="text-start py-1">{t.table.entity}</th>
                <th className="text-start py-1 ps-3">{t.trends.filterCategory}</th>
                <th className="text-start py-1 ps-3">{t.trends.score}</th>
                <th className="text-start py-1 ps-3">{t.trends.confidence}</th>
                <th className="text-start py-1 ps-3">{t.trends.stage}</th>
                <th className="text-start py-1 ps-3">{t.trends.state}</th>
                <th className="text-start py-1 ps-3">{t.table.geo}</th>
                <th className="text-end py-1 ps-3">{t.trends.sources}</th>
                <th className="text-start py-1 ps-3">{t.trends.detected}</th>
              </tr>
            </thead>
            <tbody>
              {(data?.items ?? []).map((row) => (
                <tr key={row.id} className="border-t align-top" style={{ borderColor: "var(--gridline)" }}>
                  <td className="py-2">
                    <Link href={`/trends/${row.id}`} className="underline">
                      {row.name}
                    </Link>
                    <div className="text-xs text-[var(--text-muted)]">
                      {row.subject_type}
                      {row.is_spike && ` · ${t.trends.spikeTag}`}
                      {row.is_seasonal && ` · ${t.trends.seasonalTag}`}
                    </div>
                  </td>
                  <td className="py-2 ps-3 text-[var(--text-secondary)]">
                    {(row.category ?? "").replace(/_/g, " ")}
                  </td>
                  <td className="py-2 ps-3">
                    <ScoreBar value={row.trend_score} />
                  </td>
                  <td className="py-2 ps-3">
                    <ScoreBar value={row.confidence} />
                  </td>
                  <td className="py-2 ps-3">
                    <StatusBadge status={row.stage} label={vocab(t.trends.stages, row.stage)} />
                  </td>
                  <td className="py-2 ps-3">
                    <StatusBadge status={row.state} label={vocab(t.trends.states, row.state)} />
                  </td>
                  <td className="py-2 ps-3 text-[var(--text-secondary)]">{row.geo_scope}</td>
                  <td className="py-2 ps-3 text-end tabular" dir="ltr">
                    {row.independent_source_count}
                  </td>
                  <td className="py-2 ps-3 tabular text-xs text-[var(--text-secondary)]" dir="ltr">
                    {new Date(row.first_detected_at).toISOString().slice(0, 10)}
                  </td>
                </tr>
              ))}
              {(data?.items ?? []).length === 0 && (
                <tr>
                  <td colSpan={9} className="py-3 text-[var(--text-muted)]">
                    {t.trends.empty}
                  </td>
                </tr>
              )}
            </tbody>
          </table>
        </div>
      </Card>
    </>
  );
}
