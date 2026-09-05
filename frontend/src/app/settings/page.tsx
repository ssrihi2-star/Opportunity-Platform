"use client";

import { useEffect, useState } from "react";
import { Guard } from "@/components/Guard";
import { Notifications } from "@/components/Notifications";
import { Card } from "@/components/ui";
import { api } from "@/lib/api";
import { useApp } from "@/lib/providers";
import type { Preferences } from "@/lib/types";

export default function SettingsPage() {
  return (
    <Guard>
      <Body />
    </Guard>
  );
}

function Body() {
  const { t } = useApp();
  return (
    <>
      <h1 className="text-xl font-semibold">{t.settings.title}</h1>
      <Preferences />
      <Notifications />
    </>
  );
}

function Preferences() {
  const { t, token } = useApp();
  const [prefs, setPrefs] = useState<Preferences | null>(null);
  const [saved, setSaved] = useState(false);
  const [error, setError] = useState<string | null>(null);

  useEffect(() => {
    if (!token) return;
    api<Preferences>("/preferences", { token }).then(setPrefs).catch((e) => setError(e.message));
  }, [token]);

  async function save(event: React.FormEvent) {
    event.preventDefault();
    if (!prefs) return;
    setError(null);
    try {
      const updated = await api<Preferences>("/preferences", {
        method: "PATCH",
        token,
        body: JSON.stringify(prefs),
      });
      setPrefs(updated);
      setSaved(true);
      setTimeout(() => setSaved(false), 2500);
    } catch (err) {
      setError(err instanceof Error ? err.message : "failed");
    }
  }

  if (error) return <p style={{ color: "var(--status-critical)" }}>■ {error}</p>;
  if (!prefs) return <p className="text-sm text-[var(--text-muted)]">{t.loading}</p>;

  const list = (value: string) => value.split(",").map((s) => s.trim()).filter(Boolean);

  return (
    <Card>
      <form onSubmit={save} className="space-y-4 max-w-lg text-sm">
        <label className="block">
          {t.settings.countries}
          <input
            value={prefs.countries.join(", ")}
            onChange={(e) => setPrefs({ ...prefs, countries: list(e.target.value) })}
            className="field mt-1 w-full"
          />
        </label>
        <label className="block">
          {t.settings.industries}
          <input
            value={prefs.industries.join(", ")}
            onChange={(e) => setPrefs({ ...prefs, industries: list(e.target.value) })}
            className="field mt-1 w-full"
          />
        </label>
        <label className="block">
          {t.settings.maxRisk}
          <select
            value={prefs.max_risk_level}
            onChange={(e) => setPrefs({ ...prefs, max_risk_level: e.target.value })}
            className="field mt-1 w-full"
          >
            {["low", "medium", "high", "very_high"].map((v) => (
              <option key={v} value={v}>
                {v}
              </option>
            ))}
          </select>
        </label>
        <label className="block">
          {t.settings.minConfidence} ({prefs.min_confidence.toFixed(2)})
          <input
            type="range"
            min={0}
            max={1}
            step={0.01}
            value={prefs.min_confidence}
            onChange={(e) => setPrefs({ ...prefs, min_confidence: Number(e.target.value) })}
            className="mt-1 w-full"
          />
        </label>
        <label className="block">
          {t.settings.capitalMax}
          <input
            type="number"
            min={0}
            value={prefs.capital_max_usd}
            onChange={(e) => setPrefs({ ...prefs, capital_max_usd: Number(e.target.value) })}
            className="field mt-1 w-full tabular"
            dir="ltr"
          />
        </label>
        <label className="block">
          {t.settings.horizon}
          <select
            value={prefs.time_horizon}
            onChange={(e) => setPrefs({ ...prefs, time_horizon: e.target.value })}
            className="field mt-1 w-full"
          >
            {["short", "medium", "long"].map((v) => (
              <option key={v} value={v}>
                {v}
              </option>
            ))}
          </select>
        </label>
        <button type="submit" className="btn">
          {t.settings.save}
        </button>
        {saved && (
          <span className="ms-3" style={{ color: "var(--status-good)" }}>
            ● {t.settings.saved}
          </span>
        )}
      </form>
    </Card>
  );
}
