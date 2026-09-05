"use client";

import Link from "next/link";
import { useCallback, useEffect, useMemo, useState } from "react";
import { Guard } from "@/components/Guard";
import { Card, ScoreBar, StatTile, StatusBadge, ValidationBadge, vocab } from "@/components/ui";
import { api } from "@/lib/api";
import { useApp } from "@/lib/providers";
import {
  RISK_LEVELS,
  WATCH_ITEM_TYPES,
  type Opportunity,
  type Page,
  type RiskLevel,
  type Watchlist,
  type WatchlistIn,
  type WatchlistItem,
  type WatchlistItemIn,
  type WatchItemType,
} from "@/lib/types";

export default function WatchlistsPage() {
  return (
    <Guard>
      <Body />
    </Guard>
  );
}

/**
 * The one field that makes each item_type coherent.
 *
 * The backend does no cross-field validation: it will happily store an
 * "opportunity" row with a null opportunity_id, which then joins to nothing
 * for ever. This table is the only thing standing between the user and that
 * silently useless row, so the form sends the matching field and omits the
 * rest rather than posting a spray of nulls.
 */
type ItemField =
  | "opportunity_id"
  | "trend_id"
  | "entity_id"
  | "country_code"
  | "industry"
  | "keyword";

const REQUIRED_FIELD: Record<WatchItemType, ItemField> = {
  opportunity: "opportunity_id",
  trend: "trend_id",
  company: "entity_id",
  technology: "entity_id",
  product: "entity_id",
  country: "country_code",
  industry: "industry",
  keyword: "keyword",
};

const UUID_RE = /^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$/i;

/** Backend column limits, mirrored so the user is stopped before a 422 is. */
const MAX_LEN: Record<ItemField | "name" | "description" | "label", number> = {
  name: 200,
  description: 2000,
  opportunity_id: 36,
  trend_id: 36,
  entity_id: 36,
  country_code: 2,
  industry: 80,
  keyword: 200,
  label: 240,
};

function errorText(err: unknown): string {
  return err instanceof Error ? err.message : String(err);
}

/**
 * The score, if and only if a real one exists for this row.
 *
 * A watchlist stores no scores. The only honest way to show one is to find the
 * row's opportunity in the feed we already fetched. Anything else — a
 * non-opportunity type, a null id, an opportunity that has dropped out of the
 * feed — has no score, and the caller must render nothing score-shaped for it.
 */
function hydrate(item: WatchlistItem, feed: Map<string, Opportunity> | null): Opportunity | null {
  if (item.item_type !== "opportunity") return null;
  if (!item.opportunity_id) return null;
  return feed?.get(item.opportunity_id) ?? null;
}

