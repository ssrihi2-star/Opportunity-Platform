"use client";

import Link from "next/link";
import { useCallback, useEffect, useMemo, useState } from "react";
import { Guard } from "@/components/Guard";
import { Card, ScoreBar, StatusBadge, ValidationBadge, vocab } from "@/components/ui";
import { api } from "@/lib/api";
import { useApp } from "@/lib/providers";
import type {
  Opportunity,
  Page,
  Watchlist,
  WatchlistIn,
  WatchlistItem,
  WatchTargetKind,
} from "@/lib/types";

export default function WatchlistsPage() {
  return (
    <Guard>
      <Body />
    </Guard>
  );
}

/**
 * Each item type maps to exactly one field. The API performs no cross-field
 * validation, so the form must send the matching field and nothing else.
 * "technology" and "product" have no dedicated column, so their name is
 * carried by the free-text `keyword` field (see the backend model).
 */
const ITEM_TYPES: WatchTargetKind[] = [
  "opportunity",
  "trend",
  "company",
  "technology",
  "product",
  "country",
  "industry",
  "keyword",
];

const RISK_LEVELS = ["low", "moderate", "high", "very_high"];

/** The payload the item form actually submits: only the matching field is set. */
type ItemInput = {
  item_type: WatchTargetKind;
  opportunity_id?: string;
  trend_id?: string;
  entity_id?: string;
  country_code?: string;
  industry?: string;
  keyword?: string;
  label?: string | null;
};

function formatThreshold(v: number): string {
  return Number.isInteger(v) ? String(v) : v.toFixed(1);
}

/** The plain-text body of an item that has no opportunity behind it. */
function itemText(item: WatchlistItem): string {
  switch (item.item_type) {
    case "country":
      return item.country_code ?? "";
    case "industry":
      return item.industry ?? "";
    case "keyword":
    case "technology":
    case "product":
      return item.keyword ?? "";
    case "trend":
      return item.trend_id ?? "";
    case "company":
      return item.entity_id ?? "";
    case "opportunity":
      return item.opportunity_id ?? "";
    default:
      return "";
  }
}

function Body() {
  const { t, token } = useApp();
  const [lists, setLists] = useState<Watchlist[] | null>(null);
  const [opps, setOpps] = useState<Opportunity[]>([]);
  const [error, setError] = useState<string | null>(null);
  const [creating, setCreating] = useState(false);

  const load = useCallback(() => {
    if (!token) return;
    api<Watchlist[]>("/me/watchlists", { token })
      .then(setLists)
      .catch((e) => setError(e.message));
  }, [token]);

  useEffect(load, [load]);

  // Hydrate the feed ONCE and join items against it client-side. The watchlist
  // itself carries no scores; the Opportunity objects here carry both.
  useEffect(() => {
    if (!token) return;
    api<Page<Opportunity>>("/for-you?limit=200", { token })
      .then((p) => setOpps(p.items ?? []))
      .catch(() => setOpps([]));
  }, [token]);

  async function create(payload: WatchlistIn) {
    await api<Watchlist>("/me/watchlists", {
      method: "POST",
      token,
      body: JSON.stringify(payload),
    });
    setCreating(false);
    load();
  }

  if (error) return <p style={{ color: "var(--status-critical)" }}>■ {error}</p>;
  if (!lists) return <p className="text-sm text-[var(--text-muted)]">{t.loading}</p>;

  return (
    <>
      <div className="flex flex-wrap items-start gap-3">
        <div className="me-auto">
          <h1 className="text-xl font-semibold">{t.watchlists.title}</h1>
          <p className="max-w-3xl text-sm text-[var(--text-secondary)]">
            {t.watchlists.subtitle}
          </p>
        </div>
        <button onClick={() => setCreating((v) => !v)} className="btn text-sm">
          {creating ? t.watchlists.cancel : t.watchlists.new}
        </button>
      </div>

      {creating && (
        <Card title={t.watchlists.new}>
          <WatchlistForm
            initial={null}
            submitLabel={t.watchlists.create}
            onSubmit={create}
            onCancel={() => setCreating(false)}
          />
        </Card>
      )}

      {lists.length === 0 ? (
        <Card>
          <p className="py-4 text-sm text-[var(--text-secondary)]">{t.watchlists.empty}</p>
        </Card>
      ) : (
        <div className="space-y-4">
          {lists.map((wl) => (
            <WatchlistCard key={wl.id} wl={wl} opps={opps} onChanged={load} />
          ))}
        </div>
      )}
    </>
  );
}

