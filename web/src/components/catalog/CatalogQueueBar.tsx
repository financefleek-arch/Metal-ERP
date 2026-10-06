import { useState } from "react";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { api, apiPage, ApiError } from "../../lib/api";
import { itemsQuery, type ItemFilter, type PromoteOut } from "../../lib/catalog";

const errMsg = (e: unknown, fallback: string) => (e instanceof ApiError ? e.message : fallback);

interface PricesPreview {
  total: number;
  changing: number;
  examples: string[];
}

/**
 * Review by exception: what still needs a decision, one click each, and the two catalog-wide
 * actions (add everything included to your items, with an Undo; update your item prices).
 */
export function CatalogQueueBar({
  catalogId,
  groupSel,
  matchPossible,
  notAdded,
  priceMoved,
  priceMovedCount,
  onPriceMoved,
  photoCheck,
  photoCheckCount,
  onPhotoCheck,
  onNeedGroup,
  onPossible,
  onNotAdded,
}: {
  catalogId: string;
  groupSel: string;
  matchPossible: boolean;
  notAdded: boolean;
  priceMoved: boolean;
  priceMovedCount: number;
  onPriceMoved: () => void;
  photoCheck: boolean;
  photoCheckCount: number;
  onPhotoCheck: () => void;
  onNeedGroup: () => void;
  onPossible: () => void;
  onNotAdded: () => void;
}) {
  const qc = useQueryClient();
  const [added, setAdded] = useState<{ ids: string[]; text: string } | null>(null);
  const [msg, setMsg] = useState<string | null>(null);
  const [err, setErr] = useState<string | null>(null);
  const [pricesOpen, setPricesOpen] = useState(false);

  const count = (f: ItemFilter) =>
    apiPage<unknown[]>(`/supplier-catalogs/${catalogId}/items?${itemsQuery({ ...f, included: true }, null).replace(/limit=\d+/, "limit=1")}`).then(
      (p) => p.total ?? 0,
    );
  const counts = useQuery({
    queryKey: ["catalog-queues", catalogId],
    queryFn: async () => {
      const [g, p, n] = await Promise.all([
        count({ no_group: true }),
        count({ has_suggestion: true }),
        count({ not_added: true }),
      ]);
      return { group: g, possible: p, notAdded: n };
    },
  });
  const c = counts.data;

  const refresh = () => {
    qc.invalidateQueries({ queryKey: ["catalog-items", catalogId] });
    qc.invalidateQueries({ queryKey: ["catalog-queues", catalogId] });
    qc.invalidateQueries({ queryKey: ["catalog-groups", catalogId] });
    qc.invalidateQueries({ queryKey: ["supplier-catalog", catalogId] });
    qc.invalidateQueries({ queryKey: ["items"] });
  };

  const addAll = useMutation({
    mutationFn: () =>
      api<PromoteOut>(`/supplier-catalogs/${catalogId}/promote`, {
        method: "POST",
        body: { filter: { included: true, not_added: true } },
      }),
    onSuccess: (r) => {
      setErr(null);
      const bits = [`${r.create} new item${r.create === 1 ? "" : "s"}`];
      if (r.link_existing) bits.push(`${r.link_existing} matched items you already had`);
      setAdded({ ids: r.created_item_ids, text: `Added to your items: ${bits.join(", ")}.` });
      refresh();
    },
    onError: (e) => setErr(errMsg(e, "Could not add the items. Try again.")),
  });
  const undo = useMutation({
    mutationFn: (ids: string[]) =>
      api<{ removed: number; kept: number }>(`/supplier-catalogs/${catalogId}/promote/undo`, {
        method: "POST",
        body: { item_ids: ids },
      }),
    onSuccess: (r) => {
      setAdded(null);
      setMsg(
        `Undone: ${r.removed} item${r.removed === 1 ? "" : "s"} removed${
          r.kept ? `, ${r.kept} kept (already used or sent to Tally)` : ""
        }.`,
      );
      refresh();
    },
    onError: (e) => setErr(errMsg(e, "Could not undo. Try again.")),
  });

  const chip = (on: boolean) =>
    `rounded-full border px-3 py-1 text-xs ${
      on ? "border-accent bg-accent text-ground" : "border-line bg-card text-muted hover:bg-ground"
    }`;

  return (
    <section className="mb-3" aria-label="What needs attention">
      <div className="flex flex-wrap items-center gap-2">
        <span className="text-xs font-semibold uppercase tracking-wide text-muted">To review</span>
        <button type="button" className={chip(groupSel === "none")} onClick={onNeedGroup}>
          Need a group{c ? <span className="ml-1 tabular-nums opacity-70">{c.group}</span> : null}
        </button>
        <button type="button" className={chip(matchPossible)} onClick={onPossible}>
          Possible matches{c ? <span className="ml-1 tabular-nums opacity-70">{c.possible}</span> : null}
        </button>
        {photoCheckCount > 0 && (
          <button
            type="button"
            className={chip(photoCheck)}
            title="Pictures that look too small, odd-shaped or blank"
            onClick={onPhotoCheck}
          >
            Photo to check<span className="ml-1 tabular-nums opacity-70">{photoCheckCount}</span>
          </button>
        )}
        {priceMovedCount > 0 && (
          <button type="button" className={chip(priceMoved)} onClick={onPriceMoved}>
            Price changed<span className="ml-1 tabular-nums opacity-70">{priceMovedCount}</span>
          </button>
        )}
        <button type="button" className={chip(notAdded)} onClick={onNotAdded}>
          Not in your items{c ? <span className="ml-1 tabular-nums opacity-70">{c.notAdded}</span> : null}
        </button>
        <span className="ml-auto flex flex-wrap gap-2">
          <button
            type="button"
            className="btn-primary h-9"
            disabled={addAll.isPending || (c !== undefined && c.notAdded === 0)}
            title="Add every included row that is not in your items yet"
            onClick={() => addAll.mutate()}
          >
            {addAll.isPending ? "Adding…" : "Add all included to items"}
          </button>
          <button type="button" className="btn-ghost h-9" onClick={() => setPricesOpen(true)}>
            Update item prices…
          </button>
        </span>
      </div>

      {added && (
        <p className="mt-2 flex flex-wrap items-center gap-3 text-sm text-ok" role="status">
          {added.text}
          {added.ids.length > 0 && (
            <button
              type="button"
              className="text-accent underline"
              disabled={undo.isPending}
              onClick={() => undo.mutate(added.ids)}
            >
              {undo.isPending ? "Undoing…" : "Undo"}
            </button>
          )}
        </p>
      )}
      {msg && (
        <p className="mt-2 text-sm text-ok" role="status">
          {msg}
        </p>
      )}
      {err && (
        <p className="err" role="alert">
          {err}
        </p>
      )}
      {pricesOpen && (
        <UpdatePricesDialog
          catalogId={catalogId}
          onClose={() => setPricesOpen(false)}
          onDone={(n) => {
            setMsg(`Updated the selling price of ${n} item${n === 1 ? "" : "s"}.`);
            refresh();
          }}
        />
      )}
    </section>
  );
}