function Body() {
  const { t, token } = useApp();
  const [lists, setLists] = useState<Watchlist[] | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [actionError, setActionError] = useState<string | null>(null);
  const [feed, setFeed] = useState<Map<string, Opportunity> | null>(null);
  const [feedFailed, setFeedFailed] = useState(false);
  const [creating, setCreating] = useState(false);

  const load = useCallback(async () => {
    if (!token) return;
    try {
      const rows = await api<Watchlist[]>("/me/watchlists", { token });
      setLists(rows);
      setError(null);
    } catch (err) {
      setError(errorText(err));
    }
  }, [token]);

  useEffect(() => {
    void load();
  }, [load]);

  // Hydration, fetched ONCE for the whole page and joined client-side on
  // opportunity_id. Per-item lookups would be a request per row for numbers the
  // feed already carries. A failure here costs the scores, never the lists.
  useEffect(() => {
    if (!token) return;
    let cancelled = false;
    api<Page<Opportunity>>("/for-you?limit=200", { token })
      .then((page) => {
        if (cancelled) return;
        setFeed(new Map(page.items.map((o) => [o.id, o])));
        setFeedFailed(false);
      })
      .catch(() => {
        if (cancelled) return;
        setFeed(new Map());
        setFeedFailed(true);
      });
    return () => {
      cancelled = true;
    };
  }, [token]);

  const totals = useMemo(() => {
    const all = lists ?? [];
    const items = all.flatMap((w) => w.items);
    return {
      lists: all.length,
      items: items.length,
      scored: items.filter((i) => hydrate(i, feed) !== null).length,
    };
  }, [lists, feed]);

  if (error) {
    return (
      <p className="text-sm" style={{ color: "var(--status-critical)" }}>
        ■ {error}
      </p>
    );
  }
  if (lists === null) return <p className="text-sm text-[var(--text-muted)]">{t.loading}</p>;

  return (
    <>
      <div className="flex flex-wrap items-start gap-3">
        <div className="me-auto">
          <h1 className="text-xl font-semibold">{t.watchlists.title}</h1>
          <p className="max-w-3xl text-sm text-[var(--text-secondary)]">{t.watchlists.subtitle}</p>
        </div>
        <button onClick={() => setCreating((v) => !v)} className="btn text-sm">
          {t.watchlists.newList}
        </button>
      </div>

      {/* Why most rows are blank. Said once, at the top, rather than repeated as
          a placeholder on every row that has nothing to place. */}
      <Card title={t.watchlists.noScoreTitle}>
        <ul className="space-y-2 text-sm text-[var(--text-secondary)]">
          {t.watchlists.noScoreHow.map((line, i) => (
            <li key={i} className="flex items-start gap-2">
              <span
                aria-hidden
                className="mt-1"
                style={{ color: i === 1 ? "var(--series-2)" : "var(--text-muted)" }}
              >
                ●
              </span>
              <span>{line}</span>
            </li>
          ))}
        </ul>
        <p className="mt-3 text-xs text-[var(--text-muted)]">
          {feedFailed ? (
            <span style={{ color: "var(--status-serious)" }}>
              ▲ {t.watchlists.scoresUnavailable}
            </span>
          ) : (
            <>
              <Link href="/for-you" className="link">
                {t.watchlists.scoresFrom}
              </Link>
              {" — "}
              {t.watchlists.scoresFromNote}
            </>
          )}
        </p>
      </Card>

      <div className="grid grid-cols-2 gap-4 sm:grid-cols-3">
        <StatTile label={t.watchlists.statLists} value={totals.lists} />
        <StatTile label={t.watchlists.statItems} value={totals.items} />
        <StatTile label={t.watchlists.statScored} value={totals.scored} />
      </div>

      {actionError && (
        <p className="text-sm" style={{ color: "var(--status-critical)" }}>
          ■ {actionError}
        </p>
      )}

      {creating && (
        <Card title={t.watchlists.createTitle}>
          <WatchlistForm
            submitLabel={t.watchlists.create}
            busyLabel={t.watchlists.creating}
            onCancel={() => setCreating(false)}
            onSubmit={async (body) => {
              await api<Watchlist>("/me/watchlists", {
                method: "POST",
                token,
                body: JSON.stringify(body),
              });
              setCreating(false);
              await load();
            }}
          />
        </Card>
      )}

      {lists.length === 0 ? (
        <Card>
          <p className="py-4 text-sm text-[var(--text-secondary)]">{t.watchlists.empty}</p>
        </Card>
      ) : (
        <div className="space-y-4">
          {lists.map((list) => (
            <WatchlistCard
              key={list.id}
              list={list}
              feed={feed}
              reload={load}
              onError={setActionError}
            />
          ))}
        </div>
      )}
    </>
  );
}

