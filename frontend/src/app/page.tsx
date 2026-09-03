"use client";

import Link from "next/link";
import { useEffect, useState } from "react";
import { Guard } from "@/components/Guard";
import { Card, Disclaimer, StatTile, StatusBadge } from "@/components/ui";
import { api } from "@/lib/api";
import { useApp } from "@/lib/providers";
import type { Overview } from "@/lib/types";

export default function OverviewPage() {
  return (
    <Guard>
      <OverviewBody />
    </Guard>
  );
}

function OverviewBody() {
  const { t, token } = useApp();
  const [data, setData] = useState<Overview | null>(null);
  const [error, setError] = useState<string | null>(null);

  useEffect(() => {
    if (!token) return;
    api<Overview>("/overview", { token })
      .then(setData)
      .catch((e) => setError(e.message));
  }, [token]);

  if (error) return <p style={{ color: "var(--status-critical)" }}>■ {error}</p>;
  if (!data) return <p className="text-sm text-[var(--text-muted)]">{t.loading}</p>;

  const tiles = ["sources", "raw_records", "entities", "signals", "observations", "opportunities"];

  return (
    <>
      <Disclaimer text={data.disclaimer} />
      <h1 className="text-xl font-semibold">{t.overview.title}</h1>

      <div className="grid grid-cols-2 md:grid-cols-6 gap-3">
        {tiles.map((key) => (
          <StatTile
            key={key}
            label={(t.counts as Record<string, string>)[key] ?? key}
            value={data.counts[key] ?? 0}
          />
        ))}
      </div>

      {data.counts.opportunities === 0 && (
        <p className="text-sm text-[var(--text-secondary)]">{t.overview.noOpportunities}</p>
      )}

      <Card title={t.overview.fastest}>
        <div className="overflow-x-auto">
          <table className="w-full text-sm">
            <thead className="text-xs text-[var(--text-muted)]">
              <tr>
                <th className="text-start py-1">{t.table.entity}</th>
                <th className="text-start py-1">{t.table.signal}</th>
                <th className="text-start py-1 ps-3">{t.table.source}</th>
                <th className="text-end py-1 ps-3">{t.table.change}</th>
                <th className="text-end py-1 ps-4">{t.table.acceleration}</th>
                <th className="text-end py-1 ps-4">{t.table.zscore}</th>
                <th className="text-start py-1 ps-6">{t.table.anomaly}</th>
              </tr>
            </thead>
            <tbody>
              {data.fastest_growing.map((row) => (
                <tr key={row.signal_id} className="border-t" style={{ borderColor: "var(--gridline)" }}>
                  <td className="py-1.5">{row.entity}</td>
                  <td className="py-1.5">
                    <Link href={`/signals/${row.signal_id}`} className="underline">
                      {row.signal_type}
                    </Link>
                    {row.is_proxy && (
                      <span className="ms-2 text-xs text-[var(--text-muted)]">({t.table.proxy})</span>
                    )}
                  </td>
                  <td className="py-1.5 ps-3 text-[var(--text-secondary)]">{row.source}</td>
                  <td className="py-1.5 ps-3 text-end tabular" dir="ltr">
                    {row.pct_change_window.toFixed(1)}%
                  </td>
                  <td className="py-1.5 ps-4 text-end tabular" dir="ltr">
                    {row.acceleration === null ? "—" : `${row.acceleration.toFixed(1)}pp`}
                  </td>
                  <td className="py-1.5 ps-4 text-end tabular" dir="ltr">
                    {row.zscore === null ? "—" : row.zscore.toFixed(2)}
                  </td>
                  <td className="py-1.5 ps-6">
                    {row.is_anomaly ? (
                      <StatusBadge status="anomaly" label={t.anomalyLabel} />
                    ) : (
                      <span className="text-xs text-[var(--text-muted)]">—</span>
                    )}
                  </td>
                </tr>
              ))}
              {data.fastest_growing.length === 0 && (
                <tr>
                  <td colSpan={7} className="py-3 text-[var(--text-muted)]">
                    {t.table.none}
                  </td>
                </tr>
              )}
            </tbody>
          </table>
        </div>
      </Card>

      <div className="grid md:grid-cols-2 gap-4">
        <Card title={t.overview.health}>
          <ul className="space-y-2 text-sm">
            {data.source_health.map((s) => (
              <li key={s.slug} className="flex items-center gap-3">
                <StatusBadge status={s.enabled ? s.status : "disabled"} />
                <span className="me-auto">
                  {s.name}
                  <span className="ms-2 text-xs text-[var(--text-muted)]">{s.adapter_key}</span>
                </span>
                <span className="tabular text-xs text-[var(--text-secondary)] whitespace-nowrap">
                  {t.table.reliability} <span dir="ltr">{s.reliability.toFixed(2)}</span>
                  {" · "}
                  <span dir="ltr">
                    {s.freshness_hours === null ? "—" : `${s.freshness_hours}h`}
                  </span>
                </span>
              </li>
            ))}
          </ul>
        </Card>

        <Card title={t.overview.activity}>
          <ul className="space-y-2 text-sm">
            {data.recent_activity.map((r, i) => (
              <li key={i} className="flex items-center gap-3">
                <StatusBadge status={r.status} />
                <span className="me-auto">{r.source}</span>
                <span className="tabular text-xs text-[var(--text-secondary)] whitespace-nowrap">
                  <span dir="ltr">{`${r.stored}/${r.fetched}`}</span>
                  {" · "}
                  <span dir="ltr">{`${r.http_requests} ${t.table.requests}`}</span>
                  {" · "}
                  <span dir="ltr">{new Date(r.started_at).toLocaleString()}</span>
                </span>
              </li>
            ))}
            {data.recent_activity.length === 0 && (
              <li className="text-[var(--text-muted)]">{t.table.none}</li>
            )}
          </ul>
        </Card>
      </div>
    </>
  );
}
