import { useState } from "react";
import { useMutation, useQueryClient } from "@tanstack/react-query";
import { api, ApiError } from "../../lib/api";
import type { GroupSummary } from "../../lib/catalog";
import { EditableText } from "./Editable";

/** Rename, merge and delete this catalog's groups. A group is an item category, so
 *  these act on the shop's categories (everything using the group follows). */
export function GroupsPanel({ catalogId, groups }: { catalogId: string; groups: GroupSummary[] }) {
  const qc = useQueryClient();
  const [err, setErr] = useState<string | null>(null);
  const [confirmDelete, setConfirmDelete] = useState<string | null>(null);
  const [mergeFrom, setMergeFrom] = useState<string | null>(null);
  const [mergeInto, setMergeInto] = useState("");

  const named = groups.filter((g) => g.category_id !== null);

  function refresh() {
    qc.invalidateQueries({ queryKey: ["catalog-groups", catalogId] });
    qc.invalidateQueries({ queryKey: ["catalog-items", catalogId] });
    qc.invalidateQueries({ queryKey: ["item-categories"] });
  }
  const onError = (e: unknown) =>
    setErr(e instanceof ApiError ? e.message : "That did not work. Try again.");

  const rename = useMutation({
    mutationFn: (v: { id: string; name: string }) =>
      api(`/item-categories/${v.id}`, { method: "PATCH", body: { name: v.name } }),
    onSuccess: () => {
      setErr(null);
      refresh();
    },
    onError,
  });
  const merge = useMutation({
    mutationFn: (v: { id: string; into: string }) =>
      api(`/item-categories/${v.id}/merge`, { method: "POST", body: { into: v.into } }),
    onSuccess: () => {
      setErr(null);
      setMergeFrom(null);
      setMergeInto("");
      refresh();
    },
    onError,
  });
  const remove = useMutation({
    mutationFn: (id: string) => api(`/item-categories/${id}`, { method: "DELETE", body: {} }),
    onSuccess: () => {
      setErr(null);
      setConfirmDelete(null);
      refresh();
    },
    onError,
  });

  return (
    <section className="card mb-4 p-4" aria-label="Manage groups">
      <h2 className="font-serif text-lg font-semibold">Groups</h2>
      <p className="mt-1 text-sm text-muted">
        A group is a product type, like Beer Mugs. Renaming or merging also updates your item
        list. Deleting a group leaves its items without a group.
      </p>
      {err && (
        <p className="err" role="alert">
          {err}
        </p>
      )}
      <ul className="mt-3 divide-y divide-line">
        {named.map((g) => (
          <li key={g.category_id} className="flex flex-wrap items-center gap-x-3 gap-y-2 py-2">
            <div className="min-w-[10rem] flex-1">
              <EditableText
                label={`Rename ${g.name}`}
                value={g.name}
                onSave={(name) => name && rename.mutate({ id: g.category_id!, name })}
                className="font-medium"
              />
            </div>
            <span className="w-24 text-right text-xs tabular-nums text-muted">
              {g.item_count} items
            </span>

            {mergeFrom === g.category_id ? (
              <span className="flex items-center gap-2">
                <label className="sr-only" htmlFor={`merge-${g.category_id}`}>
                  Merge {g.name} into
                </label>
                <select
                  id={`merge-${g.category_id}`}
                  className="field h-8 w-44"
                  value={mergeInto}
                  onChange={(e) => setMergeInto(e.target.value)}
                >
                  <option value="">Merge into…</option>
                  {named
                    .filter((o) => o.category_id !== g.category_id)
                    .map((o) => (
                      <option key={o.category_id} value={o.category_id!}>
                        {o.name}
                      </option>
                    ))}
                </select>
                <button
                  type="button"
                  className="btn-primary h-8 px-3"
                  disabled={!mergeInto || merge.isPending}
                  onClick={() => merge.mutate({ id: g.category_id!, into: mergeInto })}
                >
                  Merge
                </button>
                <button type="button" className="btn-ghost h-8 px-3" onClick={() => setMergeFrom(null)}>
                  Cancel
                </button>
              </span>
            ) : confirmDelete === g.category_id ? (
              <span className="flex items-center gap-2 text-sm">
                <span>Delete “{g.name}”?</span>
                <button
                  type="button"
                  className="btn-primary h-8 bg-danger px-3 hover:bg-danger"
                  disabled={remove.isPending}
                  onClick={() => remove.mutate(g.category_id!)}
                >
                  Delete
                </button>
                <button type="button" className="btn-ghost h-8 px-3" onClick={() => setConfirmDelete(null)}>
                  Keep
                </button>
              </span>
            ) : (
              <span className="flex items-center gap-2">
                <button
                  type="button"
                  className="btn-ghost h-8 px-3"
                  onClick={() => {
                    setMergeFrom(g.category_id);
                    setConfirmDelete(null);
                  }}
                >
                  Merge
                </button>
                <button
                  type="button"
                  className="btn-ghost h-8 px-3"
                  onClick={() => {
                    setConfirmDelete(g.category_id);
                    setMergeFrom(null);
                  }}
                >
                  Delete
                </button>
              </span>
            )}
          </li>
        ))}
        {named.length === 0 && <li className="py-2 text-sm text-muted">No groups yet.</li>}
      </ul>
    </section>
  );
}
