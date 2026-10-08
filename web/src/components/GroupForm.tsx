import { useEffect, useRef, useState } from "react";
import { useNavigate } from "react-router-dom";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { api, ApiError } from "../lib/api";
import { useVocab } from "../lib/reference";
import { uomDisplay } from "../lib/uom";
import type { GroupDetail, GroupOut, ItemCategoryRow, ItemType } from "../lib/types";
import { HsnPicker } from "./HsnPicker";
import { NodeMenu, NodeMenuEntry } from "./NodeMenu";
import type { NodeAction } from "../lib/itemNodes";

/**
 * Product-group editor + its size grid (drag to reorder), and the things you do to the whole
 * group: add a size, run any bulk action on it, merge it into another group, ungroup or delete it.
 */
export function GroupForm({
  groupId,
  writable,
  catalogModule,
  onAction,
}: {
  groupId: string;
  writable: boolean;
  catalogModule: boolean;
  onAction: (g: { id: string; name: string; item_count: number }, a: NodeAction) => void;
}) {
  const qc = useQueryClient();
  const nav = useNavigate();
  const [mergeOpen, setMergeOpen] = useState(false);
  const [mergeInto, setMergeInto] = useState("");
  const [actErr, setActErr] = useState<string | null>(null);
  const [saveHint, setSaveHint] = useState<string>("");
  const timer = useRef<number | undefined>(undefined);
  const [order, setOrder] = useState<string[] | null>(null);
  const dragId = useRef<string | null>(null);

  const cats = useQuery({
    queryKey: ["item-categories"],
    queryFn: () => api<ItemCategoryRow[]>("/item-categories"),
  });
  const uoms = useVocab("uoms");

  const group = useQuery({
    queryKey: ["item-group", groupId],
    queryFn: () => api<GroupDetail>(`/item-groups/${groupId}`),
  });

  useEffect(() => setOrder(null), [groupId]);

  const invalidate = () => {
    qc.invalidateQueries({ queryKey: ["item-group", groupId] });
    qc.invalidateQueries({ queryKey: ["item-tree"] });
    qc.invalidateQueries({ queryKey: ["item-tree-leaves"] });
  };

  const save = useMutation({
    mutationFn: (body: Record<string, unknown>) =>
      api<GroupDetail>(`/item-groups/${groupId}`, { method: "PATCH", body }),
    onSuccess: () => {
      setSaveHint("Saved");
      invalidate();
    },
    onError: (e) => setSaveHint(e instanceof ApiError ? e.message : "Save failed"),
  });

  const reorder = useMutation({
    mutationFn: (leafIds: string[]) =>
      api<GroupDetail>(`/item-groups/${groupId}/size-order`, {
        method: "PATCH",
        body: { leaf_ids: leafIds },
      }),
    onSuccess: () => {
      setOrder(null);
      invalidate();
    },
  });

  const allGroups = useQuery({
    queryKey: ["item-groups", ""],
    queryFn: () => api<GroupOut[]>("/item-groups"),
    enabled: mergeOpen,
  });
  const refreshAll = () => {
    qc.invalidateQueries({ queryKey: ["item-tree"] });
    qc.invalidateQueries({ queryKey: ["item-tree-leaves"] });
    qc.invalidateQueries({ queryKey: ["items"] });
    qc.invalidateQueries({ queryKey: ["item-groups"] });
    qc.invalidateQueries({ queryKey: ["item-categories"] });
  };
  const ungroup = useMutation({
    mutationFn: () =>
      api(`/items/bulk`, {
        method: "PATCH",
        body: { filter: { group_id: groupId }, fields: { group_id: null }, fields_set: ["group_id"] },
      }),
    onSuccess: () => {
      setActErr(null);
      refreshAll();
      qc.invalidateQueries({ queryKey: ["item-group", groupId] });
    },
    onError: (e) => setActErr(e instanceof ApiError ? e.message : "Could not ungroup."),
  });
  const removeGroup = useMutation({
    mutationFn: () => api<void>(`/item-groups/${groupId}`, { method: "DELETE" }),
    onSuccess: () => {
      refreshAll();
      nav("/items");
    },
    onError: (e) => setActErr(e instanceof ApiError ? e.message : "Could not delete the group."),
  });
  const merge = useMutation({
    mutationFn: (into: string) =>
      api<GroupDetail>(`/item-groups/${groupId}/merge`, { method: "POST", body: { into } }),
    onSuccess: (d) => {
      refreshAll();
      nav(`/items/g/${d.id}`);
    },
    onError: (e) => setActErr(e instanceof ApiError ? e.message : "Could not merge."),
  });

  function patch(body: Record<string, unknown>) {
    setSaveHint("Editing…");
    window.clearTimeout(timer.current);
    timer.current = window.setTimeout(() => {
      setSaveHint("Saving…");
      save.mutate(body);
    }, 600);
  }

  useEffect(() => () => window.clearTimeout(timer.current), []);

  if (group.isLoading) return <div className="text-sm text-muted">Loading…</div>;
  const g = group.data;
  if (!g) return <div className="text-sm text-muted">Group not found.</div>;

  const leaves = order
    ? order.map((id) => g.leaves.find((l) => l.id === id)!).filter(Boolean)
    : g.leaves;

  return (
    <div className="flex flex-col gap-4">
      <div>
        <h2 className="font-serif text-lg font-semibold">
          {g.name}{" "}
          <span
            className={`align-middle rounded-sm px-1.5 py-0.5 text-[9px] font-bold uppercase ${
              g.item_type === "bulk" ? "bg-accent-soft text-accent" : "bg-[#f1e7d6] text-warn"
            }`}
          >
            group · {g.item_type}
          </span>
        </h2>
        <p className="mt-0.5 text-[11px] text-muted">
          {g.category_name ?? "uncategorised"} · {g.item_count} size
          {g.item_count === 1 ? "" : "s"}
        </p>
        {writable && (
          <div className="mt-2 flex flex-wrap items-center gap-2">
            <button
              className="btn-primary h-8 px-3 text-xs"
              onClick={() => nav(`/items/new?group=${g.id}`)}
            >
              + Add size
            </button>
            <button
              className="btn-ghost h-8 px-3 text-xs"
              disabled={g.item_count === 0}
              onClick={() => onAction(g, "availability")}
            >
              Set availability
            </button>
            {catalogModule && (
              <button
                className="btn-ghost h-8 px-3 text-xs"
                disabled={g.item_count === 0}
                onClick={() => onAction(g, "catalog")}
              >
                Make customer catalog
              </button>
            )}
            <NodeMenu
              title={g.name}
              count={g.item_count}
              catalogModule={catalogModule}
              onAction={(a) => onAction(g, a)}
            >
              <NodeMenuEntry label="Merge into another group…" onClick={() => setMergeOpen(true)} />
              <NodeMenuEntry
                label="Ungroup all items"
                onClick={() => {
                  if (
                    g.item_count > 0 &&
                    confirm(
                      `Take ${g.item_count} item${g.item_count === 1 ? "" : "s"} out of "${g.name}"? They stay in their category, ungrouped.`,
                    )
                  )
                    ungroup.mutate();
                }}
              />
              <NodeMenuEntry
                label="Delete group…"
                onClick={() => {
                  if (
                    confirm(
                      g.item_count > 0
                        ? `Delete the group "${g.name}"? Its ${g.item_count} item${g.item_count === 1 ? "" : "s"} are NOT deleted; they become ungrouped.`
                        : `Delete the empty group "${g.name}"?`,
                    )
                  )
                    removeGroup.mutate();
                }}
              />
            </NodeMenu>
          </div>
        )}
        {actErr && <p className="err mt-2">{actErr}</p>}
        {mergeOpen && (
          <div className="mt-3 flex flex-col gap-2 rounded-lg border border-line bg-ground p-3 text-xs">
            <p className="text-muted">
              Move all {g.item_count} item{g.item_count === 1 ? "" : "s"} of “{g.name}” into another
              group, then delete “{g.name}”. Item names, rates and HSN are not changed; the sizes go
              to the end of the other group, and the items take its category.
            </p>
            <div className="flex flex-wrap items-center gap-2">
              <select
                className="field h-8 min-w-[12rem] flex-1 text-xs"
                value={mergeInto}
                aria-label="Merge into"
                onChange={(e) => setMergeInto(e.target.value)}
              >
                <option value="">— pick the group to keep —</option>
                {(allGroups.data ?? [])
                  .filter((x) => x.id !== g.id)
                  .map((x) => (
                    <option key={x.id} value={x.id}>
                      {x.name}
                      {x.category_name ? ` (${x.category_name})` : ""}
                    </option>
                  ))}
              </select>
              <button
                className="btn-primary h-8 px-3 text-xs"
                disabled={!mergeInto || merge.isPending}
                onClick={() => merge.mutate(mergeInto)}
              >
                {merge.isPending ? "Merging…" : "Merge"}
              </button>
              <button className="btn-ghost h-8 px-3 text-xs" onClick={() => setMergeOpen(false)}>
                Cancel
              </button>
            </div>
          </div>
        )}
      </div>

      <div className="grid grid-cols-1 gap-x-3 gap-y-3 sm:grid-cols-2 lg:grid-cols-3">
        <div className="sm:col-span-2 lg:col-span-3">
          <label className="label">Group name</label>
          <input
            className="field"
            defaultValue={g.name}
            onChange={(e) => patch({ name: e.target.value })}
          />
        </div>
        <div>
          <label className="label">Category</label>
          <select
            className="field"
            value={g.category_id ?? ""}
            onChange={(e) => patch({ category_id: e.target.value || null })}
          >
            <option value="">—</option>
            {cats.data?.map((c) => (
              <option key={c.id} value={c.id}>
                {c.name}
              </option>
            ))}
          </select>
        </div>
        <div>
          <label className="label">Type</label>
          <select
            className="field"
            value={g.item_type}
            onChange={(e) => patch({ item_type: e.target.value as ItemType })}
          >
            <option value="bulk">⚖ BULK</option>
            <option value="mrp">📦 MRP</option>
          </select>
        </div>
        <div>
          <label className="label">UOM</label>
          <select
            className="field"
            value={g.uom ?? ""}
            onChange={(e) => patch({ uom: e.target.value || null })}
          >
            <option value="">—</option>
            {uoms.data?.map((u) => (
              <option key={u} value={u}>
                {uomDisplay(u)}
              </option>
            ))}
          </select>
        </div>
        <div className="sm:col-span-2 lg:col-span-2">
          <label className="label">HSN</label>
          <HsnPicker value={g.hsn_code ?? ""} onChange={(code) => patch({ hsn_code: code || null })} />
        </div>
      </div>

      <div className="border-t border-line pt-3">
        <p className="mb-2 text-[10px] uppercase tracking-[0.06em] text-muted">
          Sizes
          <span className="ml-2 normal-case tracking-normal text-faint">
            · drag to reorder
          </span>
        </p>
        <div className="card divide-y divide-[#f3eee4] overflow-hidden">
          <div className="grid grid-cols-[24px_1fr_90px] gap-2 bg-[#efe9df] px-3 py-1.5 text-[9px] uppercase tracking-wide text-muted">
            <span>#</span>
            <span>Size</span>
            <span>Rate</span>
          </div>
          {leaves.map((l, i) => (
            <div
              key={l.id}
              draggable
              onDragStart={() => {
                dragId.current = l.id;
                setOrder(leaves.map((x) => x.id));
              }}
              onDragOver={(e) => {
                e.preventDefault();
                if (!dragId.current || dragId.current === l.id || !order) return;
                const next = order.filter((x) => x !== dragId.current);
                next.splice(i, 0, dragId.current);
                setOrder(next);
              }}
              onDrop={() => {
                if (order) reorder.mutate(order);
                dragId.current = null;
              }}
              className="grid cursor-grab grid-cols-[24px_1fr_90px] items-center gap-2 px-3 py-2 text-xs"
            >
              <span className="text-faint">☰</span>
              <button
                className="text-left hover:text-accent"
                onClick={() => nav(`/items/${l.id}`)}
              >
                {l.size_label ?? l.size_text ?? l.generated_name}
              </button>
              <span className="font-mono text-muted">
                {l.default_rate != null ? `₹${l.default_rate}` : "—"}
              </span>
            </div>
          ))}
          {leaves.length === 0 && (
            <div className="px-3 py-4 text-center text-xs text-muted">
              No sizes yet. Use “+ Add size” to create the first one.
            </div>
          )}
        </div>
      </div>

      <div className="flex items-center gap-2 text-[11px] text-muted">
        <span className="inline-block h-1.5 w-1.5 rounded-full bg-line" />
        {saveHint || "No unsaved changes"}
      </div>
    </div>
  );
}