function WatchlistForm({
  initial,
  submitLabel,
  onSubmit,
  onCancel,
}: {
  initial: Watchlist | null;
  submitLabel: string;
  onSubmit: (payload: WatchlistIn) => Promise<void>;
  onCancel: () => void;
}) {
  const { t } = useApp();
  const [name, setName] = useState(initial?.name ?? "");
  const [description, setDescription] = useState(initial?.description ?? "");
  const [minScore, setMinScore] = useState(
    initial && initial.min_score != null ? String(initial.min_score) : "",
  );
  const [maxRisk, setMaxRisk] = useState(initial?.max_risk_level ?? "");
  const [err, setErr] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);

  function submit(e: React.FormEvent) {
    e.preventDefault();
    const trimmed = name.trim();
    if (!trimmed) {
      setErr(t.watchlists.nameRequired);
      return;
    }
    let min: number | null = null;
    if (minScore.trim() !== "") {
      const n = Number(minScore);
      if (!Number.isFinite(n) || n < 0 || n > 100) {
        setErr(t.watchlists.minScoreRange);
        return;
      }
      min = n;
    }
    setErr(null);
    setBusy(true);
    // `name` is always sent, and nulls are explicit, so PUT fully replaces the
    // four editable fields — including clearing a previously-set threshold.
    onSubmit({
      name: trimmed,
      description: description.trim() === "" ? null : description.trim(),
      min_score: min,
      max_risk_level: maxRisk === "" ? null : maxRisk,
    })
      .catch((e) => setErr(e instanceof Error ? e.message : "failed"))
      .finally(() => setBusy(false));
  }

  return (
    <form onSubmit={submit} className="max-w-lg space-y-4 text-sm">
      <label className="block">
        {t.watchlists.name}
        <input
          value={name}
          onChange={(e) => setName(e.target.value)}
          maxLength={200}
          className="field mt-1 w-full"
        />
      </label>
      <label className="block">
        {t.watchlists.description}
        <textarea
          value={description}
          onChange={(e) => setDescription(e.target.value)}
          maxLength={2000}
          rows={3}
          className="field mt-1 w-full"
        />
      </label>
      <div className="grid gap-4 sm:grid-cols-2">
        <label className="block">
          {t.watchlists.minScore}
          <input
            type="number"
            min={0}
            max={100}
            value={minScore}
            onChange={(e) => setMinScore(e.target.value)}
            placeholder="0–100"
            className="field mt-1 block w-full tabular"
            dir="ltr"
          />
          <span className="mt-1 block text-[11px] text-[var(--text-muted)]">
            {t.watchlists.minScoreHint}
          </span>
        </label>
        <label className="block">
          {t.watchlists.maxRisk}
          <select
            value={maxRisk}
            onChange={(e) => setMaxRisk(e.target.value)}
            className="field mt-1 block w-full"
          >
            <option value="">—</option>
            {RISK_LEVELS.map((v) => (
              <option key={v} value={v}>
                {vocab(t.opportunities.risks_levels, v)}
              </option>
            ))}
          </select>
        </label>
      </div>

      {err && (
        <p className="text-sm" style={{ color: "var(--status-critical)" }}>
          ■ {err}
        </p>
      )}

      <div className="flex flex-wrap gap-2">
        <button type="submit" disabled={busy} className="btn text-sm">
          {busy ? t.watchlists.saving : submitLabel}
        </button>
        <button type="button" onClick={onCancel} className="btn-ghost text-sm">
          {t.watchlists.cancel}
        </button>
      </div>
    </form>
  );
}

