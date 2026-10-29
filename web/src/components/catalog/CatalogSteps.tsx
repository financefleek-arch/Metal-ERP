import { useEffect, useRef, useState } from "react";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { useNavigate } from "react-router-dom";
import { api, ApiError } from "../../lib/api";
import { trimMargin, type BulkTarget, type CatalogDetail, type GroupSummary, type ItemFilter, type PromoteOut } from "../../lib/catalog";
import { useDialogKeys } from "../../lib/keyboard";
import { PricingPanel } from "./PricingPanel";
import { GroupsReviewPanel } from "./GroupsReviewPanel";

const errMsg = (e: unknown, fallback: string) => (e instanceof ApiError ? e.message : fallback);

interface PricesPreview {
  total: number;
  changing: number;
  examples: string[];
}

type Panel = "prices" | "groups" | null;

/**
 * The review as three steps that stay in view: Prices, Groups, Your items. Each shows where it
 * stands and opens its own panel. The third cell holds the one button that adds products to your
 * items (the selection if there is one, otherwise everything included), and once they are in,
 * the next things to do: update prices if the margin moved, open them in Items.
 */
export function CatalogSteps({
  catalog,
  groups,
  noGroup,
  notAdded,
  inItems,
  selection,
  resetting,
  onResetItemMargins,
  onLook,
}: {
  catalog: CatalogDetail;
  groups: GroupSummary[];
  noGroup: number;
  /** Included rows not in your items yet (undefined while loading). */
  notAdded: number | undefined;
  /** Rows of this list that are in your items. */
  inItems: number;
  selection: { count: number; target: BulkTarget } | null;
  resetting: boolean;
  onResetItemMargins: () => void;
  onLook: (f: Pick<ItemFilter, "suggested_group" | "no_suggestion">) => void;
}) {
  const qc = useQueryClient();
  const nav = useNavigate();
  const [panel, setPanel] = useState<Panel>(null);
  const [confirm, setConfirm] = useState(false);
  const [added, setAdded] = useState<{ ids: string[]; text: string } | null>(null);
  const [msg, setMsg] = useState<string | null>(null);
  const [err, setErr] = useState<string | null>(null);
  const [pricesOpen, setPricesOpen] = useState(false);

  // a list with no margin yet opens on the prices panel, once
  const opened = useRef(false);
  useEffect(() => {
    if (opened.current) return;
    opened.current = true;
    if (Number(catalog.bulk_margin_pct) === 0 && inItems === 0) setPanel("prices");
  }, [catalog.bulk_margin_pct, inItems]);

  const preview = useQuery({
    queryKey: ["prices-preview", catalog.id],
    enabled: inItems > 0,
    queryFn: () =>
      api<PricesPreview>(`/supplier-catalogs/${catalog.id}/prices/preview`, {
        method: "POST",
        body: { all_included: true },
      }),
  });
  const stale = preview.data?.changing ?? 0;

  const refresh = () => {
    qc.invalidateQueries({ queryKey: ["catalog-items", catalog.id] });
    qc.invalidateQueries({ queryKey: ["catalog-queues", catalog.id] });
    qc.invalidateQueries({ queryKey: ["catalog-groups", catalog.id] });
    qc.invalidateQueries({ queryKey: ["catalog-in-items", catalog.id] });
    qc.invalidateQueries({ queryKey: ["prices-preview", catalog.id] });
    qc.invalidateQueries({ queryKey: ["supplier-catalog", catalog.id] });
    qc.invalidateQueries({ queryKey: ["items"] });
  };

  const addReq = selection
    ? "ids" in selection.target
      ? { ids: selection.target.ids }
      : { filter: selection.target.filter }
    : { filter: { included: true, not_added: true } };

  const add = useMutation({
    mutationFn: () =>
      api<PromoteOut>(`/supplier-catalogs/${catalog.id}/promote`, { method: "POST", body: addReq }),
    onSuccess: (r) => {
      setErr(null);
      setConfirm(false);
      const bits = [`${r.create} new item${r.create === 1 ? "" : "s"}`];
      if (r.link_existing) bits.push(`${r.link_existing} matched items you already had`);
      if (r.reuse) bits.push(`${r.reuse} refreshed`);
      setAdded({ ids: r.created_item_ids, text: `Added to your items: ${bits.join(", ")}.` });
      refresh();
    },
    onError: (e) => setErr(errMsg(e, "Could not add the items. Try again.")),
  });
  const undo = useMutation({
    mutationFn: (ids: string[]) =>
      api<{ removed: number; kept: number }>(`/supplier-catalogs/${catalog.id}/promote/undo`, {
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

  function onAdd() {
    setMsg(null);
    if (!selection && noGroup > 0) setConfirm(true);
    else add.mutate();
  }

  const margin = Number(catalog.bulk_margin_pct);
  const groupMargins = groups.filter((g) => g.category_id !== null && g.margin_pct !== null).length;
  const pricesSet = margin !== 0 || groupMargins > 0;
  const groupsDone = noGroup === 0;
  const allIn = notAdded === 0;
  const addCount = selection ? selection.count : (notAdded ?? 0);

  const dot = (done: boolean, n: string) => (
    <span
      aria-hidden
      className={`grid h-[18px] w-[18px] shrink-0 place-items-center rounded-full border-[1.5px] text-[11px] ${
        done ? "border-ok bg-ok text-white" : "border-warn text-warn"
      }`}
    >
      {done ? "✓" : n}
    </span>
  );
  const head = "flex items-center gap-2 text-[11px] font-semibold uppercase tracking-[0.06em] text-muted";

  return (
    <section
      className="card sticky top-0 z-20 mb-4 shadow-sm"
      aria-label="Review steps"
    >
      <div className="grid md:grid-cols-3">
        <div className="flex flex-col gap-1.5 border-b border-line p-3 md:border-b-0 md:border-r">
          <div className={head}>
            {dot(pricesSet, "1")}Prices
          </div>
          <p className="text-[15px] font-medium tabular-nums">
            {pricesSet ? `${trimMargin(catalog.bulk_margin_pct)}% margin` : "No margin yet"}
            <span className="text-sm font-normal text-muted">
              {" "}
              · round to ₹{catalog.rounding_step}
              {groupMargins > 0 ? ` · ${groupMargins} group${groupMargins === 1 ? "" : "s"} differ` : ""}
            </span>
          </p>
          <div>
            <button
              type="button"
              className="text-sm text-accent hover:underline"
              aria-expanded={panel === "prices"}
              onClick={() => setPanel((p) => (p === "prices" ? null : "prices"))}
            >
              {panel === "prices" ? "Close" : "Edit"}
            </button>
          </div>
        </div>

        <div className="flex flex-col gap-1.5 border-b border-line p-3 md:border-b-0 md:border-r">
          <div className={head}>
            {dot(groupsDone, "2")}Groups
          </div>
          <p className="text-[15px] font-medium tabular-nums">
            {groupsDone ? (
              <>
                {catalog.item_count} <span className="text-sm font-normal text-muted">have a group</span>
              </>
            ) : (
              <>
                {noGroup}{" "}
                <span className="text-sm font-normal text-muted">of {catalog.item_count} need a group</span>
              </>
            )}
          </p>
          <div>
            {!groupsDone ? (
              <button
                type="button"
                className="btn-ghost h-8 px-3 text-sm"
                aria-expanded={panel === "groups"}
                onClick={() => setPanel((p) => (p === "groups" ? null : "groups"))}
              >
                {panel === "groups" ? "Close" : "Review groups"}
              </button>
            ) : (
              <span className="text-sm text-muted">Nothing to do</span>
            )}
          </div>
        </div>

        <div className="flex flex-col gap-1.5 p-3">
          <div className={head}>
            {dot(allIn, "3")}Your items
          </div>
          <p className="text-[15px] font-medium tabular-nums">
            {allIn ? (
              <>
                {inItems} <span className="text-sm font-normal text-muted">in your items</span>
              </>
            ) : (
              <>
                {notAdded ?? "…"} <span className="text-sm font-normal text-muted">ready to add</span>
              </>
            )}
          </p>
          <div>
            {(!allIn || selection) && (
              <button
                type="button"
                className="btn-primary h-9"
                disabled={add.isPending || (!selection && !notAdded)}
                title="Adds the selected rows, or every included row that is not in your items yet"
                onClick={onAdd}
              >
                {add.isPending
                  ? "Adding…"
                  : selection
                    ? `Add ${addCount} selected to items`
                    : `Add ${addCount} to items`}
              </button>
            )}
          </div>
        </div>
      </div>

      {panel === "prices" && (
        <PricingPanel
          catalog={catalog}
          groups={groups}
          resetting={resetting}
          onResetItemMargins={onResetItemMargins}
          onClose={() => setPanel(null)}
        />
      )}
      {panel === "groups" && (
        <GroupsReviewPanel
          catalogId={catalog.id}
          onLook={(f) => {
            onLook(f);
            setPanel(null);
          }}
          onClose={() => setPanel(null)}
        />
      )}

      {confirm && (
        <div
          className="flex flex-wrap items-center gap-x-3 gap-y-2 border-t border-line bg-[#f6edd6] px-4 py-2.5 text-sm"
          role="alertdialog"
          aria-label="Some products have no group"
        >
          <span>
            <b>{noGroup} products have no group.</b> They will be added to your items without one.
          </span>
          <button
            type="button"
            className="btn-primary h-8 px-3"
            disabled={add.isPending}
            onClick={() => add.mutate()}
          >
            {add.isPending ? "Adding…" : `Add ${addCount} anyway`}
          </button>
          <button
            type="button"
            className="btn-ghost h-8 px-3"
            onClick={() => {
              setConfirm(false);
              setPanel("groups");
            }}
          >
            Fix groups first
          </button>
          <span className="text-xs text-muted">Adding also fixes their item codes.</span>
        </div>
      )}

      {(inItems > 0 || added) && !confirm && (
        <div
          className="flex flex-wrap items-center gap-x-4 gap-y-2 rounded-b-[10px] border-t border-line bg-[#e6efe8] px-4 py-2.5 text-sm"
          role="status"
        >
          <span>
            <b>{added ? added.text : `${inItems} of these are in your items.`}</b>
          </span>
          {added && added.ids.length > 0 && (
            <button
              type="button"
              className="text-accent underline"
              disabled={undo.isPending}
              onClick={() => undo.mutate(added.ids)}
            >
              {undo.isPending ? "Undoing…" : "Undo"}
            </button>
          )}
          <span className="flex-1" />
          {stale > 0 && (
            <>
              <span className="font-medium text-warn">
                Margin changed: {stale} item price{stale === 1 ? " is" : "s are"} older.
              </span>
              <button type="button" className="btn-ghost h-8 px-3" onClick={() => setPricesOpen(true)}>
                Update {stale} prices
              </button>
            </>
          )}
          <button
            type="button"
            className="btn-ghost h-8 px-3"
            onClick={() => nav(`/items?catalog=${catalog.id}`)}
          >
            Open in Items
          </button>
        </div>
      )}
      {(msg || err) && (
        <p className={`px-4 py-2 text-sm ${err ? "err" : "text-ok"}`} role={err ? "alert" : "status"}>
          {err ?? msg}
        </p>
      )}
      {pricesOpen && (
        <UpdatePricesDialog
          catalogId={catalog.id}
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
  useDialogKeys(onClose);
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
