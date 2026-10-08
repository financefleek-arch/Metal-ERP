import { useState } from "react";
import { useNavigate } from "react-router-dom";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { api, ApiError } from "../lib/api";
import { useDebounced } from "../lib/useDebounced";
import type { Item, ItemListItem } from "../lib/types";

function Frame({
  title,
  onClose,
  children,
}: {
  title: string;
  onClose: () => void;
  children: React.ReactNode;
}) {
  return (
    <div
      className="fixed inset-0 z-50 grid place-items-center bg-ink/40 p-4"
      role="dialog"
      aria-modal="true"
      aria-label={title}
      onMouseDown={(e) => e.target === e.currentTarget && onClose()}
    >
      <div className="card flex max-h-[90dvh] w-full max-w-md flex-col gap-3 overflow-y-auto p-4">
        <div className="flex items-start justify-between gap-3">
          <h2 className="font-serif text-base font-semibold">{title}</h2>
          <button className="btn-ghost h-7 px-2.5 text-xs" onClick={onClose}>
            Cancel
          </button>
        </div>
        {children}
      </div>
    </div>
  );
}

function refresh(qc: ReturnType<typeof useQueryClient>) {
  qc.invalidateQueries({ queryKey: ["items"] });
  qc.invalidateQueries({ queryKey: ["item"] });
  qc.invalidateQueries({ queryKey: ["item-tree"] });
  qc.invalidateQueries({ queryKey: ["item-tree-leaves"] });
}

/** "This is a duplicate of…": fold this item into another one you keep. */
export function MergeItemDialog({ item, onClose }: { item: Item; onClose: () => void }) {
  const qc = useQueryClient();
  const nav = useNavigate();
  const [q, setQ] = useState("");
  const dq = useDebounced(q.trim(), 250);
  const [target, setTarget] = useState<ItemListItem | null>(null);
  const [err, setErr] = useState<string | null>(null);

  const found = useQuery({
    queryKey: ["items", "merge-pick", dq],
    enabled: dq.length >= 2,
    queryFn: () => api<ItemListItem[]>(`/items?q=${encodeURIComponent(dq)}`),
  });
  const merge = useMutation({
    mutationFn: (targetId: string) =>
      api<Item>(`/items/${item.id}/merge`, { method: "POST", body: { target_id: targetId } }),
    onSuccess: (winner) => {
      refresh(qc);
      onClose();
      nav(`/items/${winner.id}`);
    },
    onError: (e) => setErr(e instanceof ApiError ? e.message : "Could not merge."),
  });

  const choices = (found.data ?? []).filter((x) => x.id !== item.id && x.status !== "archived");
  return (
    <Frame title="Merge into another item" onClose={onClose}>
      <p className="text-xs text-muted">
        Use this when “{item.name}” is the same product as another item. You keep the other item;
        “{item.name}” disappears from the list, its name is remembered so future bills still match, its
        invoice lines point to the item you keep, and its photo and billing count move over.
      </p>
      {!target ? (
        <>
          <input
            className="field h-9 text-sm"
            autoFocus
            placeholder="search the item to keep…"
            value={q}
            onChange={(e) => setQ(e.target.value)}
          />
          <div className="flex max-h-60 flex-col overflow-y-auto rounded-lg border border-line">
            {dq.length < 2 && <p className="px-3 py-3 text-xs text-muted">Type at least two letters.</p>}
            {dq.length >= 2 && found.isLoading && <p className="px-3 py-3 text-xs text-muted">Searching…</p>}
            {dq.length >= 2 && !found.isLoading && choices.length === 0 && (
              <p className="px-3 py-3 text-xs text-muted">No other item matches.</p>
            )}
            {choices.map((c) => (
              <button
                key={c.id}
                className="flex items-center gap-2 border-b border-[#f3eee4] px-3 py-2.5 text-left text-xs last:border-0 hover:bg-accent-soft"
                onClick={() => setTarget(c)}
              >
                <span className="truncate font-medium">{c.name}</span>
                <span className="ml-auto shrink-0 font-mono text-[10px] text-muted">
                  billed {c.times_billed}×
                </span>
              </button>
            ))}
          </div>
        </>
      ) : (
        <div className="flex flex-col gap-3 text-sm">
          <div className="rounded-lg border border-line bg-ground p-3 text-xs">
            <div className="text-muted">Keep</div>
            <div className="font-semibold">{target.name}</div>
            <div className="mt-2 text-muted">Merge away</div>
            <div className="font-semibold">{item.name}</div>
          </div>
          {err && <p className="err">{err}</p>}
          <div className="flex justify-end gap-2">
            <button className="btn-ghost h-9 px-3 text-xs" onClick={() => setTarget(null)}>
              Back
            </button>
            <button
              className="btn-primary h-9 px-3 text-xs"
              disabled={merge.isPending}
              onClick={() => merge.mutate(target.id)}
            >
              {merge.isPending ? "Merging…" : "Merge"}
            </button>
          </div>
        </div>
      )}
    </Frame>
  );
}

const COPY_FIELDS = [
  "item_type",
  "category_id",
  "group_id",
  "uom",
  "hsn_code",
  "metal",
  "shape",
  "grade",
  "size_text",
  "thickness_mm",
  "width_mm",
  "length_mm",
  "finish",
  "secondary_uom",
  "conversion_factor",
  "weight_per_uom",
  "purchase_uom",
  "default_rate",
  "mrp",
  "default_discount_pct",
  "price_min",
  "price_max",
  "availability",
  "pack_qty",
  "carton_qty",
  "notes",
] as const;

/** A new item that starts as a copy of this one (same group, unit, HSN, rates), under a new name. */
export function DuplicateItemDialog({ item, onClose }: { item: Item; onClose: () => void }) {
  const qc = useQueryClient();
  const nav = useNavigate();
  const [name, setName] = useState(`${item.name} copy`);
  const [err, setErr] = useState<string | null>(null);

  const create = useMutation({
    mutationFn: () => {
      const src = item as unknown as Record<string, unknown>;
      const body: Record<string, unknown> = { name: name.trim() };
      for (const k of COPY_FIELDS) if (src[k] != null && src[k] !== "") body[k] = src[k];
      return api<Item>("/items", { method: "POST", body });
    },
    onSuccess: (it) => {
      refresh(qc);
      onClose();
      nav(`/items/${it.id}`);
    },
    onError: (e) => setErr(e instanceof ApiError ? e.message : "Could not copy."),
  });

  return (
    <Frame title="Copy this item" onClose={onClose}>
      <p className="text-xs text-muted">
        Makes a new item with the same group, unit, HSN and rates. It does not copy the photo, the code or
        the billing history. Give it its own name.
      </p>
      <form
        className="flex flex-col gap-3"
        onSubmit={(e) => {
          e.preventDefault();
          setErr(null);
          if (name.trim() && !create.isPending) create.mutate();
        }}
      >
        <input
          className="field h-9 text-sm"
          autoFocus
          maxLength={200}
          value={name}
          onChange={(e) => setName(e.target.value)}
          onFocus={(e) => e.currentTarget.select()}
        />
        {err && <p className="err">{err}</p>}
        <div className="flex justify-end gap-2">
          <button
            className="btn-primary h-9 px-3 text-xs"
            disabled={!name.trim() || name.trim() === item.name || create.isPending}
          >
            {create.isPending ? "Copying…" : "Create copy"}
          </button>
        </div>
      </form>
    </Frame>
  );
}