// ---------------------------------------------------------------- one watchlist
function WatchlistCard({
  list,
  feed,
  reload,
  onError,
}: {
  list: Watchlist;
  feed: Map<string, Opportunity> | null;
  reload: () => Promise<void>;
  onError: (message: string | null) => void;
}) {
  const { t, token } = useApp();
  const [editing, setEditing] = useState(false);
  const [adding, setAdding] = useState(false);
  const [confirmingDelete, setConfirmingDelete] = useState(false);
  const [deleting, setDeleting] = useState(false);

  async function remove() {
    setDeleting(true);
    onError(null);
    try {
      await api<void>(`/me/watchlists/${list.id}`, { method: "DELETE", token });
      setConfirmingDelete(false);
      await reload();
    } catch (err) {
      onError(errorText(err));
      setDeleting(false);
    }
  }

  const count =
    list.items.length === 1
      ? t.watchlists.itemCountOne
      : `${list.items.length} ${t.watchlists.itemCountMany}`;

  return (
    <Card>
      <div className="flex flex-wrap items-start gap-3">
        <div className="me-auto min-w-0">
          <h2 className="text-sm font-semibold">{list.name}</h2>
          {list.description && (
            <p className="mt-1 max-w-3xl text-sm text-[var(--text-secondary)]">
              {list.description}
            </p>
          )}
          <div className="mt-1 text-[11px] text-[var(--text-muted)]">
            {count}
            {" · "}
            {t.watchlists.created}{" "}
            <span dir="ltr">{list.created_at.slice(0, 10)}</span>
          </div>
        </div>
        <div className="flex flex-wrap gap-2">
          <button onClick={() => setAdding((v) => !v)} className="btn-ghost text-xs">
            {t.watchlists.addItem}
          </button>
          <button onClick={() => setEditing((v) => !v)} className="btn-ghost text-xs">
            {t.watchlists.edit}
          </button>
          <button
            onClick={() => setConfirmingDelete(true)}
            className="btn-ghost text-xs"
            style={{ color: "var(--status-critical)" }}
          >
            {t.watchlists.deleteList}
          </button>
        </div>
      </div>

      {/* The thresholds. These are the user's own filter settings, so they are
          written as labelled text — never through ScoreBar, which would make a
          preference look like a measurement. */}
      <div className="mt-3 flex flex-wrap items-center gap-x-5 gap-y-1.5 text-xs text-[var(--text-secondary)]">
        <span>
          {t.watchlists.minScore}:{" "}
          {list.min_score == null ? (
            <span className="text-[var(--text-muted)]">{t.watchlists.noThreshold}</span>
          ) : (
            <b className="tabular" dir="ltr">
              {list.min_score.toFixed(0)}
            </b>
          )}
        </span>
        <span>
          {t.watchlists.maxRisk}:{" "}
          {list.max_risk_level == null ? (
            <span className="text-[var(--text-muted)]">{t.watchlists.noRiskCap}</span>
          ) : (
            <b>{vocab(t.opportunities.risks_levels, list.max_risk_level)}</b>
          )}
        </span>
      </div>
      {list.min_score != null && (
        <p className="mt-1 text-[11px] text-[var(--text-muted)]">{t.watchlists.minScoreHint}</p>
      )}

      {confirmingDelete && (
        <div
          className="mt-3 rounded p-3 text-sm"
          style={{
            // The shorthand has to come first: a later `border` would reset the
            // inline-start edge back to the hairline and lose the red.
            border: "1px solid var(--border-ring)",
            borderInlineStartWidth: "4px",
            borderInlineStartColor: "var(--status-critical)",
          }}
        >
          <p className="text-[var(--text-secondary)]">{t.watchlists.confirmDeleteList}</p>
          <div className="mt-2 flex flex-wrap gap-2">
            <button
              onClick={remove}
              disabled={deleting}
              className="btn text-xs"
              style={{ background: "var(--status-critical)" }}
            >
              {t.watchlists.confirmYes}
            </button>
            <button
              onClick={() => setConfirmingDelete(false)}
              disabled={deleting}
              className="btn-ghost text-xs"
            >
              {t.watchlists.keep}
            </button>
          </div>
        </div>
      )}

      {editing && (
        <div className="mt-4 border-t pt-4" style={{ borderColor: "var(--border-ring)" }}>
          <h3 className="mb-2 text-xs font-semibold">{t.watchlists.editTitle}</h3>
          <WatchlistForm
            initial={list}
            submitLabel={t.watchlists.save}
            busyLabel={t.watchlists.saving}
            onCancel={() => setEditing(false)}
            onSubmit={async (body) => {
              await api<Watchlist>(`/me/watchlists/${list.id}`, {
                method: "PUT",
                token,
                body: JSON.stringify(body),
              });
              setEditing(false);
              await reload();
            }}
          />
        </div>
      )}

      {adding && (
        <div className="mt-4 border-t pt-4" style={{ borderColor: "var(--border-ring)" }}>
          <h3 className="mb-2 text-xs font-semibold">{t.watchlists.addItemTitle}</h3>
          <ItemForm
            onCancel={() => setAdding(false)}
            onSubmit={async (body) => {
              await api<WatchlistItem>(`/me/watchlists/${list.id}/items`, {
                method: "POST",
                token,
                body: JSON.stringify(body),
              });
              setAdding(false);
              await reload();
            }}
          />
        </div>
      )}

      <div className="mt-4 border-t pt-3" style={{ borderColor: "var(--border-ring)" }}>
        {list.items.length === 0 ? (
          <p className="py-2 text-sm text-[var(--text-secondary)]">{t.watchlists.emptyItems}</p>
        ) : (
          <ul>
            {list.items.map((item) => (
              <ItemRow
                key={item.id}
                listId={list.id}
                item={item}
                opportunity={hydrate(item, feed)}
                reload={reload}
                onError={onError}
              />
            ))}
          </ul>
        )}
      </div>
    </Card>
  );
}