function AddItemForm({
  opps,
  onSubmit,
  onCancel,
}: {
  opps: Opportunity[];
  onSubmit: (payload: ItemInput) => Promise<void>;
  onCancel: () => void;
}) {
  const { t } = useApp();
  const [type, setType] = useState<WatchTargetKind>("opportunity");
  const [value, setValue] = useState("");
  const [label, setLabel] = useState("");
  const [err, setErr] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);

  const fieldLabels: Record<WatchTargetKind, string> = {
    opportunity: t.watchlists.opportunityField,
    trend: t.watchlists.trendField,
    company: t.watchlists.companyField,
    technology: t.watchlists.technologyField,
    product: t.watchlists.productField,
    country: t.watchlists.countryField,
    industry: t.watchlists.industryField,
    keyword: t.watchlists.keywordField,
  };

  const noOpportunities = type === "opportunity" && opps.length === 0;

  function submit(e: React.FormEvent) {
    e.preventDefault();
    const v = type === "country" ? value.trim().toUpperCase() : value.trim();
    if (!v) {
      setErr(t.watchlists.required);
      return;
    }
    if (type === "country" && !/^[A-Za-z]{2}$/.test(v)) {
      setErr(t.watchlists.countryCodeInvalid);
      return;
    }
    setErr(null);

    // Build the coherent payload: only the field matching the type is set.
    const payload: ItemInput = { item_type: type };
    if (type === "opportunity") payload.opportunity_id = v;
    else if (type === "trend") payload.trend_id = v;
    else if (type === "company") payload.entity_id = v;
    else if (type === "country") payload.country_code = v;
    else if (type === "industry") payload.industry = v;
    else payload.keyword = v; // technology, product, keyword
    if (label.trim() !== "") payload.label = label.trim();

    setBusy(true);
    onSubmit(payload)
      .catch((e) => setErr(e instanceof Error ? e.message : "failed"))
      .finally(() => setBusy(false));
  }

  return (
    <form onSubmit={submit} className="space-y-4 text-sm">
      <label className="block">
        {t.watchlists.itemType}
        <select
          value={type}
          onChange={(e) => {
            setType(e.target.value as WatchTargetKind);
            setValue("");
          }}
          className="field mt-1 block w-full"
        >
          {ITEM_TYPES.map((v) => (
            <option key={v} value={v}>
              {vocab(t.watchlists.itemTypes, v)}
            </option>
          ))}
        </select>
      </label>

      <label className="block">
        {fieldLabels[type]}
        {type === "opportunity" ? (
          <>
            <select
              value={value}
              onChange={(e) => setValue(e.target.value)}
              className="field mt-1 block w-full"
            >
              <option value="">{t.watchlists.opportunityField}…</option>
              {opps.map((o) => (
                <option key={o.id} value={o.id}>
                  {o.title}
                </option>
              ))}
            </select>
            {noOpportunities && (
              <span className="mt-1 block text-[11px] text-[var(--text-muted)]">
                {t.watchlists.noOpportunities}
              </span>
            )}
          </>
        ) : (
          <>
            <input
              value={value}
              onChange={(e) =>
                setValue(type === "country" ? e.target.value.toUpperCase() : e.target.value)
              }
              maxLength={
                type === "country" ? 2 : type === "industry" ? 80 : type === "keyword" || type === "technology" || type === "product" ? 200 : undefined
              }
              placeholder={type === "country" ? "US" : undefined}
              dir={type === "trend" || type === "company" ? "ltr" : undefined}
              className="field mt-1 block w-full"
            />
            {(type === "trend" || type === "company") && (
              <span className="mt-1 block text-[11px] text-[var(--text-muted)]">
                {type === "trend" ? t.watchlists.trendHint : t.watchlists.companyHint}
              </span>
            )}
          </>
        )}
      </label>

      <label className="block">
        {t.watchlists.label}
        <input
          value={label}
          onChange={(e) => setLabel(e.target.value)}
          maxLength={240}
          className="field mt-1 block w-full"
        />
        <span className="mt-1 block text-[11px] text-[var(--text-muted)]">
          {t.watchlists.labelHint}
        </span>
      </label>

      {err && (
        <p className="text-sm" style={{ color: "var(--status-critical)" }}>
          ■ {err}
        </p>
      )}

      <div className="flex flex-wrap gap-2">
        <button type="submit" disabled={busy || noOpportunities} className="btn text-sm">
          {busy ? t.watchlists.adding : t.watchlists.addItem}
        </button>
        <button type="button" onClick={onCancel} className="btn-ghost text-sm">
          {t.watchlists.cancel}
        </button>
      </div>
    </form>
  );
}

