"use client";

import Link from "next/link";
import { useParams } from "next/navigation";
import { useCallback, useEffect, useState } from "react";
import { Guard } from "@/components/Guard";
import { Card, ScoreBar, StatusBadge, vocab } from "@/components/ui";
import { api } from "@/lib/api";
import { useApp } from "@/lib/providers";
import type { OpportunityDetail, OpportunityReport } from "@/lib/types";

export default function OpportunityDetailPage() {
  return (
    <Guard>
      <Body />
    </Guard>
  );
}

const INTERESTS = [
  "interested",
  "researching",
  "watching",
  "not_interested",
  "rejected",
  "acted_on",
];

function Tile({ label, children }: { label: string; children: React.ReactNode }) {
  return (
    <div className="card min-w-[150px] flex-1 p-3">
      <div className="text-xs text-[var(--text-secondary)]">{label}</div>
      <div className="mt-1">{children}</div>
    </div>
  );
}

function Section({
  title,
  note,
  children,
}: {
  title: string;
  note?: string;
  children: React.ReactNode;
}) {
  return (
    <Card>
      <h2 className="text-sm font-semibold">{title}</h2>
      {note && <p className="mt-1 text-xs text-[var(--text-secondary)]">{note}</p>}
      <div className="mt-3">{children}</div>
    </Card>
  );
}

function Bullets({ items }: { items: string[] }) {
  if (items.length === 0) return null;
  return (
    <ul className="space-y-1.5 text-sm">
      {items.map((x, i) => (
        <li key={i} className="flex gap-2">
          <span className="text-[var(--text-muted)]">•</span>
          <span>{x}</span>
        </li>
      ))}
    </ul>
  );
}

