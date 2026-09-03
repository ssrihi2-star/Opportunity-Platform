"use client";

import { useCallback, useEffect, useState } from "react";
import { Guard } from "@/components/Guard";
import { Card, StatusBadge } from "@/components/ui";
import { api, upload } from "@/lib/api";
import { useApp } from "@/lib/providers";
import type { AdapterInfo, Credential, RunResult, Source, SourceHealth, SourceRun } from "@/lib/types";

export default function SourcesPage() {
  return (
    <Guard>
      <Body />
    </Guard>
  );
}

function Body() {
  const { t, token } = useApp();
  const [sources, setSources] = useState<Source[]>([]);
  const [adapters, setAdapters] = useState<Record<string, AdapterInfo>>({});
  const [open, setOpen] = useState<string | null>(null);

  const load = useCallback(() => {
    if (!token) return;
    api<Source[]>("/sources", { token })
      // Enabled sources first: those are the ones actually collecting.
      .then((list) =>
        setSources(
          [...list].sort(
            (a, b) => Number(b.enabled) - Number(a.enabled) || a.name.localeCompare(b.name),
          ),
        ),
      )
      .catch(() => setSources([]));
    api<AdapterInfo[]>("/sources/adapters", { token })
      .then((list) => setAdapters(Object.fromEntries(list.map((a) => [a.adapter_key, a]))))
      .catch(() => setAdapters({}));
  }, [token]);

  useEffect(load, [load]);

  return (
    <>
      <h1 className="text-xl font-semibold">{t.sources.title}</h1>

      <div className="space-y-4">
        {sources.map((s) => (
          <SourceCard
            key={s.id}
            source={s}
            adapter={adapters[s.adapter_key]}
            expanded={open === s.id}
            onToggle={() => setOpen(open === s.id ? null : s.id)}
            onChanged={load}
          />
        ))}
      </div>

      <Card title={t.sources.catalogue} note="What can be plugged in, and what each upstream allows.">
        <div className="overflow-x-auto">
          <table className="w-full text-sm">
            <thead className="text-xs text-[var(--text-muted)]">
              <tr>
                <th className="text-start py-1">Adapter</th>
                <th className="text-start py-1 ps-3">{t.sources.needsNetwork}</th>
                <th className="text-start py-1 ps-3">{t.sources.credentials}</th>
                <th className="text-start py-1 ps-3">{t.sources.rateLimit}</th>
              </tr>
            </thead>
            <tbody>
              {Object.values(adapters).map((a) => (
                <tr key={a.adapter_key} className="border-t" style={{ borderColor: "var(--gridline)" }}>
                  <td className="py-1.5">
                    <div>{a.adapter_key}</div>
                    <div className="text-xs text-[var(--text-muted)]">{a.doc}</div>
                  </td>
                  <td className="py-1.5 ps-3 text-xs">
                    {a.requires_network ? t.sources.needsNetwork : t.sources.offline}
                  </td>
                  <td className="py-1.5 ps-3 text-xs">
                    {a.requires_credentials.length ? a.requires_credentials.join(", ") : "—"}
                  </td>
                  <td className="py-1.5 ps-3 text-xs text-[var(--text-secondary)]">
                    {a.documented_rate_limit}
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

function SourceCard({
  source,
  adapter,
  expanded,
  onToggle,
  onChanged,
}: {
  source: Source;
  adapter?: AdapterInfo;
  expanded: boolean;
  onToggle: () => void;
  onChanged: () => void;
}) {
  const { t, token } = useApp();
  const [busy, setBusy] = useState<string | null>(null);
  const [message, setMessage] = useState<string | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [health, setHealth] = useState<SourceHealth | null>(null);
  const [runs, setRuns] = useState<SourceRun[]>([]);
  const [creds, setCreds] = useState<Credential[]>([]);
  const [credKey, setCredKey] = useState("");
  const [credValue, setCredValue] = useState("");

  const loadDetail = useCallback(() => {
    if (!token) return;
    api<SourceHealth>(`/sources/${source.id}/health`, { token }).then(setHealth).catch(() => null);
    api<SourceRun[]>(`/sources/${source.id}/runs?limit=8`, { token }).then(setRuns).catch(() => null);
    api<Credential[]>(`/sources/${source.id}/credentials`, { token })
      .then(setCreds)
      .catch(() => setCreds([]));
  }, [token, source.id]);

  useEffect(() => {
    if (expanded) loadDetail();
  }, [expanded, loadDetail]);

  async function act<T>(name: string, fn: () => Promise<T>, describe: (r: T) => string) {
    setBusy(name);
    setError(null);
    setMessage(null);
    try {
      setMessage(describe(await fn()));
      loadDetail();
      onChanged();
    } catch (err) {
      setError(err instanceof Error ? err.message : "failed");
    } finally {
      setBusy(null);
    }
  }

  const run = () =>
    act(
      "run",
      () => api<RunResult>(`/sources/${source.id}/run`, { method: "POST", token }),
      (r) =>
        `${r.status}: stored ${r.stored}, duplicates ${r.duplicates}, observations ${r.observations}, ` +
        `${r.http_requests} HTTP requests${r.error ? ` — ${r.error}` : ""}`,
    );

  const probe = () =>
    act(
      "probe",
      () => api<SourceHealth>(`/sources/${source.id}/health?probe=true`, { token }),
      (h) => `${h.probe_healthy ? "OK" : "Problem"}: ${h.probe ?? "no detail"}`,
    );

  const toggleEnabled = () =>
    act(
      "toggle",
      () =>
        api<Source>(`/sources/${source.id}`, {
          method: "PATCH",
          token,
          body: JSON.stringify({ enabled: !source.enabled }),
        }),
      (s) => (s.enabled ? "Enabled." : "Disabled."),
    );

  const saveCredential = () =>
    act(
      "cred",
      () =>
        api<Credential>(`/sources/${source.id}/credentials`, {
          method: "PUT",
          token,
          body: JSON.stringify({ key: credKey, value: credValue }),
        }),
      (c) => {
        setCredKey("");
        setCredValue("");
        return `Stored credential '${c.key}' (${c.hint}).`;
      },
    );

  const deleteCredential = (key: string) =>
    act(
      `del:${key}`,
      () => api<{ detail: string }>(`/sources/${source.id}/credentials/${key}`, { method: "DELETE", token }),
      (r) => r.detail,
    );

  const uploadCsv = (file: File) => {
    const form = new FormData();
    form.append("file", file);
    return act(
      "csv",
      () => upload<{ rows_parsed: number; detail: string }>(`/sources/${source.id}/upload-csv`, form, token),
      (r) => r.detail,
    );
  };

  const missing = health?.missing_credentials ?? [];

  return (
    <Card
      title={source.name}
      note={source.notes ?? undefined}
      actions={
        <button onClick={onToggle} className="btn-ghost text-xs">
          {expanded ? "▾" : "▸"}
        </button>
      }
    >
      <div className="flex flex-wrap items-center gap-3 text-sm mb-2">
        <StatusBadge status={source.enabled ? source.status : "disabled"} />
        <span className="text-[var(--text-secondary)]">{source.adapter_key}</span>
        {adapter?.requires_network && (
          <span className="text-xs text-[var(--text-muted)]">{t.sources.needsNetwork}</span>
        )}
        {missing.length > 0 && (
          <StatusBadge status="failed" label={`${t.sources.missingCredential}: ${missing.join(", ")}`} />
        )}
        <span className="tabular text-xs text-[var(--text-muted)] ms-auto whitespace-nowrap">
          {t.table.reliability} <span dir="ltr">{source.reliability.toFixed(2)}</span>
        </span>
      </div>

      <div className="text-xs text-[var(--text-muted)] mb-3">
        <span dir="ltr">{source.schedule_cron}</span> · group {source.source_group} ·{" "}
        <span dir="ltr">
          {source.last_success_at ? new Date(source.last_success_at).toLocaleString() : "never run"}
        </span>
        {adapter && <> · {adapter.documented_rate_limit}</>}
      </div>

      <div className="flex flex-wrap gap-2">
        <button onClick={run} disabled={!source.enabled || busy === "run"} className="btn text-sm">
          {busy === "run" ? t.sources.running : t.sources.run}
        </button>
        <button onClick={probe} disabled={busy === "probe"} className="btn-ghost text-sm">
          {busy === "probe" ? t.sources.probing : t.sources.probe}
        </button>
        <button onClick={toggleEnabled} disabled={busy === "toggle"} className="btn-ghost text-sm">
          {source.enabled ? t.sources.disable : t.sources.enable}
        </button>
      </div>

      {message && <p className="mt-2 text-sm text-[var(--text-secondary)]">{message}</p>}
      {error && (
        <p className="mt-2 text-sm" style={{ color: "var(--status-critical)" }}>
          ■ {error}
        </p>
      )}

      {expanded && (
        <div className="mt-4 space-y-4 border-t pt-4" style={{ borderColor: "var(--gridline)" }}>
          {adapter && adapter.requires_credentials.length > 0 && (
            <div>
              <h3 className="text-sm font-semibold mb-2">{t.sources.credentials}</h3>
              <ul className="text-sm space-y-1 mb-2">
                {creds.map((c) => (
                  <li key={c.key} className="flex items-center gap-3">
                    <span className="tabular" dir="ltr">
                      {c.key}: {c.hint}
                    </span>
                    <button
                      onClick={() => deleteCredential(c.key)}
                      className="btn-ghost text-xs"
                      disabled={busy === `del:${c.key}`}
                    >
                      {t.sources.delete}
                    </button>
                  </li>
                ))}
                {creds.length === 0 && (
                  <li className="text-[var(--text-muted)] text-xs">{t.sources.noCredentials}</li>
                )}
              </ul>
              <div className="flex flex-wrap gap-2 items-end">
                <label className="text-xs">
                  {t.sources.credentialKey}
                  <input
                    value={credKey}
                    onChange={(e) => setCredKey(e.target.value)}
                    placeholder={adapter.requires_credentials[0]}
                    className="field mt-1 block text-sm"
                  />
                </label>
                <label className="text-xs">
                  {t.sources.credentialValue}
                  <input
                    type="password"
                    value={credValue}
                    onChange={(e) => setCredValue(e.target.value)}
                    className="field mt-1 block text-sm"
                  />
                </label>
                <button
                  onClick={saveCredential}
                  disabled={!credKey || !credValue || busy === "cred"}
                  className="btn text-sm"
                >
                  {t.sources.addCredential}
                </button>
              </div>
            </div>
          )}

          {source.adapter_key === "csv_import" && (
            <div>
              <h3 className="text-sm font-semibold mb-2">{t.sources.uploadCsv}</h3>
              <input
                type="file"
                accept=".csv,text/csv"
                className="text-sm"
                onChange={(e) => {
                  const file = e.target.files?.[0];
                  if (file) uploadCsv(file);
                }}
              />
              <p className="text-xs text-[var(--text-muted)] mt-1">
                Required columns: observed_at, entity_name, signal_type, value.
              </p>
            </div>
          )}

          <div>
            <h3 className="text-sm font-semibold mb-2">{t.sources.history}</h3>
            <div className="overflow-x-auto">
              <table className="w-full text-sm">
                <thead className="text-xs text-[var(--text-muted)]">
                  <tr>
                    <th className="text-start py-1">{t.table.status}</th>
                    <th className="text-start py-1 ps-3">{t.table.started}</th>
                    <th className="text-end py-1 ps-3">{t.table.records}</th>
                    <th className="text-end py-1 ps-3">{t.table.stored}</th>
                    <th className="text-end py-1 ps-3">{t.table.duplicates}</th>
                    <th className="text-end py-1 ps-3">{t.table.requests}</th>
                  </tr>
                </thead>
                <tbody>
                  {runs.map((r) => (
                    <tr key={r.id} className="border-t align-top" style={{ borderColor: "var(--gridline)" }}>
                      <td className="py-1.5">
                        <StatusBadge status={r.status} />
                        {r.error && (
                          <div className="text-xs mt-1" style={{ color: "var(--status-serious)" }}>
                            {r.error}
                          </div>
                        )}
                      </td>
                      <td className="py-1.5 ps-3 tabular text-xs" dir="ltr">
                        {new Date(r.started_at).toLocaleString()}
                      </td>
                      <td className="py-1.5 ps-3 text-end tabular" dir="ltr">{r.records_fetched}</td>
                      <td className="py-1.5 ps-3 text-end tabular" dir="ltr">{r.records_stored}</td>
                      <td className="py-1.5 ps-3 text-end tabular" dir="ltr">{r.records_duplicate}</td>
                      <td className="py-1.5 ps-3 text-end tabular" dir="ltr">{r.http_requests}</td>
                    </tr>
                  ))}
                  {runs.length === 0 && (
                    <tr>
                      <td colSpan={6} className="py-2 text-[var(--text-muted)]">
                        {t.table.none}
                      </td>
                    </tr>
                  )}
                </tbody>
              </table>
            </div>
          </div>
        </div>
      )}
    </Card>
  );
}