function WatchlistCard({
  wl,
  opps,
  onChanged,
}: {
  wl: Watchlist;
  opps: Opportunity[];
  onChanged: () => void;
}) {
  const { t, token } = useApp();
  const [editing, setEditing] = useState(false);
  const [adding, setAdding] = useState(false);
  const [confirmDelete, setConfirmDelete] = useState(false);
  const [confirmRemoveId, setConfirmRemoveId] = useState<string | null>(null);
  const [busy, setBusy] = useState<string | null>(null);
  const [error, setError] = useState<string | null>(null);

  const oppById = useMemo(() => new Map(opps.map((o) => [o.id, o])), [opps]);

  async function update(payload: WatchlistIn) {
    await api<Watchlist>(`/me/watchlists/${wl.id}`, {
      method: "PUT",
      token,
      body: JSON.stringify(payload),
    });
    setEditing(false);
    onChanged();
  }

  async function addItem(payload: ItemInput) {
    await api<WatchlistItem>(`/me/watchlists/${wl.id}/items`, {
      method: "POST",
      token,
      body: JSON.stringify(payload),
    });
    setAdding(false);
    onChanged();
  }

  async function removeList() {
    setBusy("delete");
    setError(null);
    try {
      await api(`/me/watchlists/${wl.id}`, { method: "DELETE", token });
      onChanged();
    } catch (e) {
      setError(e instanceof Error ? e.message : "failed");
      setBusy(null);
    }
  }

  async function removeItem(itemId: string) {
    setBusy(`remove:${itemId}`);
    setError(null);
    try {
      await api(`/me/watchlists/${wl.id}/items/${itemId}`, { method: "DELETE", token });
      setConfirmRemoveId(null);
      setBusy(null);
      onChanged();
    } catch (e) {
      setError(e instanceof Error ? e.message : "failed");
      setBusy(null);
    }
  }

  if (editing) {
    return (
      <Card title={t.watchlists.edit}>
        <WatchlistForm
          initial={wl}
          submitLabel={t.watchlists.save}
          onSubmit={update}
          onCancel={() => setEditing(false)}
        />
      </Card>
    );
  }

  return (
    <Card>
      <div className="flex flex-wrap items-start gap-2">
        <h2 className="text-sm font-semibold me-auto">{wl.name}</h2>
        <div className="flex flex-wrap gap-2">
          <button onClick={() => setAdding((v) => !v)} className="btn-ghost text-xs">
            {adding ? t.watchlists.cancel : t.watchlists.addItem}
          </button>
          <button onClick={() => setEditing(true)} className="btn-ghost text-xs">
            {t.watchlists.edit}
          </button>
          <button onClick={() => setConfirmDelete((v) => !v)} className="btn-ghost text-xs">
            {t.watchlists.delete}
          </button>
        </div>
      </div>

      {wl.description && (
        <p className="mt-1 text-sm text-[var(--text-secondary)]">{wl.description}</p>
      )}

      {/* min_score is the user's threshold, never a score: plain labelled text. */}
      <div className="mt-2 flex flex-wrap items-center gap-x-4 gap-y-1.5 text-xs text-[var(--text-secondary)]">
        {wl.min_score != null && (
          <span title={t.watchlists.minScoreHint}>
            {t.watchlists.minScore}:{" "}
            <b className="tabular" dir="ltr">
              {formatThreshold(wl.min_score)}
            </b>
          </span>
        )}
        {wl.max_risk_level != null && (
          <span className="inline-flex items-center gap-1.5">
            {t.watchlists.maxRisk}:{" "}
            <StatusBadge
              status={`risk_${wl.max_risk_level}`}
              label={vocab(t.opportunities.risks_levels, wl.max_risk_level)}
            />
          </span>
        )}
        <span className="text-[var(--text-muted)]">
          {wl.items.length} {t.watchlists.items}
        </span>
        <span className="text-[var(--text-muted)]">
          {t.watchlists.created}: <span dir="ltr">{wl.created_at.slice(0, 10)}</span>
        </span>
      </div>

      {confirmDelete && (
        <div
          className="mt-3 rounded border p-3 text-sm"
          style={{ borderColor: "var(--border-ring)", background: "var(--plane)" }}
        >
          <p className="font-medium">{t.watchlists.confirmDelete}</p>
          <p className="mt-1 text-xs text-[var(--text-secondary)]">
            {t.watchlists.confirmDeleteHint}
          </p>
          <div className="mt-2 flex flex-wrap gap-2">
            <button
              onClick={removeList}
              disabled={busy === "delete"}
              className="btn text-xs"
              style={{ background: "var(--status-critical)" }}
            >
              {busy === "delete" ? t.watchlists.deleting : t.watchlists.yesDelete}
            </button>
            <button onClick={() => setConfirmDelete(false)} className="btn-ghost text-xs">
              {t.watchlists.cancel}
            </button>
          </div>
        </div>
      )}

      {error && (
        <p className="mt-2 text-xs" style={{ color: "var(--status-critical)" }}>
          ■ {error}
        </p>
      )}

      <div className="mt-3">
        {wl.items.length === 0 ? (
          <p className="text-sm text-[var(--text-secondary)]">{t.watchlists.emptyItems}</p>
        ) : (
          <ul className="space-y-2">
            {wl.items.map((item) => (
              <WatchlistItemRow
                key={item.id}
                item={item}
                opp={item.opportunity_id ? oppById.get(item.opportunity_id) : undefined}
                confirming={confirmRemoveId === item.id}
                busy={busy === `remove:${item.id}`}
                onAskRemove={() => setConfirmRemoveId(item.id)}
                onCancelRemove={() => setConfirmRemoveId(null)}
                onRemove={() => removeItem(item.id)}
              />
            ))}
          </ul>
        )}
      </div>

      {adding && (
        <div className="mt-4 border-t pt-4" style={{ borderColor: "var(--gridline)" }}>
          <AddItemForm opps={opps} onSubmit={addItem} onCancel={() => setAdding(false)} />
        </div>
      )}
    </Card>
  );
}

