"use client";

import { useCallback, useEffect, useState } from "react";
import { Guard } from "@/components/Guard";
import { Card } from "@/components/ui";
import { api } from "@/lib/api";
import { useApp } from "@/lib/providers";
import type { MatchCandidate } from "@/lib/types";

export default function ReviewPage() {
  return (
    <Guard>
      <Body />
    </Guard>
  );
}

function Body() {
  const { t, token } = useApp();
  const [rows, setRows] = useState<MatchCandidate[]>([]);
  const [showAll, setShowAll] = useState(false);
  const [busy, setBusy] = useState<string | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [notes, setNotes] = useState<Record<string, string>>({});

  const load = useCallback(() => {
    if (!token) return;
    api<MatchCandidate[]>(`/entity-review?decision=${showAll ? "all" : "pending"}`, { token })
      .then(setRows)
      .catch((e) => setError(e.message));
  }, [token, showAll]);

  useEffect(load, [load]);

  async function decide(id: string, decision: string) {
    setBusy(id);
    setError(null);
    try {
      await api(`/entity-review/${id}`, {
        method: "POST",
        token,
        body: JSON.stringify({ decision, note: notes[id] || null }),
      });
      load();
    } catch (e) {
      setError(e instanceof Error ? e.message : "failed");
    } finally {
      setBusy(null);
    }
  }

  return (
    <>
      <div className="flex flex-wrap items-start gap-3">
        <div className="me-auto">
          <h1 className="text-xl font-semibold">{t.review.title}</h1>
          <p className="text-sm text-[var(--text-secondary)] max-w-2xl">{t.review.subtitle}</p>
        </div>
        <button onClick={() => setShowAll(!showAll)} className="btn-ghost text-sm">
          {showAll ? t.review.showPending : t.review.showAll}
        </button>
      </div>

      {error && (
        <p className="text-sm" style={{ color: "var(--status-critical)" }}>
          ■ {error}
        </p>
      )}

      {rows.length === 0 && <Card>{t.review.empty}</Card>}

      <div className="space-y-4">
        {rows.map((row) => (
          <Card key={row.id}>
            <div className="text-xs text-[var(--text-muted)] mb-1">{t.review.possibleMatch}</div>
            <div className="text-sm mb-2">
              <span className="font-semibold">&ldquo;{row.observed_name}&rdquo;</span>
              <span className="mx-2 text-[var(--text-muted)]">→</span>
              <span className="font-semibold">{row.candidate_entity_name}</span>
              <span className="ms-3 tabular text-xs text-[var(--text-secondary)]" dir="ltr">
                {t.review.confidence}: {(row.confidence * 100).toFixed(0)}%
              </span>
            </div>
            <p className="text-sm text-[var(--text-secondary)] mb-2">{row.reason}</p>
            <p className="text-xs text-[var(--text-muted)] mb-3">
              {t.review.identifiers}:{" "}
              {Object.keys(row.candidate_external_ids).length
                ? Object.entries(row.candidate_external_ids)
                    .map(([k, v]) => `${k}=${String(v)}`)
                    .join(", ")
                : t.review.none}
            </p>

            {row.decision === "pending" ? (
              <div className="flex flex-wrap gap-2 items-end">
                <label className="text-xs">
                  {t.review.note}
                  <input
                    value={notes[row.id] ?? ""}
                    onChange={(e) => setNotes({ ...notes, [row.id]: e.target.value })}
                    className="field mt-1 block text-sm"
                  />
                </label>
                <button
                  onClick={() => decide(row.id, "confirmed")}
                  disabled={busy === row.id}
                  className="btn text-sm"
                >
                  {t.review.confirm}
                </button>
                <button
                  onClick={() => decide(row.id, "rejected")}
                  disabled={busy === row.id}
                  className="btn-ghost text-sm"
                >
                  {t.review.reject}
                </button>
                <button
                  onClick={() => decide(row.id, "keep_separate")}
                  disabled={busy === row.id}
                  className="btn-ghost text-sm"
                >
                  {t.review.keepSeparate}
                </button>
              </div>
            ) : (
              <p className="text-sm">
                {t.review.decided}: <strong>{row.decision.replace(/_/g, " ")}</strong>
                {row.note && <span className="text-[var(--text-secondary)]"> — {row.note}</span>}
              </p>
            )}
          </Card>
        ))}
      </div>
    </>
  );
}
