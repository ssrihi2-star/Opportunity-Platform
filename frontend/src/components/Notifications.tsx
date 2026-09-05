"use client";

import { useCallback, useEffect, useRef, useState } from "react";
import { Card, StatusBadge } from "@/components/ui";
import { api } from "@/lib/api";
import { useApp } from "@/lib/providers";
import type { ChannelLink, Digest, DigestFrequency, Profile } from "@/lib/types";

/**
 * How often an outstanding link code is checked.
 *
 * Verification happens when the user sends the code into the chat and Telegram
 * calls the webhook, so the only way this page can learn about it is to ask.
 * Five seconds is short enough to feel instant and long enough that a user who
 * walks away does not leave the browser hammering the API.
 */
const POLL_MS = 5000;

const FREQUENCIES: DigestFrequency[] = ["off", "daily", "weekly"];

/**
 * Notifications: what the user is told, where, and whether it is linked.
 *
 * Three things that were previously reachable only through the API:
 *
 * 1. **Digest frequency** — `PUT /me/profile` with only `digest_frequency` in
 *    the body. The endpoint applies exactly the fields sent, so the rest of the
 *    profile (countries, capital, risk tolerance…) is untouched; sending the
 *    whole profile back would have overwritten fields this form never shows.
 * 2. **Telegram linking** — the code proves the user owns the chat, so the code
 *    is issued here and redeemed inside Telegram. This panel polls the *specific*
 *    link it created, stops as soon as it is verified, and never invents an
 *    expiry: the deadline shown is `link_code_expires_at` from the row the
 *    webhook itself checks.
 * 3. **Recent digests** — what the nightly run stored, rendered readably.
 *
 * Weekly is offered as generation only. Weekly digests are written and readable
 * here, but only **daily** digests are delivered to Telegram, and the label says
 * so rather than implying a message that will never arrive.
 */
