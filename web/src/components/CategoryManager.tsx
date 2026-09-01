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
          <div key={c.id} className="flex items-center gap-2 px-3 py-2 text-sm">
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
                <span className="flex-1">{c.name}</span>
                <span className="font-mono text-[10px] text-muted">
                  {c.group_count} grp · {c.item_count} items
                </span>
                <button
                  className="text-xs text-muted hover:text-ink"
                  onClick={() => {
                    setEditing(c.id);
                    setEditName(c.name);
                  }}
                >
                  Rename
                </button>
                <button
                  className="text-xs text-danger hover:underline"
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
        ))}
      </div>
    </div>
  );
}
