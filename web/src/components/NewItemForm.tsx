import { useState } from "react";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { api, ApiError } from "../lib/api";
import { useVocab } from "../lib/reference";
import type { GroupOut, Item, ItemCategoryRow, ItemType } from "../lib/types";
import { HsnPicker } from "./HsnPicker";

/**
 * Create-in-place form for a not-yet-saved item. Only the essentials —
 * the full attribute set is filled after creation in the detail form.
 */
export function NewItemForm({
  onCreated,
  onCancel,
}: {
  onCreated: (it: Item) => void;
  onCancel: () => void;
}) {
  const qc = useQueryClient();
  const [name, setName] = useState("");
  const [itemType, setItemType] = useState<ItemType>("bulk");
  const [categoryId, setCategoryId] = useState("");
  const [groupId, setGroupId] = useState("");
  const [metal, setMetal] = useState("");
  const [shape, setShape] = useState("");
  const [uom, setUom] = useState("");
  const [hsn, setHsn] = useState("");
  const [rate, setRate] = useState("");
  const [err, setErr] = useState<string | null>(null);

  const metals = useVocab("metals");
  const shapes = useVocab("shapes");
  const uoms = useVocab("uoms");
  const cats = useQuery({
    queryKey: ["item-categories"],
    queryFn: () => api<ItemCategoryRow[]>("/item-categories"),
  });
  const groups = useQuery({
    queryKey: ["item-groups", categoryId],
    queryFn: () =>
      api<GroupOut[]>(`/item-groups${categoryId ? `?category_id=${categoryId}` : ""}`),
  });

  const create = useMutation({
    mutationFn: () =>
      api<Item>("/items", {
        method: "POST",
        body: {
          name: name.trim(),
          item_type: itemType,
          category_id: categoryId || null,
          group_id: groupId || null,
          metal: metal || null,
          shape: shape || null,
          uom: uom || null,
          hsn_code: hsn || null,
          default_rate: rate.trim() || null,
        },
      }),
    onSuccess: (it) => {
      qc.invalidateQueries({ queryKey: ["items"] });
      qc.invalidateQueries({ queryKey: ["item-tree"] });
      onCreated(it);
    },
    onError: (e) => setErr(e instanceof ApiError ? e.message : "Create failed"),
  });

  const canCreate = !!name.trim() && !create.isPending;

  return (
    <form
      className="flex flex-col gap-4"
      onSubmit={(e) => {
        e.preventDefault();
        setErr(null);
        if (canCreate) create.mutate();
      }}
    >
      <div className="flex flex-wrap items-center justify-between gap-2">
        <h2 className="font-serif text-lg font-semibold text-accent">New item</h2>
        <div className="flex gap-2">
          <button type="button" className="btn-ghost" onClick={onCancel}>
            Cancel
          </button>
          <button type="submit" className="btn-primary" disabled={!canCreate}>
            {create.isPending ? "Creating…" : "Create"}
          </button>
        </div>
      </div>

      <div className="grid grid-cols-1 gap-x-3 gap-y-3 sm:grid-cols-2 lg:grid-cols-3">
        <div className="sm:col-span-2 lg:col-span-3">
          <label className="label">Name *</label>
          <input
            className="field"
            autoFocus
            placeholder="e.g. SS 304 Patta 4in 2mm"
            value={name}
            onChange={(e) => setName(e.target.value)}
          />
        </div>
        <div>
          <label className="label">Type</label>
          <select
            className="field"
            value={itemType}
            onChange={(e) => setItemType(e.target.value as ItemType)}
          >
            <option value="bulk">⚖ BULK</option>
            <option value="mrp">📦 MRP</option>
          </select>
        </div>
        <div>
          <label className="label">Category</label>
          <select
            className="field"
            value={categoryId}
            onChange={(e) => {
              setCategoryId(e.target.value);
              setGroupId("");
            }}
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
          <label className="label">Group</label>
          <select
            className="field"
            value={groupId}
            onChange={(e) => setGroupId(e.target.value)}
          >
            <option value="">— (loose)</option>
            {groups.data?.map((g) => (
              <option key={g.id} value={g.id}>
                {g.name}
              </option>
            ))}
          </select>
        </div>
        <div>
          <label className="label">Metal</label>
          <select className="field" value={metal} onChange={(e) => setMetal(e.target.value)}>
            <option value="">—</option>
            {(metals.data ?? []).map((m) => (
              <option key={m}>{m}</option>
            ))}
          </select>
        </div>
        <div>
          <label className="label">Shape</label>
          <select className="field" value={shape} onChange={(e) => setShape(e.target.value)}>
            <option value="">—</option>
            {(shapes.data ?? []).map((s) => (
              <option key={s}>{s}</option>
            ))}
          </select>
        </div>
        <div>
          <label className="label">UOM</label>
          <select className="field" value={uom} onChange={(e) => setUom(e.target.value)}>
            <option value="">—</option>
            {(uoms.data ?? []).map((u) => (
              <option key={u}>{u}</option>
            ))}
          </select>
        </div>
        <div>
          <label className="label">HSN</label>
          <HsnPicker value={hsn} onChange={(code) => setHsn(code)} />
        </div>
        <div>
          <label className="label">Default rate</label>
          <input
            className="field font-mono"
            inputMode="decimal"
            value={rate}
            onChange={(e) => setRate(e.target.value)}
          />
        </div>
      </div>

      {err && <p className="err">{err}</p>}
      <p className="text-[11px] text-muted">
        Created as <strong>unconfirmed</strong>. Fill the rest of the attributes after Create.
      </p>
    </form>
  );
}
