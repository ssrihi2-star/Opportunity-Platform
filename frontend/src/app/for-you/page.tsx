"use client";

import Link from "next/link";
import { useCallback, useEffect, useState } from "react";
import { Guard } from "@/components/Guard";
import { AnalysisModeBadge, Card, ScoreBar, StatusBadge, ValidationBadge, vocab } from "@/components/ui";
import { api } from "@/lib/api";
import { useApp } from "@/lib/providers";
import type { Opportunity, Page } from "@/lib/types";

export default function ForYouPage() {
  return (
    <Guard>
      <Body />
    </Guard>
  );
}

function Body() {
  const { t, token } = useApp();
  const [data, setData] = useState<Page<Opportunity> | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [minRelevance, setMinRelevance] = useState("");
  const [includeOutside, setIncludeOutside] = useState(true);

  const load = useCallback(() => {
    if (!token) return;
    const params = new URLSearchParams({ limit: "200" });
    if (minRelevance !== "") params.set("min_relevance", minRelevance);
    if (!includeOutside) params.set("include_outside_profile", "false");
    api<Page<Opportunity>>(`/for-you?${params}`, { token })
      .then(setData)
      .catch((e) => setError(e.message));
  }, [token, minRelevance, includeOutside]);

  useEffect(load, [load]);

  if (error) return <p style={{ color: "var(--status-critical)" }}>■ {error}</p>;

  const items = data?.items ?? [];

  return (
    <>
      <div>
        <h1 className="text-xl font-semibold">{t.forYou.title}</h1>
        <p className="max-w-3xl text-sm text-[var(--text-secondary)]">{t.forYou.subtitle}</p>
      </div>

      <Card title={t.forYou.howTitle}>
        <ul className="space-y-2 text-sm text-[var(--text-secondary)]">
          {t.forYou.how.map((line, i) => (
            <li key={i} className="flex items-start gap-2">
              <span
                aria-hidden
                className="mt-1"
                style={{ color: i === 1 ? "var(--series-2)" : "var(--series-1)" }}
              >
                ●
              </span>
              <span>{line}</span>
            </li>
          ))}
        </ul>
        <p className="mt-3 text-xs text-[var(--text-muted)]">
          <Link href="/settings" className="link">
            {t.forYou.noProfileHint}
          </Link>
        </p>
      </Card>

      {/* Stated once at page level, because it is a property of the whole feed
          rather than of any one card: this surface has no evidence selector, so
          the reader is told what it reads instead of choosing. */}
      <p className="text-xs text-[var(--text-muted)]">{t.forYou.liveScope}</p>

      <div className="flex flex-wrap items-end gap-4 text-sm">
        <label className="text-xs">
          {t.forYou.minRelevance}
          <input
            type="number"
            min={0}
            max={100}
            value={minRelevance}
            onChange={(e) => setMinRelevance(e.target.value)}
            placeholder="0–100"
            className="field mt-1 block w-24 text-sm"
            dir="ltr"
          />
        </label>
        <label className="flex items-start gap-2 text-xs">
          <input
            type="checkbox"
            checked={includeOutside}
            onChange={(e) => setIncludeOutside(e.target.checked)}
            className="mt-0.5"
          />
          <span>
            {t.forYou.includeOutside}
            <span className="block text-[var(--text-muted)]">{t.forYou.includeOutsideHint}</span>
          </span>
        </label>
      </div>

      {items.length === 0 ? (
        <Card>
          <p className="py-4 text-sm text-[var(--text-secondary)]">{t.forYou.empty}</p>
        </Card>
      ) : (
        <div className="space-y-4">
          {items.map((row) => (
            <OpportunityCard key={row.id} row={row} />
          ))}
        </div>
      )}
    </>
  );
}

function OpportunityCard({ row }: { row: Opportunity }) {
  const { t } = useApp();
  return (
    <Card>
      <div className="flex items-start gap-3">
        <div className="me-auto">
          <Link href={`/opportunities/${row.id}`} className="link font-medium">
            {row.title}
          </Link>
          <div className="mt-1 flex flex-wrap items-center gap-1.5 text-[11px] text-[var(--text-muted)]">
            {/* Per row as well as per page. Cards get copied, screenshotted and
                pasted into chats on their own, and the label has to survive that. */}
            <AnalysisModeBadge mode={row.analysis_mode} />
            <ValidationBadge status={row.validation_status} />
            <span>{vocab(t.opportunities.types, row.opportunity_type)}</span>
            {row.trend_name && <span>· {row.trend_name}</span>}
          </div>
        </div>
        <StatusBadge status={row.state} label={vocab(t.opportunities.states, row.state)} />
      </div>

      {/* The two numbers the whole page exists to keep apart. */}
      <div className="mt-4 grid gap-3 sm:grid-cols-2">
        <div>
          <div className="text-xs text-[var(--text-secondary)]">{t.opportunities.score}</div>
          <ScoreBar value={row.opportunity_score} />
          <div className="mt-1 text-[11px] text-[var(--text-muted)]">{t.opportunities.scoreHint}</div>
        </div>
        <div>
          <div className="text-xs text-[var(--text-secondary)]">{t.opportunities.relevance}</div>
          {row.user_relevance == null ? (
            <span className="text-sm text-[var(--text-muted)]">—</span>
          ) : (
            <ScoreBar value={row.user_relevance} tone="var(--series-2)" />
          )}
          <div className="mt-1 text-[11px] text-[var(--text-muted)]">
            {t.opportunities.relevanceHint}
          </div>
        </div>
      </div>

      <div className="mt-3 flex flex-wrap items-center gap-x-4 gap-y-1.5 text-xs text-[var(--text-secondary)]">
        <span>
          {t.opportunities.confidence}:{" "}
          <b className="tabular" dir="ltr">
            {row.confidence.toFixed(0)}
          </b>
        </span>
        <span>
          {t.opportunities.trendScore}:{" "}
          <b className="tabular" dir="ltr">
            {row.trend_score == null ? "—" : row.trend_score.toFixed(0)}
          </b>
        </span>
        <span className="inline-flex items-center gap-1.5">
          {t.opportunities.risk}:{" "}
          <StatusBadge
            status={`risk_${row.risk_level}`}
            label={vocab(t.opportunities.risks_levels, row.risk_level)}
          />
        </span>
        <span>
          {t.opportunities.geography}: {row.country ?? row.geo_scope}
        </span>
        <span className="text-[var(--text-muted)]" dir="ltr">
          {row.detected_at.slice(0, 10)}
        </span>
      </div>

      {(row.best_path || row.outside_profile) && (
        <div className="mt-3 flex flex-wrap items-center gap-2 text-xs">
          {row.best_path && (
            <span
              className="inline-flex items-center gap-1.5 rounded border px-1.5 py-0.5"
              style={{ borderColor: "var(--border-ring)" }}
            >
              <span aria-hidden style={{ color: "var(--series-1)" }}>
                ◆
              </span>
              <span>
                {t.opportunities.bestPath}: {vocab(t.opportunities.paths, row.best_path)}
              </span>
            </span>
          )}
          {row.outside_profile && (
            <span
              className="inline-flex items-center gap-1.5 rounded border px-1.5 py-0.5"
              style={{ borderColor: "var(--border-ring)" }}
            >
              <span aria-hidden style={{ color: "var(--status-warning)" }}>
                ◈
              </span>
              <span>{t.opportunities.outsideProfile}</span>
            </span>
          )}
        </div>
      )}
    </Card>
  );
}