// --------------------------------------------------------------------- one item
function ItemRow({
  listId,
  item,
  opportunity,
  reload,
  onError,
}: {
  listId: string;
  item: WatchlistItem;
  opportunity: Opportunity | null;
  reload: () => Promise<void>;
  onError: (message: string | null) => void;
}) {
  const { t, token } = useApp();
  const [confirming, setConfirming] = useState(false);
  const [busy, setBusy] = useState(false);

  async function remove() {
    setBusy(true);
    onError(null);
    try {
      await api<void>(`/me/watchlists/${listId}/items/${item.id}`, { method: "DELETE", token });
      setConfirming(false);
      await reload();
    } catch (err) {
      onError(errorText(err));
      setBusy(false);
    }
  }

  // What this row is, in the plainest terms available: the user's own label
  // first, then the hydrated title, then the raw value they entered.
  const raw =
    item.label ??
    opportunity?.title ??
    item.keyword ??
    item.industry ??
    item.country_code ??
    item.opportunity_id ??
    item.trend_id ??
    item.entity_id ??
    "—";
  const isId = raw === (item.opportunity_id ?? item.trend_id ?? item.entity_id);

  return (
    // Separated with an explicit palette colour rather than `divide-y`, whose
    // default grey is not one of the theme's variables and does not follow dark.
    <li
      className="py-3 [&:not(:first-child)]:border-t"
      style={{ borderColor: "var(--gridline)" }}
    >
      <div className="flex flex-wrap items-start gap-3">
        <div className="me-auto min-w-0">
          <div className="flex flex-wrap items-center gap-2">
            {opportunity ? (
              <Link href={`/opportunities/${opportunity.id}`} className="link text-sm font-medium">
                {raw}
              </Link>
            ) : (
              <span
                className={`text-sm font-medium ${isId ? "tabular break-all text-xs" : ""}`}
                dir={isId ? "ltr" : undefined}
              >
                {raw}
              </span>
            )}
            <span
              className="rounded border px-1.5 py-0.5 text-[10px] text-[var(--text-secondary)]"
              style={{ borderColor: "var(--border-ring)" }}
            >
              {vocab(t.watchlists.types, item.item_type)}
            </span>
            {/* Only ever from a real Opportunity. The badge says where evidence
                came from, so a row with no evidence attached shows none. */}
            {opportunity && <ValidationBadge status={opportunity.validation_status} />}
            {opportunity && (
              <StatusBadge
                status={opportunity.state}
                label={vocab(t.opportunities.states, opportunity.state)}
              />
            )}
          </div>
          <div className="mt-1 text-[11px] text-[var(--text-muted)]">
            {t.watchlists.addedOn} <span dir="ltr">{item.created_at.slice(0, 10)}</span>
          </div>
        </div>
        <button
          onClick={() => setConfirming(true)}
          className="btn-ghost text-xs"
          style={{ color: "var(--status-critical)" }}
        >
          {t.watchlists.remove}
        </button>
      </div>

      {opportunity ? (
        <>
          {/* Two scores, two labels, two colours, two bars. They measure
              different things and are never allowed to read as one number. */}
          <div className="mt-3 grid gap-3 sm:grid-cols-2">
            <div>
              <div className="text-xs text-[var(--text-secondary)]">{t.opportunities.score}</div>
              <ScoreBar value={opportunity.opportunity_score} />
              <div className="mt-1 text-[11px] text-[var(--text-muted)]">
                {t.opportunities.scoreHint}
              </div>
            </div>
            <div>
              <div className="text-xs text-[var(--text-secondary)]">
                {t.opportunities.relevance}
              </div>
              {opportunity.user_relevance == null ? (
                <span className="text-sm text-[var(--text-muted)]">—</span>
              ) : (
                <ScoreBar value={opportunity.user_relevance} tone="var(--series-2)" />
              )}
              <div className="mt-1 text-[11px] text-[var(--text-muted)]">
                {t.opportunities.relevanceHint}
              </div>
            </div>
          </div>
          <div className="mt-2 flex flex-wrap items-center gap-x-4 gap-y-1.5 text-xs text-[var(--text-secondary)]">
            <span className="inline-flex items-center gap-1.5">
              {t.opportunities.risk}:{" "}
              <StatusBadge
                status={`risk_${opportunity.risk_level}`}
                label={vocab(t.opportunities.risks_levels, opportunity.risk_level)}
              />
            </span>
          </div>
        </>
      ) : (
        // No score exists for this row, so nothing score-shaped is drawn: no
        // zero, no empty bar, no grey badge. Just a sentence saying why.
        <p className="mt-1 text-[11px] text-[var(--text-muted)]">
          {item.item_type === "opportunity"
            ? t.watchlists.notInFeed
            : t.watchlists.noScoreForType}
        </p>
      )}

      {confirming && (
        <div className="mt-3 flex flex-wrap items-center gap-2 text-sm">
          <span className="text-[var(--text-secondary)]">{t.watchlists.confirmDeleteItem}</span>
          <button
            onClick={remove}
            disabled={busy}
            className="btn text-xs"
            style={{ background: "var(--status-critical)" }}
          >
            {busy ? t.watchlists.removing : t.watchlists.confirmYesRemove}
          </button>
          <button
            onClick={() => setConfirming(false)}
            disabled={busy}
            className="btn-ghost text-xs"
          >
            {t.watchlists.keep}
          </button>
        </div>
      )}
    </li>
  );
}

