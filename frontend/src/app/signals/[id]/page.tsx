"use client";

import { useParams } from "next/navigation";
import { useEffect, useState } from "react";
import { Guard } from "@/components/Guard";
import { LineChart } from "@/components/LineChart";
import { Card } from "@/components/ui";
import { api } from "@/lib/api";
import { useApp } from "@/lib/providers";
import type { SignalDetail } from "@/lib/types";

export default function SignalDetailPage() {
  return (
    <Guard>
      <Body />
    </Guard>
  );
}

function fmt(value: number | null, suffix = ""): string {
  return value === null ? "—" : `${value.toFixed(2)}${suffix}`;
}

function Body() {
  const { t, token, dir } = useApp();
  const params = useParams<{ id: string }>();
  const [data, setData] = useState<SignalDetail | null>(null);
  const [error, setError] = useState<string | null>(null);

  useEffect(() => {
    if (!token || !params?.id) return;
    api<SignalDetail>(`/signals/${params.id}`, { token })
      .then(setData)
      .catch((e) => setError(e.message));
  }, [token, params?.id]);

  if (error) return <p style={{ color: "var(--status-critical)" }}>■ {error}</p>;
  if (!data) return <p className="text-sm text-[var(--text-muted)]">{t.loading}</p>;

  const points = data.observations.map((o) => ({
    x: new Date(o.observed_at).toISOString().slice(0, 10),
    y: o.value,
  }));

  const stats: [string, string][] = [
    ["n", String(data.stats.n)],
    ["direction", data.stats.direction],
    ["pct change (last)", fmt(data.stats.pct_change_last, "%")],
    ["pct change (window)", fmt(data.stats.pct_change_window, "%")],
    ["moving average", fmt(data.stats.moving_average)],
    ["EWMA", fmt(data.stats.ewma_last)],
    ["z-score", fmt(data.stats.zscore_last)],
    ["acceleration", fmt(data.stats.acceleration, "pp")],
    [
      "changepoint index",
      data.stats.changepoint_index === null ? "none" : String(data.stats.changepoint_index),
    ],
    ["anomaly", data.stats.is_anomaly ? "yes" : "no"],
    ["strength", data.stats.strength.toFixed(3)],
  ];

  return (
    <>
      <h1 className="text-xl font-semibold">
        {data.entity_name} — {data.signal_type}
      </h1>
      <p className="text-xs text-[var(--text-muted)]">
        {t.table.source}: {data.source_slug} · {t.table.geo}: {data.geo_scope}
      </p>
      {data.is_proxy && (
        <p className="text-sm" style={{ color: "var(--status-serious)" }}>
          ▲ {t.signal.proxyWarning}
        </p>
      )}

      <Card title={t.signal.series}>
        <LineChart
          points={points}
          label={`${data.signal_type} — ${data.entity_name ?? ""}`}
          unit={data.unit}
          dir={dir}
        />
      </Card>

      <Card title={t.signal.stats} note={data.stats.note || undefined}>
        <dl className="grid grid-cols-2 md:grid-cols-4 gap-3 text-sm">
          {stats.map(([k, v]) => (
            <div key={k}>
              <dt className="text-xs text-[var(--text-muted)]">{k}</dt>
              <dd className="tabular" dir="ltr">
                {v}
              </dd>
            </div>
          ))}
        </dl>
      </Card>

      <Card title={t.table.observations}>
        <div className="overflow-x-auto max-h-80">
          <table className="w-full text-sm">
            <thead className="text-xs text-[var(--text-muted)]">
              <tr>
                <th className="text-start py-1">{t.table.date}</th>
                <th className="text-end py-1">{t.table.value}</th>
                <th className="text-end py-1">%</th>
              </tr>
            </thead>
            <tbody>
              {data.observations
                .slice()
                .reverse()
                .map((o) => (
                  <tr key={o.id} className="border-t" style={{ borderColor: "var(--gridline)" }}>
                    <td className="py-1 tabular" dir="ltr">
                      {new Date(o.observed_at).toISOString().slice(0, 10)}
                    </td>
                    <td className="py-1 text-end tabular" dir="ltr">
                      {o.value.toLocaleString()}
                    </td>
                    <td className="py-1 text-end tabular" dir="ltr">
                      {o.pct_change === null ? "—" : `${o.pct_change.toFixed(2)}%`}
                    </td>
                  </tr>
                ))}
            </tbody>
          </table>
        </div>
      </Card>
    </>
  );
}
