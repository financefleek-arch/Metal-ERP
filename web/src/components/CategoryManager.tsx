import { useState } from "react";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { api, ApiError } from "../lib/api";
import type { ItemCategoryRow } from "../lib/types";

/** Small editor for the per-tenant category list (drives the tree's top level). */
export function CategoryManager({ onClose }: { onClose: () => void }) {
  const qc = useQueryClient();
  const [newName, setNewName] = useState("");
  const [err, setErr] = useState<string | null>(null);
  const [editing, setEditing] = useState<string | null>(null);
  const [editName, setEditName] = useState("");
  const [taxOpen, setTaxOpen] = useState<string | null>(null);

  const cats = useQuery({
    queryKey: ["item-categories"],
    queryFn: () => api<ItemCategoryRow[]>("/item-categories"),
  });

  const invalidate = () => {
    qc.invalidateQueries({ queryKey: ["item-categories"] });
    qc.invalidateQueries({ queryKey: ["item-tree"] });
  };

  const create = useMutation({
    mutationFn: (name: string) =>
      api<ItemCategoryRow>("/item-categories", { method: "POST", body: { name } }),
    onSuccess: () => {
      setNewName("");
      setErr(null);
      invalidate();
    },
    onError: (e) => setErr(e instanceof ApiError ? e.message : "Failed"),
  });

  const rename = useMutation({
    mutationFn: (a: { id: string; name: string }) =>
      api<ItemCategoryRow>(`/item-categories/${a.id}`, { method: "PATCH", body: { name: a.name } }),
    onSuccess: () => {
      setEditing(null);
      invalidate();
    },
    onError: (e) => setErr(e instanceof ApiError ? e.message : "Failed"),
  });

  const [mergeFrom, setMergeFrom] = useState<string | null>(null);
  const [mergeInto, setMergeInto] = useState("");
  const merge = useMutation({
    mutationFn: (a: { id: string; into: string }) =>
      api<ItemCategoryRow>(`/item-categories/${a.id}/merge`, { method: "POST", body: { into: a.into } }),
    onSuccess: () => {
      setMergeFrom(null);
      setMergeInto("");
      setErr(null);
      invalidate();
      qc.invalidateQueries({ queryKey: ["item-tree-leaves"] });
      qc.invalidateQueries({ queryKey: ["items"] });
    },
    onError: (e) => setErr(e instanceof ApiError ? e.message : "Could not merge."),
  });

  const del = useMutation({
    mutationFn: (id: string) =>
      api<void>(`/item-categories/${id}`, { method: "DELETE", body: {} }),
    onSuccess: invalidate,
    onError: (e) => setErr(e instanceof ApiError ? e.message : "Failed"),
  });

  return (
    <div className="flex flex-col gap-3">
      <div className="flex items-center justify-between">
        <h2 className="font-serif text-lg font-semibold">Categories</h2>
        <button className="btn-ghost h-7 px-3 text-xs" onClick={onClose}>
          Done
        </button>
      </div>
      <p className="text-[11px] text-muted">
        The top bucket the tree groups by. A utensil shop turns these into brands
        (Hawkins, Mintage); a metal shop keeps materials (Steel, Aluminium).
      </p>

      <form
        className="flex gap-2"
        onSubmit={(e) => {
          e.preventDefault();
          if (newName.trim()) create.mutate(newName.trim());
        }}
      >
        <input
          className="field h-8 text-xs"
          placeholder="New category…"
          maxLength={60}
          value={newName}
          onChange={(e) => setNewName(e.target.value)}
        />
        <button className="btn-primary h-8 px-3 text-xs" disabled={!newName.trim()}>
          Add
        </button>
      </form>
      {err && <p className="err">{err}</p>}

      <div className="card divide-y divide-[#f3eee4] overflow-hidden">
        {cats.data?.map((c) => (
          <div key={c.id}>
          <div className="flex items-center gap-2 px-3 py-2.5 text-sm md:py-2">
            {editing === c.id ? (
              <>
                <input
                  className="field h-7 flex-1 text-xs"
                  value={editName}
                  maxLength={60}
                  autoFocus
                  onChange={(e) => setEditName(e.target.value)}
                />
                <button
                  className="text-xs text-accent hover:underline"
                  onClick={() => rename.mutate({ id: c.id, name: editName.trim() })}
                >
                  Save
                </button>
                <button
                  className="text-xs text-muted hover:underline"
                  onClick={() => setEditing(null)}
                >
                  Cancel
                </button>
              </>
            ) : (
              <>
                <span className="min-w-0 flex-1 truncate">{c.name}</span>
                <span className="hidden font-mono text-[10px] text-muted sm:inline">
                  {c.group_count} grp · {c.item_count} items
                </span>
                <button
                  className="shrink-0 px-1 text-xs text-muted hover:text-ink"
                  title="Default HSN and GST rate for items in this group"
                  onClick={() => setTaxOpen((t) => (t === c.id ? null : c.id))}
                >
                  {c.hsn_code ? `HSN ${c.hsn_code}` : "HSN / GST"}
                </button>
                <button
                  className="shrink-0 px-1 text-xs text-muted hover:text-ink"
                  onClick={() => {
                    setEditing(c.id);
                    setEditName(c.name);
                  }}
                >
                  Rename
                </button>
                <button
                  className="shrink-0 px-1 text-xs text-muted hover:text-ink"
                  title="Move everything in this category into another one, then remove this one"
                  onClick={() => {
                    setMergeFrom((m) => (m === c.id ? null : c.id));
                    setMergeInto("");
                  }}
                >
                  Merge
                </button>
                <button
                  className="shrink-0 px-1 text-xs text-danger hover:underline"
                  onClick={() => {
                    if (
                      confirm(
                        c.group_count || c.item_count
                          ? `"${c.name}" is used by ${c.group_count} groups / ${c.item_count} items. They'll be left uncategorised. Delete anyway?`
                          : `Delete "${c.name}"?`,
                      )
                    )
                      del.mutate(c.id);
                  }}
                >
                  Delete
                </button>
              </>
            )}
          </div>
          {mergeFrom === c.id && (
            <div className="flex flex-wrap items-center gap-2 bg-ground px-3 py-2.5 text-xs">
              <span className="basis-full text-muted">
                Move its {c.group_count} group{c.group_count === 1 ? "" : "s"} and {c.item_count} item
                {c.item_count === 1 ? "" : "s"} into another category, then remove “{c.name}”.
              </span>
              <select
                className="field h-8 min-w-[10rem] flex-1 text-xs"
                aria-label="Merge into"
                value={mergeInto}
                onChange={(e) => setMergeInto(e.target.value)}
              >
                <option value="">— category to keep —</option>
                {(cats.data ?? [])
                  .filter((x) => x.id !== c.id)
                  .map((x) => (
                    <option key={x.id} value={x.id}>
                      {x.name}
                    </option>
                  ))}
              </select>
              <button
                className="btn-primary h-8 px-3"
                disabled={!mergeInto || merge.isPending}
                onClick={() => merge.mutate({ id: c.id, into: mergeInto })}
              >
                {merge.isPending ? "Merging…" : "Merge"}
              </button>
              <button className="btn-ghost h-8 px-3" onClick={() => setMergeFrom(null)}>
                Cancel
              </button>
            </div>
          )}
          {taxOpen === c.id && <TaxPanel cat={c} onChanged={invalidate} />}
          </div>
        ))}
      </div>
    </div>
  );
}

