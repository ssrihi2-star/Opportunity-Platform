"use client";

import Link from "next/link";
import { usePathname } from "next/navigation";
import { useApp } from "@/lib/providers";

export function Card({
  title,
  children,
  note,
  actions,
}: {
  title?: string;
  children: React.ReactNode;
  note?: string;
  actions?: React.ReactNode;
}) {
  return (
    <section className="card p-4">
      {(title || actions) && (
        <div className="flex items-start gap-3 mb-1">
          {title && <h2 className="text-sm font-semibold me-auto">{title}</h2>}
          {actions}
        </div>
      )}
      {note && <p className="text-xs text-[var(--text-muted)] mb-3">{note}</p>}
      {children}
    </section>
  );
}

export function StatTile({ label, value }: { label: string; value: number | string }) {
  return (
    <div className="card p-4">
      <div className="text-xs text-[var(--text-secondary)]">{label}</div>
      <div className="text-2xl mt-1 tabular" dir="ltr">
        {typeof value === "number" ? value.toLocaleString() : value}
      </div>
    </div>
  );
}

const STATUS_STYLE: Record<string, { color: string; icon: string }> = {
  ok: { color: "var(--status-good)", icon: "●" },
  succeeded: { color: "var(--status-good)", icon: "●" },
  active: { color: "var(--status-good)", icon: "●" },
  partial: { color: "var(--status-warning)", icon: "▲" },
  anomaly: { color: "var(--status-warning)", icon: "▲" },
  running: { color: "var(--status-warning)", icon: "▲" },
  stale: { color: "var(--status-serious)", icon: "▲" },
  disabled: { color: "var(--text-muted)", icon: "○" },
  failed: { color: "var(--status-critical)", icon: "■" },
  error: { color: "var(--status-critical)", icon: "■" },
  // trend lifecycle
  confirmed: { color: "var(--status-good)", icon: "●" },
  candidate: { color: "var(--text-muted)", icon: "○" },
  weakening: { color: "var(--status-serious)", icon: "▼" },
  ended: { color: "var(--text-muted)", icon: "□" },
  invalidated: { color: "var(--status-critical)", icon: "✕" },
  // opportunity lifecycle
  researching: { color: "var(--series-1)", icon: "◐" },
  watchlist: { color: "var(--text-muted)", icon: "◇" },
  promising: { color: "var(--status-good)", icon: "▲" },
  strong_evidence: { color: "var(--status-good)", icon: "●" },
  archived: { color: "var(--text-muted)", icon: "□" },
  // risk levels: prefixed so "low" as a risk is never confused with a status
  risk_low: { color: "var(--status-good)", icon: "●" },
  risk_moderate: { color: "var(--status-warning)", icon: "▲" },
  risk_high: { color: "var(--status-serious)", icon: "▲" },
  risk_very_high: { color: "var(--status-critical)", icon: "■" },
  // trend stages
  accelerating: { color: "var(--status-good)", icon: "▲" },
  early_adoption: { color: "var(--status-good)", icon: "▲" },
  emerging: { color: "var(--series-1)", icon: "▲" },
  weak_signal: { color: "var(--text-muted)", icon: "○" },
  mainstream: { color: "var(--series-1)", icon: "●" },
  mature: { color: "var(--text-muted)", icon: "●" },
  declining: { color: "var(--status-critical)", icon: "▼" },
};

/** A 0-100 bar. Reads as a proportion at a glance, with the number still shown. */
export function ScoreBar({ value, max = 100 }: { value: number; max?: number }) {
  const pct = Math.max(0, Math.min(100, (value / max) * 100));
  return (
    <span className="inline-flex items-center gap-2">
      <span
        aria-hidden
        className="inline-block h-1.5 w-16 rounded"
        style={{ background: "var(--gridline)" }}
      >
        <span
          className="block h-1.5 rounded"
          style={{ width: `${pct}%`, background: "var(--series-1)" }}
        />
      </span>
      <span className="tabular text-sm" dir="ltr">
        {value.toFixed(0)}
      </span>
    </span>
  );
}

/**
 * Look a code up in a translated vocabulary. Stage and lifecycle codes arrive
 * from the API as raw strings, so the dictionary is indexed loosely and falls
 * back to the readable form of the code itself rather than showing nothing.
 */
export function vocab(dict: object, key: string): string {
  return (dict as Record<string, string>)[key] ?? key.replace(/_/g, " ");
}

/** Status is never colour alone: every badge ships an icon and a label. */
export function StatusBadge({ status, label }: { status: string; label?: string }) {
  const style = STATUS_STYLE[status] ?? { color: "var(--text-muted)", icon: "○" };
  return (
    <span className="inline-flex items-center gap-1.5 text-xs">
      <span aria-hidden style={{ color: style.color }}>
        {style.icon}
      </span>
      <span>{label ?? status}</span>
    </span>
  );
}

export function Disclaimer({ text }: { text: string }) {
  const { t, locale } = useApp();
  const body = locale === "ar" ? t.disclaimerBody : text;
  return (
    <aside
      className="card p-3 text-xs text-[var(--text-secondary)] border-s-4"
      style={{ borderInlineStartColor: "var(--status-warning)" }}
    >
      <strong className="block mb-1 text-[var(--text-primary)]">{t.disclaimerTitle}</strong>
      {body}
    </aside>
  );
}

export function Shell({ children }: { children: React.ReactNode }) {
  const { t, locale, setLocale, theme, toggleTheme, token, logout } = useApp();
  const pathname = usePathname();

  const links = [
    { href: "/", label: t.nav.overview },
    { href: "/trends", label: t.nav.trends },
    { href: "/opportunities", label: t.nav.opportunities },
    { href: "/signals", label: t.nav.signals },
    { href: "/sources", label: t.nav.sources },
    { href: "/review", label: t.nav.review },
    { href: "/settings", label: t.nav.settings },
  ];
  const planned = [t.nav.reports, t.nav.backtesting];

  return (
    <div className="min-h-screen">
      <header className="border-b" style={{ borderColor: "var(--border-ring)" }}>
        <div className="max-w-6xl mx-auto px-4 py-3 flex flex-wrap items-center gap-3">
          <div className="me-auto">
            <div className="font-semibold">{t.appName}</div>
            <div className="text-xs text-[var(--text-muted)]">{t.tagline}</div>
          </div>
          <button onClick={() => setLocale(locale === "en" ? "ar" : "en")} className="btn-ghost text-xs">
            {locale === "en" ? "العربية" : "English"}
          </button>
          <button onClick={toggleTheme} className="btn-ghost text-xs">
            {theme === "light" ? "Dark" : "Light"}
          </button>
          {token && (
            <button onClick={logout} className="btn-ghost text-xs">
              {t.logout}
            </button>
          )}
        </div>
        <nav className="max-w-6xl mx-auto px-4 pb-2 flex flex-wrap gap-4 text-sm">
          {links.map((l) => (
            <Link
              key={l.href}
              href={l.href}
              className={pathname === l.href ? "font-semibold underline" : "text-[var(--text-secondary)]"}
            >
              {l.label}
            </Link>
          ))}
          {planned.map((label) => (
            <span key={label} className="text-[var(--text-muted)]" title={t.comingSoon}>
              {label} ·
            </span>
          ))}
        </nav>
      </header>
      <main className="max-w-6xl mx-auto px-4 py-6 space-y-6">{children}</main>
    </div>
  );
}