function WatchlistItemRow({
  item,
  opp,
  confirming,
  busy,
  onAskRemove,
  onCancelRemove,
  onRemove,
}: {
  item: WatchlistItem;
  opp?: Opportunity;
  confirming: boolean;
  busy: boolean;
  onAskRemove: () => void;
  onCancelRemove: () => void;
  onRemove: () => void;
}) {
  const { t } = useApp();

  return (
    <li className="rounded border p-3" style={{ borderColor: "var(--border-ring)" }}>
      {item.item_type === "opportunity" && opp ? (
        <>
          <div className="flex flex-wrap items-start gap-2">
            <Link href={`/opportunities/${opp.id}`} className="link me-auto text-sm font-medium">
              {opp.title}
            </Link>
            <div className="flex flex-wrap items-center gap-1.5 text-[11px] text-[var(--text-muted)]">
              <ValidationBadge status={opp.validation_status} />
              <StatusBadge status={opp.state} label={vocab(t.opportunities.states, opp.state)} />
              {item.label && <span>· {item.label}</span>}
            </div>
          </div>

          {/* The two numbers are shown separately, labelled, never merged. */}
          <div className="mt-2 grid gap-2 sm:grid-cols-2">
            <div>
              <div className="text-[11px] text-[var(--text-secondary)]">
                {t.opportunities.score}
              </div>
              <ScoreBar value={opp.opportunity_score} />
            </div>
            <div>
              <div className="text-[11px] text-[var(--text-secondary)]">
                {t.opportunities.relevance}
              </div>
              {opp.user_relevance == null ? (
                <span className="text-sm text-[var(--text-muted)]">—</span>
              ) : (
                <ScoreBar value={opp.user_relevance} tone="var(--series-2)" />
              )}
            </div>
          </div>

          <div className="mt-2 flex flex-wrap items-center gap-x-4 gap-y-1 text-xs text-[var(--text-secondary)]">
            <span className="inline-flex items-center gap-1.5">
              {t.opportunities.risk}:{" "}
              <StatusBadge
                status={`risk_${opp.risk_level}`}
                label={vocab(t.opportunities.risks_levels, opp.risk_level)}
              />
            </span>
          </div>
        </>
      ) : (
        // No opportunity behind this item (or none in the hydrated feed): plain
        // text only. Absence of a score is shown as absence, never as a zero.
        <div className="flex flex-wrap items-center gap-2">
          <span className="text-xs text-[var(--text-muted)]">
            {vocab(t.watchlists.itemTypes, item.item_type)}
          </span>
          <span className="text-sm">{item.label ?? itemText(item)}</span>
        </div>
      )}

      <div className="mt-2 flex items-center gap-2">
        {confirming ? (
          <>
            <span className="me-auto text-xs text-[var(--text-secondary)]">
              {t.watchlists.confirmRemove}
              <span className="block text-[11px] text-[var(--text-muted)]">
                {t.watchlists.confirmRemoveHint}
              </span>
            </span>
            <button
              onClick={onRemove}
              disabled={busy}
              className="btn text-xs"
              style={{ background: "var(--status-critical)" }}
            >
              {busy ? t.watchlists.removing : t.watchlists.yesRemove}
            </button>
            <button onClick={onCancelRemove} className="btn-ghost text-xs">
              {t.watchlists.cancel}
            </button>
          </>
        ) : (
          <button onClick={onAskRemove} className="btn-ghost text-xs">
            {t.watchlists.remove}
          </button>
        )}
      </div>
    </li>
  );
}