/** The group's default HSN and GST rate: new items inherit them; "apply" fills existing items
 *  that have no HSN. */
function TaxPanel({ cat, onChanged }: { cat: ItemCategoryRow; onChanged: () => void }) {
  const [hsn, setHsn] = useState(cat.hsn_code ?? "");
  const [gst, setGst] = useState(cat.gst_rate ? String(Number(cat.gst_rate)) : "");
  const [msg, setMsg] = useState<string | null>(null);
  const [err, setErr] = useState<string | null>(null);
  const save = useMutation({
    mutationFn: () =>
      api<ItemCategoryRow>(`/item-categories/${cat.id}`, {
        method: "PATCH",
        body: { hsn_code: hsn.trim() || null, gst_rate: gst === "" ? null : Number(gst).toFixed(2) },
      }),
    onSuccess: () => {
      setErr(null);
      setMsg("Saved.");
      onChanged();
    },
    onError: (e) => setErr(e instanceof ApiError ? e.message : "Could not save. Use a 4 to 8 digit HSN."),
  });
  const apply = useMutation({
    mutationFn: () => api<{ updated: number }>(`/item-categories/${cat.id}/apply-hsn`, { method: "POST" }),
    onSuccess: (r) => {
      setErr(null);
      setMsg(`${r.updated} item${r.updated === 1 ? "" : "s"} updated.`);
      onChanged();
    },
    onError: (e) => setErr(e instanceof ApiError ? e.message : "Could not apply."),
  });
  return (
    <div className="flex flex-wrap items-end gap-3 bg-ground px-3 py-2.5 text-xs">
      <div>
        <label className="label" htmlFor={`hsn-${cat.id}`}>
          HSN
        </label>
        <input
          id={`hsn-${cat.id}`}
          className="field h-8 w-28"
          inputMode="numeric"
          maxLength={8}
          value={hsn}
          onChange={(e) => setHsn(e.target.value.replace(/\D/g, ""))}
        />
      </div>
      <div>
        <label className="label" htmlFor={`gst-${cat.id}`}>
          GST %
        </label>
        <select id={`gst-${cat.id}`} className="field h-8 w-24" value={gst} onChange={(e) => setGst(e.target.value)}>
          <option value="">from HSN</option>
          {[0, 5, 12, 18, 28].map((r) => (
            <option key={r} value={r}>
              {r}%
            </option>
          ))}
        </select>
      </div>
      <button className="btn-primary h-8 px-3" disabled={save.isPending} onClick={() => save.mutate()}>
        Save
      </button>
      {cat.hsn_code && cat.items_without_hsn > 0 && (
        <button className="btn-ghost h-8 px-3" disabled={apply.isPending} onClick={() => apply.mutate()}>
          Apply to {cat.items_without_hsn} items without HSN
        </button>
      )}
      {msg && <span className="text-ok">{msg}</span>}
      {err && <span className="text-danger">{err}</span>}
      <p className="basis-full text-[11px] text-muted">
        New items in this group get this HSN and GST rate. Items that already have an HSN are never changed.
      </p>
    </div>
  );
}
