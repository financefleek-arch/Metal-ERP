import { useEffect, useRef, useState } from "react";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { api, ApiError } from "../lib/api";
import { useVocab } from "../lib/reference";
import type { GroupDetail, ItemCategoryRow, ItemType, RateMode } from "../lib/types";
import { HsnPicker } from "./HsnPicker";

/** Product-group editor + its size grid (drag to reorder). */
export function GroupForm({ groupId }: { groupId: string }) {
  const qc = useQueryClient();
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
      </div>

      <div className="grid grid-cols-3 gap-x-3 gap-y-3">
        <div className="col-span-3">
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
          <label className="label">Default rate mode</label>
          <select
            className="field"
            value={g.default_rate_mode}
            onChange={(e) => patch({ default_rate_mode: e.target.value as RateMode })}
          >
            <option value="piece">per piece</option>
            <option value="kg">per kg</option>
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
              <option key={u}>{u}</option>
            ))}
          </select>
        </div>
        <div className="col-span-2">
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
          <div className="grid grid-cols-[24px_1fr_90px_90px] gap-2 bg-[#efe9df] px-3 py-1.5 text-[9px] uppercase tracking-wide text-muted">
            <span>#</span>
            <span>Size</span>
            <span>Rate</span>
            <span>Mode</span>
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
              className="grid cursor-grab grid-cols-[24px_1fr_90px_90px] items-center gap-2 px-3 py-2 text-xs"
            >
              <span className="text-faint">☰</span>
              <button
                className="text-left hover:text-accent"
                onClick={() => window.location.assign(`/items/${l.id}`)}
              >
                {l.size_label ?? l.size_text ?? l.generated_name}
              </button>
              <span className="font-mono text-muted">
                {l.default_rate != null ? `₹${l.default_rate}` : "—"}
              </span>
              <span className="text-muted">{l.rate_mode}</span>
            </div>
          ))}
          {leaves.length === 0 && (
            <div className="px-3 py-4 text-center text-xs text-muted">
              No sizes yet. Add an item and set its group to this one.
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