export function Notifications() {
  const { t, token } = useApp();
  const [frequency, setFrequency] = useState<DigestFrequency | null>(null);
  const [saved, setSaved] = useState(false);
  const [links, setLinks] = useState<ChannelLink[] | null>(null);
  const [pending, setPending] = useState<ChannelLink | null>(null);
  const [digests, setDigests] = useState<Digest[] | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [notice, setNotice] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);
  const [copied, setCopied] = useState(false);
  const [polling, setPolling] = useState(false);

  // The link this page is waiting on. A ref, so the polling interval always sees
  // the current value without being torn down and rebuilt on every render.
  const watching = useRef<string | null>(null);
  const timer = useRef<ReturnType<typeof setInterval> | null>(null);

  const stopPolling = useCallback(() => {
    if (timer.current) clearInterval(timer.current);
    timer.current = null;
    watching.current = null;
    setPolling(false);
  }, []);

  const load = useCallback(async () => {
    if (!token) return;
    try {
      const [profile, channels, recent] = await Promise.all([
        api<Profile>("/me/profile", { token }),
        api<ChannelLink[]>("/me/channels", { token }),
        api<Digest[]>("/me/digests?limit=5", { token }),
      ]);
      setFrequency(profile.digest_frequency);
      setLinks(channels);
      setDigests(recent);
      setError(null);
    } catch (err) {
      setError(err instanceof Error ? err.message : t.notifications.error);
    }
  }, [token, t.notifications.error]);

  useEffect(() => {
    void load();
  }, [load]);

  // Leaving the page stops the polling. So does verification, unlinking, or
  // asking for a new code (which starts watching the new link instead).
  useEffect(() => stopPolling, [stopPolling]);

  const poll = useCallback(
    (linkId: string) => {
      stopPolling();
      watching.current = linkId;
      setPolling(true);
      timer.current = setInterval(async () => {
        if (watching.current !== linkId || !token) return;
        try {
          const channels = await api<ChannelLink[]>("/me/channels", { token });
          const mine = channels.find((c) => c.id === linkId);
          setLinks(channels);
          if (!mine) {
            // Deleted elsewhere: nothing left to wait for.
            setPending(null);
            stopPolling();
            return;
          }
          const deadline = mine.link_code_expires_at
            ? new Date(mine.link_code_expires_at).getTime()
            : null;
          if (deadline !== null && deadline < Date.now()) {
            // The backend will refuse this code from now on, so asking again can
            // only ever return the same answer. Stop, and leave the expired state
            // and the fresh-code action on screen.
            stopPolling();
            return;
          }
          if (mine.verified) {
            setPending(null);
            setNotice(t.notifications.verifiedNotice);
            stopPolling();
            void load();
          }
        } catch {
          // A failed poll is not worth interrupting the user for; the next tick
          // tries again, and the states below still show what is outstanding.
        }
      }, POLL_MS);
    },
    [load, stopPolling, t.notifications.verifiedNotice, token],
  );

  async function requestCode() {
    if (!token) return;
    setBusy(true);
    setError(null);
    setNotice(null);
    setCopied(false);
    try {
      // Replace an outstanding code rather than adding a second one: two live
      // codes for one account is two chances for a code to be pasted somewhere
      // it should not be.
      const outstanding = (links ?? []).filter((l) => l.channel === "telegram" && !l.verified);
      for (const link of outstanding) {
        await api<void>(`/me/channels/${link.id}`, { method: "DELETE", token });
      }
      const created = await api<ChannelLink>("/me/channels", {
        method: "POST",
        token,
        body: JSON.stringify({ channel: "telegram" }),
      });
      setPending(created);
      setLinks(await api<ChannelLink[]>("/me/channels", { token }));
      poll(created.id);
    } catch (err) {
      setError(err instanceof Error ? err.message : t.notifications.error);
    } finally {
      setBusy(false);
    }
  }

  async function unlink(linkId: string) {
    if (!token) return;
    if (!window.confirm(t.notifications.unlinkConfirm)) return;
    setBusy(true);
    setError(null);
    try {
      await api<void>(`/me/channels/${linkId}`, { method: "DELETE", token });
      if (watching.current === linkId) {
        setPending(null);
        stopPolling();
      }
      setLinks(await api<ChannelLink[]>("/me/channels", { token }));
      setNotice(t.notifications.unlinkedNotice);
    } catch (err) {
      setError(err instanceof Error ? err.message : t.notifications.error);
    } finally {
      setBusy(false);
    }
  }

  async function chooseFrequency(next: DigestFrequency) {
    if (!token) return;
    setBusy(true);
    setError(null);
    try {
      // Only the one field. Everything else on the profile stays as it is.
      const updated = await api<Profile>("/me/profile", {
        method: "PUT",
        token,
        body: JSON.stringify({ digest_frequency: next }),
      });
      setFrequency(updated.digest_frequency);
      setSaved(true);
      setTimeout(() => setSaved(false), 2500);
    } catch (err) {
      setError(err instanceof Error ? err.message : t.notifications.error);
    } finally {
      setBusy(false);
    }
  }

  async function copyCode(code: string) {
    try {
      await navigator.clipboard.writeText(code);
      setCopied(true);
      setTimeout(() => setCopied(false), 2000);
    } catch {
      // No clipboard permission: the code is on screen and selectable anyway.
    }
  }

  const telegram = (links ?? []).filter((l) => l.channel === "telegram");
  const verified = telegram.find((l) => l.verified);
  const outstanding = pending ?? telegram.find((l) => !l.verified) ?? null;
  const expired =
    !!outstanding?.link_code_expires_at && new Date(outstanding.link_code_expires_at).getTime() < Date.now();
  /** Only the response that created a link carries the code; it is never echoed back. */
  const code = outstanding?.link_code ?? null;

  return (
    <>
      <h2 className="text-lg font-semibold">{t.notifications.title}</h2>

      {error && (
        <p className="text-sm" style={{ color: "var(--status-critical)" }}>
          ■ {error}
        </p>
      )}
      {notice && (
        <p className="text-sm" style={{ color: "var(--status-good)" }}>
          ● {notice}
        </p>
      )}

      <Card title={t.notifications.digestTitle} note={t.notifications.digestNote}>
        <div className="max-w-lg text-sm space-y-2">
          <label className="block">
            {t.notifications.digestFrequency}
            <select
              className="field mt-1 w-full"
              value={frequency ?? "off"}
              disabled={frequency === null || busy}
              onChange={(e) => void chooseFrequency(e.target.value as DigestFrequency)}
            >
              {FREQUENCIES.map((value) => (
                <option key={value} value={value}>
                  {t.notifications.frequencyLabels[value]}
                </option>
              ))}
            </select>
          </label>
          {saved && (
            <span style={{ color: "var(--status-good)" }}>● {t.settings.saved}</span>
          )}
          {frequency === "daily" && !verified && (
            <p className="text-xs" style={{ color: "var(--status-warning)" }}>
              ▲ {t.notifications.needsTelegram}
            </p>
          )}
        </div>
      </Card>

      <Card title={t.notifications.telegramTitle} note={t.notifications.telegramNote}>
        <div className="text-sm space-y-3 max-w-2xl">
          {links === null && <p className="text-[var(--text-muted)]">{t.loading}</p>}

          {links !== null && telegram.length === 0 && (
            <p className="text-[var(--text-secondary)]">{t.notifications.telegramNone}</p>
          )}

          {verified && (
            <div className="flex flex-wrap items-center gap-3">
              <StatusBadge status="active" label={t.notifications.verified} />
              {verified.verified_at && (
                <span className="text-xs text-[var(--text-muted)]" dir="ltr">
                  {t.notifications.verifiedAt} {new Date(verified.verified_at).toLocaleString()}
                </span>
              )}
              <button className="btn-ghost text-xs ms-auto" disabled={busy} onClick={() => void unlink(verified.id)}>
                {t.notifications.unlink}
              </button>
            </div>
          )}

          {!verified && (
            <div className="space-y-2">
              <button className="btn" disabled={busy} onClick={() => void requestCode()}>
                {outstanding ? t.notifications.newCode : t.notifications.linkButton}
              </button>

              {outstanding && (
                <div className="card p-3 space-y-2">
                  <StatusBadge status={expired ? "failed" : "running"} label={t.notifications.pending} />
                  {code && (
                    <div>
                      <div className="text-xs text-[var(--text-secondary)]">{t.notifications.codeLabel}</div>
                      <div className="flex items-center gap-2 mt-1">
                        <code
                          className="tabular text-base px-2 py-1 rounded"
                          style={{ background: "var(--gridline)" }}
                          dir="ltr"
                        >
                          /link {code}
                        </code>
                        <button className="btn-ghost text-xs" onClick={() => void copyCode(code)}>
                          {copied ? t.notifications.copied : t.notifications.copy}
                        </button>
                      </div>
                    </div>
                  )}
                  {outstanding.instructions && (
                    <p className="text-xs text-[var(--text-secondary)] whitespace-pre-line">
                      {outstanding.instructions}
                    </p>
                  )}
                  {/* Only a deadline the backend actually stores. A code fetched
                      from GET /me/channels has no code in the response, so there
                      is nothing to expire and nothing is invented. */}
                  {outstanding.link_code_expires_at && (
                    <p className="text-xs" dir="ltr">
                      {expired ? "■ " : "○ "}
                      {expired ? t.notifications.expired : `${t.notifications.expiresAt} `}
                      <span className="tabular">{new Date(outstanding.link_code_expires_at).toLocaleString()}</span>
                    </p>
                  )}
                  {!code && !outstanding.link_code_expires_at && (
                    <p className="text-xs text-[var(--text-muted)]">{t.notifications.codeNotShown}</p>
                  )}
                  <div className="flex flex-wrap gap-2">
                    <button className="btn-ghost text-xs" disabled={busy} onClick={() => void requestCode()}>
                      {t.notifications.newCode}
                    </button>
                    <button className="btn-ghost text-xs" disabled={busy} onClick={() => void unlink(outstanding.id)}>
                      {t.notifications.unlink}
                    </button>
                  </div>
                  {polling && !expired && (
                    <p className="text-xs text-[var(--text-muted)]">○ {t.notifications.polling}</p>
                  )}
                </div>
              )}
            </div>
          )}
        </div>
      </Card>

      <Card title={t.notifications.digestsTitle} note={t.notifications.digestsNote}>
        {digests === null && <p className="text-sm text-[var(--text-muted)]">{t.loading}</p>}
        {digests !== null && digests.length === 0 && (
          <p className="text-sm text-[var(--text-secondary)]">{t.notifications.digestsEmpty}</p>
        )}
        <div className="space-y-3">
          {(digests ?? []).map((digest) => (
            <DigestCard key={digest.id} digest={digest} />
          ))}
        </div>
      </Card>
    </>
  );
}