function Body() {
  const { t, token } = useApp();
  const params = useParams<{ id: string }>();
  const [data, setData] = useState<OpportunityDetail | null>(null);
  const [report, setReport] = useState<OpportunityReport | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [note, setNote] = useState("");
  const [saving, setSaving] = useState(false);

  const load = useCallback(() => {
    if (!token || !params?.id) return;
    api<OpportunityDetail>(`/opportunities/${params.id}`, { token })
      .then(setData)
      .catch((e) => setError(e.message));
  }, [token, params?.id]);

  useEffect(load, [load]);

  async function openReport() {
    if (!params?.id) return;
    const r = await api<OpportunityReport>(`/opportunities/${params.id}/report`, { token });
    setReport(r);
  }

  async function decide(interest: string) {
    if (!params?.id) return;
    setSaving(true);
    try {
      await api(`/opportunities/${params.id}/decisions`, {
        method: "POST",
        token,
        body: JSON.stringify({ interest, note: note || null }),
      });
      setNote("");
      load();
    } catch (e) {
      setError(e instanceof Error ? e.message : "failed");
    } finally {
      setSaving(false);
    }
  }

  if (error) return <p style={{ color: "var(--status-critical)" }}>■ {error}</p>;
  if (!data) return <p className="text-sm text-[var(--text-secondary)]">{t.loading}</p>;

  const confirmations = data.conditions.filter((c) => c.kind === "confirmation");
  const invalidations = data.conditions.filter((c) => c.kind === "invalidation");
  const evidenceByKind = data.evidence.reduce<Record<string, typeof data.evidence>>(
    (acc, row) => {
      (acc[row.kind] ||= []).push(row);
      return acc;
    },
    {},
  );

  return (
    <>
      <div>
        <h1 className="text-xl font-semibold">{data.title}</h1>
        <p className="text-xs text-[var(--text-secondary)]">
          {vocab(t.opportunities.types, data.opportunity_type)} · {data.country ?? data.geo_scope}
          {data.trend_name && (
            <>
              {" · "}
              <Link href={`/trends/${data.trend_id}`} className="link">
                {data.trend_name}
              </Link>
            </>
          )}
          {" · "}
          {t.opportunities.report}: v{data.algorithm_version}
        </p>
      </div>

      {data.validation_status !== "live_validated" && (
        <div
          className="card border-s-4 p-3 text-sm"
          style={{ borderInlineStartColor: "var(--status-warning)" }}
        >
          <strong>
            {data.validation_status === "demo"
              ? t.opportunities.demoBadge
              : t.opportunities.unvalidatedBadge}
          </strong>{" "}
          {t.opportunities.validationNote}
        </div>
      )}

      {/* The two scores are rendered in two separate blocks, never side by side
          in a way that invites the reader to average them. The global block is
          about the world; the personal block is about them. */}
      <div className="flex flex-wrap gap-3">
        <Tile label={t.opportunities.score}>
          <ScoreBar value={data.opportunity_score} />
        </Tile>
        <Tile label={t.opportunities.confidence}>
          <ScoreBar value={data.confidence} />
        </Tile>
        <Tile label={t.opportunities.risk}>
          <StatusBadge
            status={`risk_${data.risk_level}`}
            label={vocab(t.opportunities.risks_levels, data.risk_level)}
          />
        </Tile>
        <Tile label={t.opportunities.trendScore}>
          <ScoreBar value={data.trend_score ?? 0} />
        </Tile>
        <Tile label={t.opportunities.status}>
          <StatusBadge status={data.state} label={vocab(t.opportunities.states, data.state)} />
        </Tile>
      </div>
      <p className="text-xs text-[var(--text-secondary)]">{t.opportunities.scoreHint}</p>

      <div className="card p-3">
        <div className="flex flex-wrap items-center gap-3">
          <Tile label={t.opportunities.relevance}>
            <ScoreBar value={data.user_relevance ?? 0} />
          </Tile>
          {data.best_path && (
            <Tile label={t.opportunities.bestPath}>
              <span className="text-sm">
                {vocab(t.opportunities.paths, data.best_path)}
              </span>
            </Tile>
          )}
          {data.outside_profile && (
            <Tile label={t.opportunities.outsideProfile}>
              <StatusBadge status="risk_moderate" label={t.opportunities.outsideProfile} />
            </Tile>
          )}
        </div>
        <p className="mt-2 text-xs text-[var(--text-secondary)]">
          {t.opportunities.relevanceHint}
        </p>
        {data.outside_profile && (
          <p className="mt-1 text-xs text-[var(--text-secondary)]">
            {t.opportunities.outsideProfileHint}
          </p>
        )}
        {data.relevance_notes.length > 0 && (
          <ul className="mt-2 space-y-1 text-xs text-[var(--text-secondary)]">
            {data.relevance_notes.map((note, i) => (
              <li key={i}>— {note}</li>
            ))}
          </ul>
        )}
      </div>

      <p className="text-xs text-[var(--text-secondary)]">{t.opportunities.notAdvice}</p>

      {data.warnings.length > 0 && (
        <Section title={t.trends.warningsTitle}>
          <ul className="space-y-1.5 text-sm">
            {data.warnings.map((w, i) => (
              <li key={i} className="flex gap-2">
                <span style={{ color: "var(--status-warning)" }}>▲</span>
                <span>{w}</span>
              </li>
            ))}
          </ul>
        </Section>
      )}

      {(data.thesis || data.counter_thesis) && (
        <Card>
          <div className="grid gap-4 md:grid-cols-2">
            <div>
              <h2 className="text-sm font-semibold">{t.opportunities.thesis}</h2>
              <p className="mt-2 text-sm">{data.thesis ?? "—"}</p>
            </div>
            <div>
              <h2 className="text-sm font-semibold">{t.opportunities.counterThesis}</h2>
              <p className="mt-2 text-sm">{data.counter_thesis ?? "—"}</p>
            </div>
          </div>
          {data.mechanism && (
            <div className="mt-4 border-t border-[var(--border)] pt-3">
              <h3 className="text-xs font-semibold text-[var(--text-secondary)]">
                {t.opportunities.whatMakesInteresting}
              </h3>
              <p className="mt-1 text-sm">{data.mechanism}</p>
            </div>
          )}
        </Card>
      )}

      <Section title={t.opportunities.whyEarly}>
        {data.why_early.length > 0 ? (
          <Bullets items={data.why_early} />
        ) : (
          <p className="text-sm text-[var(--text-secondary)]">{t.opportunities.noWhyEarly}</p>
        )}
      </Section>

      <Section title={t.opportunities.breakdown} note={`${t.opportunities.raw}: ${data.raw_score.toFixed(0)} · ${t.opportunities.penalties}: −${data.penalty_total.toFixed(0)}`}>
        <table className="w-full text-sm">
          <tbody>
            {Object.entries(data.components).map(([name, c]) => (
              <tr key={name} className="border-b border-[var(--border)] align-top">
                <td className="py-1.5 pe-3 whitespace-nowrap">{name.replace(/_/g, " ")}</td>
                <td className="py-1.5 pe-3 tabular whitespace-nowrap" dir="ltr">
                  +{c.points.toFixed(1)} / {c.max}
                </td>
                <td className="py-1.5 text-[var(--text-secondary)]">{c.why}</td>
              </tr>
            ))}
            {Object.entries(data.penalties).map(([name, points]) => (
              <tr key={name} className="border-b border-[var(--border)]">
                <td className="py-1.5 pe-3" style={{ color: "var(--status-critical)" }}>
                  {name.replace(/_/g, " ")}
                </td>
                <td className="py-1.5 pe-3 tabular" dir="ltr" style={{ color: "var(--status-critical)" }}>
                  −{Number(points).toFixed(1)}
                </td>
                <td className="py-1.5 text-[var(--text-secondary)]">
                  {t.opportunities.penalties}
                </td>
              </tr>
            ))}
            <tr className="font-semibold">
              <td className="py-2">{t.opportunities.score}</td>
              <td className="py-2 tabular" dir="ltr">
                {data.opportunity_score.toFixed(0)} / 100
              </td>
              <td />
            </tr>
          </tbody>
        </table>
      </Section>

      <div className="grid gap-4 md:grid-cols-2">
        <Section title={t.opportunities.confidenceBreakdown}>
          <table className="w-full text-sm">
            <tbody>
              {Object.entries(data.confidence_parts).map(([name, points]) => (
                <tr key={name} className="border-b border-[var(--border)]">
                  <td className="py-1.5">{name.replace(/_/g, " ")}</td>
                  <td className="py-1.5 tabular text-end" dir="ltr">
                    {Number(points) >= 0 ? "+" : "−"}
                    {Math.abs(Number(points)).toFixed(1)}
                  </td>
                </tr>
              ))}
              <tr className="font-semibold">
                <td className="py-2">{t.opportunities.confidence}</td>
                <td className="py-2 tabular text-end" dir="ltr">
                  {data.confidence.toFixed(0)} / 100
                </td>
              </tr>
            </tbody>
          </table>
        </Section>

        <Section title={t.opportunities.relevanceBreakdown}>
          <table className="w-full text-sm">
            <tbody>
              {Object.entries(data.user_relevance_parts).map(([name, part]) => (
                <tr key={name} className="border-b border-[var(--border)] align-top">
                  <td className="py-1.5 pe-2">
                    {name.replace(/_/g, " ")}
                    <div className="text-xs text-[var(--text-secondary)]">{part.why}</div>
                  </td>
                  <td className="py-1.5 tabular text-end whitespace-nowrap" dir="ltr">
                    +{part.points.toFixed(1)}
                  </td>
                </tr>
              ))}
              <tr className="font-semibold">
                <td className="py-2">{t.opportunities.relevance}</td>
                <td className="py-2 tabular text-end" dir="ltr">
                  {(data.user_relevance ?? 0).toFixed(0)} / 100
                </td>
              </tr>
            </tbody>
          </table>
          {Object.keys(data.path_relevance).length > 1 && (
            <div className="mt-3">
              <h3 className="text-xs font-semibold uppercase tracking-wide text-[var(--text-secondary)]">
                {t.opportunities.pathBreakdown}
              </h3>
              <table className="mt-1 w-full text-sm">
                <tbody>
                  {Object.entries(data.path_relevance)
                    .sort((a, b) => b[1] - a[1])
                    .map(([path, value]) => (
                      <tr key={path} className="border-b border-[var(--border)]">
                        <td className="py-1.5 pe-2">
                          {vocab(t.opportunities.paths, path)}
                        </td>
                        <td className="py-1.5 tabular text-end" dir="ltr">
                          {value.toFixed(0)} / 100
                        </td>
                      </tr>
                    ))}
                </tbody>
              </table>
            </div>
          )}
        </Section>
      </div>

      <Section title={t.opportunities.evidence}>
        {Object.entries(evidenceByKind).map(([kind, rows]) => (
          <div key={kind} className="mb-3">
            <h3 className="text-xs font-semibold uppercase tracking-wide text-[var(--text-secondary)]">
              {kind}
            </h3>
            <table className="mt-1 w-full text-sm">
              <tbody>
                {rows.map((row, i) => (
                  <tr key={i} className="border-b border-[var(--border)]">
                    <td className="py-1.5">
                      {row.signal_id ? (
                        <Link href={`/signals/${row.signal_id}`} className="link">
                          {row.signal_type.replace(/_/g, " ")}
                        </Link>
                      ) : (
                        row.signal_type.replace(/_/g, " ")
                      )}
                    </td>
                    <td className="py-1.5 ps-3 text-xs text-[var(--text-secondary)]">
                      {row.source_slug}
                    </td>
                    <td className="py-1.5 ps-3 tabular text-xs" dir="ltr">
                      {row.growth_30d === null ? "—" : `${row.growth_30d.toFixed(1)}%`}
                    </td>
                    <td className="py-1.5 ps-3 text-xs text-[var(--text-secondary)]">
                      {row.observation_count} obs
                    </td>
                    <td className="py-1.5 ps-3 text-xs text-[var(--text-secondary)]">
                      {row.is_proxy ? t.table.proxy : ""}
                    </td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        ))}
      </Section>

      {data.skeptic && (
        <Section title={t.opportunities.skeptic} note={t.opportunities.skepticNote}>
          <p className="mb-3 text-sm font-medium">
            {t.opportunities.strongestObjection}: {data.skeptic.strongest_counterargument}
          </p>
          <Bullets items={data.skeptic.counterarguments} />
          {data.skeptic.alternative_explanations.length > 0 && (
            <>
              <h3 className="mt-4 text-xs font-semibold text-[var(--text-secondary)]">
                {t.opportunities.alternatives}
              </h3>
              <Bullets items={data.skeptic.alternative_explanations} />
            </>
          )}
          {data.skeptic.risk_flags.length > 0 && (
            <>
              <h3 className="mt-4 text-xs font-semibold text-[var(--text-secondary)]">
                {t.opportunities.redFlags}
              </h3>
              <Bullets items={data.skeptic.risk_flags} />
            </>
          )}
          {data.skeptic.too_late_reasons.length > 0 && (
            <>
              <h3 className="mt-4 text-xs font-semibold text-[var(--text-secondary)]">
                {t.opportunities.tooLate}
              </h3>
              <Bullets items={data.skeptic.too_late_reasons} />
            </>
          )}
          {data.skeptic.inaccessible_reasons.length > 0 && (
            <>
              <h3 className="mt-4 text-xs font-semibold text-[var(--text-secondary)]">
                {t.opportunities.inaccessible}
              </h3>
              <Bullets items={data.skeptic.inaccessible_reasons} />
            </>
          )}
          <p className="mt-4 text-xs text-[var(--text-secondary)]">
            {t.opportunities.manipulation}:{" "}
            <span dir="ltr">{(data.skeptic.manipulation_probability * 100).toFixed(0)}%</span> ·{" "}
            {t.opportunities.confidenceReduction}:{" "}
            <span dir="ltr">−{data.skeptic.confidence_reduction.toFixed(0)}</span>
          </p>
        </Section>
      )}

      <Section title={t.opportunities.risks}>
        <table className="w-full text-sm">
          <tbody>
            {data.risks.map((r) => (
              <tr key={r.code} className="border-b border-[var(--border)] align-top">
                <td className="py-1.5 pe-3 whitespace-nowrap">
                  <StatusBadge
                    status={r.is_blocking ? "risk_very_high" : `risk_${r.severity === "high" ? "high" : "moderate"}`}
                    label={r.severity}
                  />
                </td>
                <td className="py-1.5 pe-3 text-xs text-[var(--text-secondary)]">
                  {r.category.replace(/_/g, " ")}
                </td>
                <td className="py-1.5">
                  {r.rationale}
                  {r.mitigation && (
                    <div className="text-xs text-[var(--text-secondary)]">↳ {r.mitigation}</div>
                  )}
                </td>
              </tr>
            ))}
          </tbody>
        </table>
      </Section>

      <div className="grid gap-4 md:grid-cols-2">
        <Section title={t.opportunities.confirmation}>
          <Bullets items={confirmations.map((c) => c.description)} />
        </Section>
        <Section title={t.opportunities.invalidation}>
          <Bullets items={invalidations.map((c) => c.description)} />
        </Section>
      </div>

      <Section title={t.opportunities.participation}>
        <ul className="space-y-2 text-sm">
          {data.participation.map((p, i) => (
            <li key={i}>
              <span className="font-medium">{p.kind.replace(/_/g, " ")}</span>{" "}
              <span className="text-xs text-[var(--text-muted)]">({p.difficulty})</span>
              <div className="text-[var(--text-secondary)]">{p.description}</div>
            </li>
          ))}
        </ul>
      </Section>

      {data.missing_evidence.length > 0 && (
        <Section title={t.opportunities.missing}>
          <Bullets items={data.missing_evidence} />
        </Section>
      )}

      {data.next_research_steps.length > 0 && (
        <Section title={t.opportunities.nextSteps}>
          <Bullets items={data.next_research_steps} />
        </Section>
      )}

      <Section title={t.opportunities.analysis}>
        <table className="w-full text-sm">
          <tbody>
            {Object.entries(data.analysis).map(([key, value]) => (
              <tr key={key} className="border-b border-[var(--border)] align-top">
                <td className="py-1.5 pe-3 whitespace-nowrap">{key.replace(/_/g, " ")}</td>
                <td className="py-1.5 text-[var(--text-secondary)]">
                  {typeof value === "object" && value !== null
                    ? JSON.stringify(value)
                    : String(value)}
                </td>
              </tr>
            ))}
          </tbody>
        </table>
      </Section>

      <Section title={t.opportunities.experimental} note={t.opportunities.experimentalNote}>
        <div className="grid gap-4 md:grid-cols-2 text-sm">
          <div>
            <div className="text-xs text-[var(--text-secondary)]">{t.opportunities.geoGap}</div>
            <div className="mt-1">
              {data.geographic_gap === null ? (
                <span className="text-[var(--text-secondary)]">{t.opportunities.unknown}</span>
              ) : (
                <ScoreBar value={data.geographic_gap} />
              )}
            </div>
          </div>
          <div>
            <div className="text-xs text-[var(--text-secondary)]">
              {t.opportunities.adoptionRatio}
            </div>
            <div className="mt-1 tabular" dir="ltr">
              {data.adoption_attention_ratio === null
                ? t.opportunities.unknown
                : data.adoption_attention_ratio.toFixed(2)}
            </div>
            <div className="mt-1 text-xs text-[var(--text-secondary)]">
              {String(data.adoption_attention_parts?.reading ?? "")}
            </div>
          </div>
        </div>
      </Section>

      <Section title={t.opportunities.decisions} note={t.opportunities.decisionsNote}>
        <label className="text-xs">
          {t.opportunities.note}
          <input
            value={note}
            onChange={(e) => setNote(e.target.value)}
            className="field mt-1 block w-full text-sm"
          />
        </label>
        <div className="mt-2 flex flex-wrap gap-2">
          {INTERESTS.map((x) => (
            <button
              key={x}
              onClick={() => decide(x)}
              disabled={saving}
              className="btn-ghost text-xs"
            >
              {vocab(t.opportunities.interests, x)}
            </button>
          ))}
        </div>
        {data.decisions.length > 0 && (
          <table className="mt-4 w-full text-sm">
            <tbody>
              {data.decisions.map((d) => (
                <tr key={d.id} className="border-b border-[var(--border)]">
                  <td className="py-1.5">{vocab(t.opportunities.interests, d.interest)}</td>
                  <td className="py-1.5 ps-3 text-xs text-[var(--text-secondary)]" dir="ltr">
                    {d.decided_at.slice(0, 10)}
                  </td>
                  <td className="py-1.5 ps-3 text-xs text-[var(--text-secondary)]" dir="ltr">
                    {d.score_at_decision.toFixed(0)} / {d.confidence_at_decision.toFixed(0)} /{" "}
                    {d.risk_at_decision}
                  </td>
                  <td className="py-1.5 ps-3 text-xs">{d.note}</td>
                </tr>
              ))}
            </tbody>
          </table>
        )}
      </Section>

      <Card>
        <div className="flex flex-wrap items-center gap-3">
          <h2 className="me-auto text-sm font-semibold">{t.opportunities.report}</h2>
          <button onClick={openReport} className="btn-ghost text-xs">
            {t.opportunities.openReport}
          </button>
        </div>
        {report && (
          <div className="mt-3 space-y-3">
            {report.narration_note && (
              <p className="text-xs text-[var(--text-secondary)]">{report.narration_note}</p>
            )}
            {report.sections
              .filter((s) => s.body)
              .map((s) => (
                <div key={s.key}>
                  <h3 className="text-xs font-semibold text-[var(--text-secondary)]">
                    {s.title}
                    {s.generated && " ✎"}
                  </h3>
                  <p className="text-sm">{s.body}</p>
                </div>
              ))}
          </div>
        )}
      </Card>
    </>
  );
}
