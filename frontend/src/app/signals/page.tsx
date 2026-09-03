"use client";

import Link from "next/link";
import { useEffect, useState } from "react";
import { Guard } from "@/components/Guard";
import { Card } from "@/components/ui";
import { api } from "@/lib/api";
import { useApp } from "@/lib/providers";
import type { Page, SignalRow } from "@/lib/types";

export default function SignalsPage() {
  return (
    <Guard>
      <SignalsBody />
    </Guard>
  );
}

function SignalsBody() {
  const { t, token } = useApp();
  const [data, setData] = useState<Page<SignalRow> | null>(null);
  const [query, setQuery] = useState("");

  useEffect(() => {
    if (!token) return;
    api<Page<SignalRow>>("/signals?limit=200", { token }).then(setData).catch(() => setData(null));
  }, [token]);

  const rows = (data?.items ?? []).filter((r) => {
    const q = query.toLowerCase();
    return (
      !q ||
      r.signal_type.includes(q) ||
      (r.entity_name ?? "").toLowerCase().includes(q) ||
      (r.source_slug ?? "").toLowerCase().includes(q)
    );
  });

  return (
    <>
      <h1 className="text-xl font-semibold">{t.signal.title}</h1>
      <input
        value={query}
        onChange={(e) => setQuery(e.target.value)}
        placeholder={t.signal.filter}
        className="field text-sm w-full max-w-xs"
      />
      <Card>
        <div className="overflow-x-auto">
          <table className="w-full text-sm">
            <thead className="text-xs text-[var(--text-muted)]">
              <tr>
                <th className="text-start py-1">{t.table.entity}</th>
                <th className="text-start py-1 ps-3">{t.table.signal}</th>
                <th className="text-start py-1 ps-3">{t.table.class}</th>
                <th className="text-start py-1 ps-3">{t.table.source}</th>
                <th className="text-start py-1 ps-3">{t.table.geo}</th>
                <th className="text-start py-1 ps-3">{t.table.proxy}</th>
              </tr>
            </thead>
            <tbody>
              {rows.map((r) => (
                <tr key={r.id} className="border-t" style={{ borderColor: "var(--gridline)" }}>
                  <td className="py-1.5">{r.entity_name}</td>
                  <td className="py-1.5 ps-3">
                    <Link href={`/signals/${r.id}`} className="underline">
                      {r.signal_type}
                    </Link>
                  </td>
                  <td className="py-1.5 ps-3 text-[var(--text-secondary)]">{r.signal_class}</td>
                  <td className="py-1.5 ps-3 text-[var(--text-secondary)]">{r.source_slug}</td>
                  <td className="py-1.5 ps-3 text-[var(--text-secondary)]">{r.geo_scope}</td>
                  <td className="py-1.5 ps-3">{r.is_proxy ? "yes" : "no"}</td>
                </tr>
              ))}
              {rows.length === 0 && (
                <tr>
                  <td colSpan={6} className="py-3 text-[var(--text-muted)]">
                    {t.table.none}
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
