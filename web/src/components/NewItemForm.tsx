import { useState } from "react";
import { useMutation, useQueryClient } from "@tanstack/react-query";
import { api, ApiError } from "../lib/api";
import { useVocab } from "../lib/reference";
import type { Item, ItemType } from "../lib/types";
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
  const [metal, setMetal] = useState("");
  const [shape, setShape] = useState("");
  const [uom, setUom] = useState("");
  const [hsn, setHsn] = useState("");
  const [rate, setRate] = useState("");
  const [err, setErr] = useState<string | null>(null);

  const metals = useVocab("metals");
  const shapes = useVocab("shapes");
  const uoms = useVocab("uoms");

  const create = useMutation({
    mutationFn: () =>
      api<Item>("/items", {
        method: "POST",
        body: {
          name: name.trim(),
          item_type: itemType,
          metal: metal || null,
          shape: shape || null,
          uom: uom || null,
          hsn_code: hsn || null,
          default_rate: rate.trim() || null,
        },
      }),
    onSuccess: (it) => {
      qc.invalidateQueries({ queryKey: ["items"] });
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
      <div className="flex items-center justify-between">
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

      <div className="grid grid-cols-3 gap-x-3 gap-y-3">
        <div className="col-span-3">
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
