"use client";

import { useParams } from "next/navigation";
import { useCallback, useEffect, useState } from "react";
import { Guard } from "@/components/Guard";
import { LineChart } from "@/components/LineChart";
import { AnalysisModeBadge, Card, ScoreBar, StatusBadge, vocab } from "@/components/ui";
import { api } from "@/lib/api";
import { useApp } from "@/lib/providers";
import type { TrendDetail } from "@/lib/types";

export default function TrendDetailPage() {
  return (
    <Guard>
      <Body />
    </Guard>
  );
}

function Body() {
  const { t, token, dir } = useApp();
  const params = useParams<{ id: string }>();
  const [data, setData] = useState<TrendDetail | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);
  const [selected, setSelected] = useState<string>("");

  const load = useCallback(() => {
    if (!token || !params?.id) return;
    api<TrendDetail>(`/trends/${params.id}`, { token })
      .then((d) => {
        setData(d);
        setSelected((prev) => prev || d.signals[0]?.signal_type || "");
      })
      .catch((e) => setError(e.message));
  }, [token, params?.id]);

  useEffect(load, [load]);

  async function explain() {
    setBusy(true);
    try {
      setData(await api<TrendDetail>(`/trends/${params.id}/explain`, { method: "POST", token }));
    } catch (e) {
      setError(e instanceof Error ? e.message : "failed");
    } finally {
      setBusy(false);
    }
  }

  if (error) return <p style={{ color: "var(--status-critical)" }}>■ {error}</p>;
  if (!data) return <p className="text-sm text-[var(--text-muted)]">{t.loading}</p>;

  const seriesTypes = Array.from(new Set(data.series.map((p) => p.signal_type)));
  const active = selected || seriesTypes[0];
  const chosen = data.series.filter((p) => p.signal_type === active);
  const points = chosen
    .filter((p) => p.status === "ok" && p.value !== null)
    .map((p) => ({ x: new Date(p.at).toISOString().slice(0, 10), y: p.value as number }));
  const gaps = chosen.filter((p) => p.status !== "ok").length;
  const unit = chosen[0]?.currency ?? chosen[0]?.unit ?? null;

  const components = Object.entries(data.components);
  const penalties = Object.entries(data.penalties);
  const confidenceParts = Object.entries(
    (data.metrics.confidence_parts as Record<string, number>) ?? {},
  );
  const stageEvidence = (data.metrics.stage_evidence as string[]) ?? [];
  const lifecycleReason = data.metrics.lifecycle_reason as string | undefined;

  return (
    <>
      <div>
        <h1 className="text-xl font-semibold">{data.name}</h1>
        <p className="text-xs text-[var(--text-muted)]">
          {data.subject_type} · {(data.category ?? "").replace(/_/g, " ")} · {data.geo_scope}
        </p>
      </div>

      <div className="grid grid-cols-2 md:grid-cols-5 gap-3">
        <div className="card p-4">
          <div className="text-xs text-[var(--text-secondary)]">{t.trends.score}</div>
          <div className="mt-1">
            <ScoreBar value={data.trend_score} />
          </div>
        </div>
        <div className="card p-4">
          <div className="text-xs text-[var(--text-secondary)]">{t.trends.confidence}</div>
          <div className="mt-1">
            <ScoreBar value={data.confidence} />
          </div>
        </div>
        <div className="card p-4">
          <div className="text-xs text-[var(--text-secondary)]">{t.trends.stage}</div>
          <div className="mt-2">
            <StatusBadge status={data.stage} label={vocab(t.trends.stages, data.stage)} />
          </div>
        </div>
        <div className="card p-4">
          <div className="text-xs text-[var(--text-secondary)]">{t.trends.state}</div>
          <div className="mt-2">
            <StatusBadge status={data.state} label={vocab(t.trends.states, data.state)} />
          </div>
        </div>
        <div className="card p-4">
          <div className="text-xs text-[var(--text-secondary)]">{t.trends.sources}</div>
          <div className="text-2xl mt-1 tabular" dir="ltr">
            {data.independent_source_count}
          </div>
        </div>
      </div>

      <p className="text-sm text-[var(--text-secondary)]">{t.trends.notAdvice}</p>

      {/* Which evidence produced every number above. A demo-inclusive page says
          so plainly rather than letting a reader assume the live sources alone
          carried the score. */}
      <div className="flex flex-wrap items-center gap-2">
        <AnalysisModeBadge mode={data.analysis_mode} />
        <span className="text-xs text-[var(--text-secondary)]">
          {data.analysis_mode === "live_only"
            ? t.analysis.liveOnlyHint
            : t.analysis.mixedWarning}
        </span>
      </div>

      {data.warnings.length > 0 && (
        <Card title={t.trends.warningsTitle}>
          <ul className="space-y-1 text-sm">
            {data.warnings.map((w, i) => (
              <li key={i} style={{ color: "var(--status-serious)" }}>
                ▲ <span className="text-[var(--text-primary)]">{w}</span>
              </li>
            ))}
          </ul>
        </Card>
      )}

      <Card
        title={t.trends.chart}
        note={
          gaps > 0
            ? `${t.trends.missingData}: ${gaps}. Gaps are excluded from every calculation, never counted as zero.`
            : undefined
        }
        actions={
          seriesTypes.length > 1 ? (
            <select
              value={active}
              onChange={(e) => setSelected(e.target.value)}
              className="field text-xs"
            >
              {seriesTypes.map((s) => (
                <option key={s} value={s}>
                  {s.replace(/_/g, " ")}
                </option>
              ))}
            </select>
          ) : undefined
        }
      >
        <LineChart points={points} label={active.replace(/_/g, " ")} unit={unit} dir={dir} />
      </Card>

      <Card title={t.trends.whyDetected}>
        <ul className="space-y-1 text-sm mb-3">
          {stageEvidence.map((e, i) => (
            <li key={i}>• {e}</li>
          ))}
        </ul>
        {lifecycleReason && (
          <p className="text-sm text-[var(--text-secondary)]">
            {t.trends.state}: {lifecycleReason}
          </p>
        )}
      </Card>

      <Card
        title={t.trends.breakdown}
        note={`${t.trends.raw}: ${Number(data.metrics.raw_score ?? 0).toFixed(0)} · ${
          t.trends.penalties
        }: −${Number(data.metrics.penalty_total ?? 0).toFixed(0)}`}
      >
        <table className="w-full text-sm">
          <tbody>
            {components.map(([name, c]) => (
              <tr key={name} className="border-t" style={{ borderColor: "var(--gridline)" }}>
                <td className="py-1.5 w-48">{name.replace(/_/g, " ")}</td>
                <td className="py-1.5 tabular w-24" dir="ltr">
                  +{c.points.toFixed(1)} / {c.max}
                </td>
                <td className="py-1.5 text-[var(--text-secondary)]">{c.why}</td>
              </tr>
            ))}
            {penalties.map(([name, value]) => (
              <tr key={name} className="border-t" style={{ borderColor: "var(--gridline)" }}>
                <td className="py-1.5" style={{ color: "var(--status-critical)" }}>
                  {name.replace(/_/g, " ")}
                </td>
                <td className="py-1.5 tabular" dir="ltr" style={{ color: "var(--status-critical)" }}>
                  −{value.toFixed(1)}
                </td>
                <td className="py-1.5 text-[var(--text-secondary)]">{t.trends.penalties}</td>
              </tr>
            ))}
            <tr className="border-t-2" style={{ borderColor: "var(--baseline)" }}>
              <td className="py-2 font-semibold">{t.trends.score}</td>
              <td className="py-2 font-semibold tabular" dir="ltr">
                {data.trend_score.toFixed(0)} / 100
              </td>
              <td />
            </tr>
          </tbody>
        </table>
      </Card>

      <Card title={t.trends.confidenceBreakdown}>
        <table className="w-full text-sm">
          <tbody>
            {confidenceParts.map(([name, value]) => (
              <tr key={name} className="border-t" style={{ borderColor: "var(--gridline)" }}>
                <td className="py-1.5 w-56">{name.replace(/_/g, " ")}</td>
                <td className="py-1.5 tabular" dir="ltr">
                  {value >= 0 ? "+" : "−"}
                  {Math.abs(value).toFixed(1)}
                </td>
              </tr>
            ))}
            <tr className="border-t-2" style={{ borderColor: "var(--baseline)" }}>
              <td className="py-2 font-semibold">{t.trends.confidence}</td>
              <td className="py-2 font-semibold tabular" dir="ltr">
                {data.confidence.toFixed(0)} / 100
              </td>
            </tr>
          </tbody>
        </table>
      </Card>

      <Card title={t.trends.supporting}>
        <div className="overflow-x-auto">
          <table className="w-full text-sm">
            <thead className="text-xs text-[var(--text-muted)]">
              <tr>
                <th className="text-start py-1">{t.table.signal}</th>
                <th className="text-start py-1 ps-3">{t.table.source}</th>
                <th className="text-start py-1 ps-3">{t.analysis.label}</th>
                <th className="text-start py-1 ps-3">owner</th>
                <th className="text-end py-1 ps-3">30d</th>
                <th className="text-end py-1 ps-3">{t.table.observations}</th>
                <th className="text-start py-1 ps-3">{t.table.proxy}</th>
              </tr>
            </thead>
            <tbody>
              {data.signals.map((s) => (
                <tr key={s.signal_id} className="border-t" style={{ borderColor: "var(--gridline)" }}>
                  <td className="py-1.5">{s.signal_type.replace(/_/g, " ")}</td>
                  <td className="py-1.5 ps-3 text-[var(--text-secondary)]">{s.source_slug}</td>
                  <td className="py-1.5 ps-3 text-xs text-[var(--text-muted)]">
                    {s.is_live_source ? t.analysis.liveSourceTag : t.analysis.excludedSourceTag}
                  </td>
                  <td className="py-1.5 ps-3 text-[var(--text-muted)]">{s.source_group}</td>
                  <td className="py-1.5 ps-3 text-end tabular" dir="ltr">
                    {s.growth_30d === null ? "—" : `${s.growth_30d.toFixed(1)}%`}
                  </td>
                  <td className="py-1.5 ps-3 text-end tabular" dir="ltr">
                    {s.observation_count}
                  </td>
                  <td className="py-1.5 ps-3">{s.is_proxy ? "yes" : "no"}</td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      </Card>

      {data.related_entities.length > 0 && (
        <Card title={t.trends.relatedTitle}>
          <ul className="text-sm space-y-1">
            {data.related_entities.map((e) => (
              <li key={e.id}>
                {e.name}{" "}
                <span className="text-xs text-[var(--text-muted)]">
                  {e.type}
                  {Object.keys(e.external_ids).length > 0 &&
                    ` · ${Object.entries(e.external_ids)
                      .map(([k, v]) => `${k}=${String(v)}`)
                      .join(", ")}`}
                </span>
              </li>
            ))}
          </ul>
        </Card>
      )}

      <Card
        title={t.trends.explanationTitle}
        actions={
          <button onClick={explain} disabled={busy} className="btn-ghost text-xs">
            {busy ? t.trends.explaining : t.trends.explain}
          </button>
        }
      >
        {data.explanation ? (
          <p className="text-sm">{data.explanation}</p>
        ) : (
          <p className="text-sm text-[var(--text-muted)]">{t.trends.noExplanation}</p>
        )}
      </Card>

      {data.history.length > 1 && (
        <Card title={t.trends.history}>
          <LineChart
            points={data.history.map((h) => ({
              x: new Date(h.evaluated_at).toISOString().slice(0, 10),
              y: h.trend_score,
            }))}
            label={t.trends.score}
            height={160}
            dir={dir}
          />
        </Card>
      )}

      <Card title={t.trends.timeline}>
        <ul className="text-sm space-y-1 max-h-80 overflow-y-auto">
          {data.evidence.map((e, i) => (
            <li key={i} className="flex gap-3">
              <span className="tabular text-xs text-[var(--text-muted)] w-24 shrink-0" dir="ltr">
                {new Date(e.at).toISOString().slice(0, 10)}
              </span>
              <span>
                {e.url ? (
                  <a href={e.url} target="_blank" rel="noreferrer" className="underline">
                    {e.detail}
                  </a>
                ) : (
                  e.detail
                )}
                {e.source_slug && (
                  <span className="text-xs text-[var(--text-muted)]"> · {e.source_slug}</span>
                )}
              </span>
            </li>
          ))}
        </ul>
      </Card>
    </>
  );
}