/** One stored digest, rendered as something a person can read. */
function DigestCard({ digest }: { digest: Digest }) {
  const { t } = useApp();
  const sections = digest.sections ?? {};
  const watched = sections.watchlist_changes ?? [];
  const others = sections.other_changes ?? [];
  const alerts = sections.alerts ?? [];
  // `sections.period.key` is the canonical period the scheduler wrote
  // (`daily:2026-09-06`). On-demand digests have no key, so they fall back to
  // the window's own start date rather than claiming a period they do not have.
  const label = sections.period?.key?.split(":")[1] ?? new Date(digest.period_start).toLocaleDateString();

  return (
    <article className="card p-3 text-sm space-y-2">
      <header className="flex flex-wrap items-center gap-2">
        <strong className="me-auto">
          {digest.frequency === "weekly" ? t.notifications.weeklyDigest : t.notifications.dailyDigest} ·{" "}
          <span className="tabular" dir="ltr">
            {label}
          </span>
        </strong>
        <span className="text-xs text-[var(--text-muted)] tabular" dir="ltr">
          {new Date(digest.generated_at).toLocaleString()}
        </span>
        <StatusBadge
          status={digest.item_count > 0 ? "active" : "disabled"}
          label={`${digest.item_count} ${t.notifications.items}`}
        />
      </header>

      {digest.item_count === 0 && (
        <p className="text-[var(--text-secondary)]">
          {sections.empty_note ?? t.notifications.digestsEmpty}
        </p>
      )}

      <DigestSection title={t.notifications.watchlistChanges} items={watched} />
      <DigestSection title={t.notifications.otherChanges} items={others} />

      {alerts.length > 0 && (
        <div>
          <h4 className="text-xs font-semibold text-[var(--text-secondary)]">{t.notifications.alerts}</h4>
          <ul className="mt-1 space-y-1">
            {alerts.map((alert, index) => (
              <li key={`${alert.at}-${index}`} className="text-[var(--text-secondary)]">
                {alert.title} — <span className="text-[var(--text-muted)]">{alert.body}</span>
              </li>
            ))}
          </ul>
        </div>
      )}

      {sections.note && <p className="text-xs text-[var(--text-muted)]">{sections.note}</p>}
    </article>
  );
}

function DigestSection({ title, items }: { title: string; items: Digest["sections"]["watchlist_changes"] }) {
  if (!items || items.length === 0) return null;
  return (
    <div>
      <h4 className="text-xs font-semibold text-[var(--text-secondary)]">{title}</h4>
      <ul className="mt-1 space-y-1">
        {items.map((item) => (
          <li key={`${item.opportunity_id}-${item.kind}`}>
            <a className="underline" href={`/opportunities/${item.opportunity_id}`}>
              {item.title}
            </a>{" "}
            <span className="text-xs text-[var(--text-muted)]">{item.kind.replace(/_/g, " ")}</span>
            {item.summary && <span className="text-xs text-[var(--text-secondary)]"> — {item.summary}</span>}
          </li>
        ))}
      </ul>
    </div>
  );
}