// ------------------------------------------------------------ create / edit form
function WatchlistForm({
  initial,
  submitLabel,
  busyLabel,
  onSubmit,
  onCancel,
}: {
  initial?: Watchlist;
  submitLabel: string;
  busyLabel: string;
  onSubmit: (body: WatchlistIn) => Promise<void>;
  onCancel: () => void;
}) {
  const { t } = useApp();
  const [name, setName] = useState(initial?.name ?? "");
  const [description, setDescription] = useState(initial?.description ?? "");
  const [minScore, setMinScore] = useState(
    initial?.min_score == null ? "" : String(initial.min_score),
  );
  const [maxRisk, setMaxRisk] = useState<string>(initial?.max_risk_level ?? "");
  const [invalid, setInvalid] = useState<string | null>(null);
  const [failed, setFailed] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);

  async function submit(event: React.FormEvent) {
    event.preventDefault();
    setInvalid(null);
    setFailed(null);

    if (!name.trim()) return setInvalid(t.watchlists.errName);
    const score = minScore.trim() === "" ? null : Number(minScore);
    if (score !== null && (!Number.isFinite(score) || score < 0 || score > 100)) {
      return setInvalid(t.watchlists.errMinScore);
    }

    // Every field is sent on every request, including the nulls. PUT applies
    // exclude_unset server-side, so an omitted key means "leave it alone" — the
    // only way to clear a threshold is to say null out loud. `name` rides along
    // unchanged for the same reason: the backend requires it on every PUT.
    const body: WatchlistIn = {
      name: name.trim(),
      description: description.trim() === "" ? null : description.trim(),
      min_score: score,
      max_risk_level: maxRisk === "" ? null : (maxRisk as RiskLevel),
    };

    setBusy(true);
    try {
      await onSubmit(body);
    } catch (err) {
      setFailed(errorText(err));
      setBusy(false);
    }
  }

  return (
    <form onSubmit={submit} className="max-w-lg space-y-3 text-sm">
      <label className="block">
        {t.watchlists.name}
        <input
          value={name}
          onChange={(e) => setName(e.target.value)}
          maxLength={MAX_LEN.name}
          required
          className="field mt-1 w-full"
        />
      </label>
      <label className="block">
        {t.watchlists.description}
        <textarea
          value={description}
          onChange={(e) => setDescription(e.target.value)}
          maxLength={MAX_LEN.description}
          rows={2}
          className="field mt-1 w-full"
        />
        <span className="text-[11px] text-[var(--text-muted)]">
          {t.watchlists.descriptionHint}
        </span>
      </label>
      <div className="grid gap-3 sm:grid-cols-2">
        <label className="block">
          {t.watchlists.minScore}
          <input
            type="number"
            min={0}
            max={100}
            value={minScore}
            onChange={(e) => setMinScore(e.target.value)}
            placeholder="0–100"
            className="field mt-1 w-full tabular"
            dir="ltr"
          />
          <span className="text-[11px] text-[var(--text-muted)]">{t.watchlists.minScoreHint}</span>
        </label>
        <label className="block">
          {t.watchlists.maxRisk}
          <select
            value={maxRisk}
            onChange={(e) => setMaxRisk(e.target.value)}
            className="field mt-1 w-full"
          >
            <option value="">{t.watchlists.noRiskCap}</option>
            {RISK_LEVELS.map((level) => (
              <option key={level} value={level}>
                {vocab(t.opportunities.risks_levels, level)}
              </option>
            ))}
          </select>
        </label>
      </div>

      {initial && <p className="text-[11px] text-[var(--text-muted)]">{t.watchlists.editNote}</p>}
      {invalid && (
        <p className="text-xs" style={{ color: "var(--status-critical)" }}>
          ■ {invalid}
        </p>
      )}
      {failed && (
        <p className="text-xs" style={{ color: "var(--status-critical)" }}>
          ■ {failed}
        </p>
      )}

      <div className="flex flex-wrap gap-2">
        <button type="submit" disabled={busy} className="btn text-sm">
          {busy ? busyLabel : submitLabel}
        </button>
        <button type="button" onClick={onCancel} disabled={busy} className="btn-ghost text-sm">
          {t.watchlists.cancel}
        </button>
      </div>
    </form>
  );
}