function UpdatePricesDialog({
  catalogId,
  onClose,
  onDone,
}: {
  catalogId: string;
  onClose: () => void;
  onDone: (n: number) => void;
}) {
  const [err, setErr] = useState<string | null>(null);
  const body = { all_included: true };
  const pre = useQuery({
    queryKey: ["prices-preview", catalogId],
    queryFn: () =>
      api<PricesPreview>(`/supplier-catalogs/${catalogId}/prices/preview`, { method: "POST", body }),
  });
  const apply = useMutation({
    mutationFn: () =>
      api<{ updated: number }>(`/supplier-catalogs/${catalogId}/prices/apply`, { method: "POST", body }),
    onSuccess: (r) => {
      onDone(r.updated);
      onClose();
    },
    onError: (e) => setErr(errMsg(e, "Could not update. Try again.")),
  });
  const p = pre.data;
  return (
    <div
      className="fixed inset-0 z-50 grid place-items-center overflow-y-auto bg-black/40 p-4"
      onMouseDown={(e) => {
        if (e.target === e.currentTarget) onClose();
      }}
    >
      <div role="dialog" aria-modal="true" aria-labelledby="up-title" className="card w-full max-w-lg bg-card p-5 shadow-xl">
        <h2 id="up-title" className="font-serif text-xl font-semibold">
          Update item prices
        </h2>
        <p className="mt-1 text-sm text-muted">
          Set the selling price of your items to this price list&apos;s current selling price (cost
          plus margin, rounded). Your items are never changed by a margin change on their own.
        </p>
        {pre.isLoading && <p className="mt-3 text-sm text-muted">Checking…</p>}
        {pre.isError && <p className="err">{errMsg(pre.error, "Could not check.")}</p>}
        {p && (
          <div className="mt-3 text-sm">
            <p>
              <span className="font-medium tabular-nums">{p.changing}</span> of{" "}
              <span className="tabular-nums">{p.total}</span> items in your item list would change.
            </p>
            {p.examples.length > 0 && (
              <ul className="mt-2 list-disc pl-5 text-xs text-muted">
                {p.examples.map((x) => (
                  <li key={x}>{x}</li>
                ))}
              </ul>
            )}
            {p.changing > 0 && (
              <p className="mt-2 text-xs text-muted">
                Items already in Tally will show &quot;Tally price old&quot; until you send them again.
              </p>
            )}
          </div>
        )}
        {err && (
          <p className="err" role="alert">
            {err}
          </p>
        )}
        <div className="mt-4 flex justify-end gap-2">
          <button type="button" className="btn-ghost" onClick={onClose}>
            Cancel
          </button>
          <button
            type="button"
            className="btn-primary"
            disabled={!p || p.changing === 0 || apply.isPending}
            onClick={() => apply.mutate()}
          >
            {apply.isPending ? "Updating…" : `Update ${p?.changing ?? ""} prices`}
          </button>
        </div>
      </div>
    </div>
  );
}