// ----------------------------------------------------------------- add-item form
function ItemForm({
  onSubmit,
  onCancel,
}: {
  onSubmit: (body: WatchlistItemIn) => Promise<void>;
  onCancel: () => void;
}) {
  const { t } = useApp();
  const [itemType, setItemType] = useState<WatchItemType>("opportunity");
  const [value, setValue] = useState("");
  const [label, setLabel] = useState("");
  const [invalid, setInvalid] = useState<string | null>(null);
  const [failed, setFailed] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);

  const field = REQUIRED_FIELD[itemType];
  const isUuid = field.endsWith("_id");
  const fieldLabel = {
    opportunity_id: t.watchlists.fieldOpportunityId,
    trend_id: t.watchlists.fieldTrendId,
    entity_id: t.watchlists.fieldEntityId,
    country_code: t.watchlists.fieldCountry,
    industry: t.watchlists.fieldIndustry,
    keyword: t.watchlists.fieldKeyword,
  }[field];

  async function submit(event: React.FormEvent) {
    event.preventDefault();
    setInvalid(null);
    setFailed(null);

    const entered = value.trim();
    if (!entered) return setInvalid(t.watchlists.errRequiredField);
    if (isUuid && !UUID_RE.test(entered)) return setInvalid(t.watchlists.errId);
    if (field === "country_code" && !/^[A-Za-z]{2}$/.test(entered)) {
      return setInvalid(t.watchlists.errCountry);
    }

    // Exactly one identifying field, chosen by item_type, plus an optional
    // label. The irrelevant fields are omitted rather than nulled, so the row
    // can never claim to be two things at once.
    const body: WatchlistItemIn = { item_type: itemType, [field]: entered };
    if (label.trim()) body.label = label.trim();

    setBusy(true);
    try {
      await onSubmit(body);
    } catch (err) {
      setFailed(errorText(err));
      setBusy(false);
    }
  }

  return (
    <form onSubmit={submit} className="max-w-lg space-y-3 text-sm">
      <div className="grid gap-3 sm:grid-cols-2">
        <label className="block">
          {t.watchlists.itemType}
          <select
            value={itemType}
            onChange={(e) => {
              setItemType(e.target.value as WatchItemType);
              setValue("");
              setInvalid(null);
            }}
            className="field mt-1 w-full"
          >
            {WATCH_ITEM_TYPES.map((kind) => (
              <option key={kind} value={kind}>
                {vocab(t.watchlists.types, kind)}
              </option>
            ))}
          </select>
        </label>
        <label className="block">
          {fieldLabel}
          <input
            value={value}
            onChange={(e) => setValue(e.target.value)}
            maxLength={MAX_LEN[field]}
            required
            className={`field mt-1 w-full ${isUuid ? "tabular text-xs" : ""}`}
            dir={isUuid || field === "country_code" ? "ltr" : undefined}
          />
          {field === "country_code" && (
            <span className="text-[11px] text-[var(--text-muted)]">
              {t.watchlists.countryHint}
            </span>
          )}
        </label>
      </div>
      <label className="block">
        {t.watchlists.fieldLabel}
        <input
          value={label}
          onChange={(e) => setLabel(e.target.value)}
          maxLength={MAX_LEN.label}
          className="field mt-1 w-full"
        />
        <span className="text-[11px] text-[var(--text-muted)]">{t.watchlists.labelHint}</span>
      </label>

      <p className="text-[11px] text-[var(--text-muted)]">{t.watchlists.noItemEdit}</p>
      {invalid && (
        <p className="text-xs" style={{ color: "var(--status-critical)" }}>
          ■ {invalid}
        </p>
      )}
      {failed && (
        <p className="text-xs" style={{ color: "var(--status-critical)" }}>
          ■ {failed}
        </p>
      )}

      <div className="flex flex-wrap gap-2">
        <button type="submit" disabled={busy} className="btn text-sm">
          {busy ? t.watchlists.adding : t.watchlists.add}
        </button>
        <button type="button" onClick={onCancel} disabled={busy} className="btn-ghost text-sm">
          {t.watchlists.cancel}
        </button>
      </div>
    </form>
  );
}
