import { useEffect, useMemo, useState } from "react";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { api, ApiError } from "../../lib/api";
import type { BulkTarget, ItemFilter, PromoteOut } from "../../lib/catalog";

type Scope = "selected" | "filtered" | "all";

/** Put catalog products in your item list. Independent of Tally. */
export function PromoteDialog({
  catalogId,
  selection,
  filtered,
  includedTotal,
  onClose,
}: {
  catalogId: string;
  selection: { count: number; target: BulkTarget } | null;
  filtered: { count: number; filter: ItemFilter } | null;
  includedTotal: number;
  onClose: () => void;
}) {
  const qc = useQueryClient();
  const [scope, setScope] = useState<Scope>(selection ? "selected" : "all");
  const [done, setDone] = useState<PromoteOut | null>(null);
  const [err, setErr] = useState<string | null>(null);

  const req = useMemo(() => {
    if (scope === "selected" && selection)
      return "ids" in selection.target ? { ids: selection.target.ids } : { filter: selection.target.filter };
    if (scope === "filtered" && filtered) return { filter: filtered.filter };
    return { all_included: true };
  }, [scope, selection, filtered]);

  useEffect(() => {
    const onKey = (e: KeyboardEvent) => e.key === "Escape" && onClose();
    window.addEventListener("keydown", onKey);
    return () => window.removeEventListener("keydown", onKey);
  }, [onClose]);

  const preview = useQuery({
    queryKey: ["promote-preview", catalogId, JSON.stringify(req)],
    queryFn: () =>
      api<PromoteOut>(`/supplier-catalogs/${catalogId}/promote/preview`, { method: "POST", body: req }),
    enabled: !done,
  });
  const promote = useMutation({
    mutationFn: () => api<PromoteOut>(`/supplier-catalogs/${catalogId}/promote`, { method: "POST", body: req }),
    onSuccess: (r) => {
      setDone(r);
      setErr(null);
      qc.invalidateQueries({ queryKey: ["catalog-items", catalogId] });
      qc.invalidateQueries({ queryKey: ["tally-preflight", catalogId] });
    },
    onError: (e) => setErr(e instanceof ApiError ? e.message : "Could not add the items. Try again."),
  });

  const p = done ?? preview.data;
  const todo = p ? p.create + p.link_existing + p.reuse : 0;

  return (
    <div
      className="fixed inset-0 z-50 grid place-items-center overflow-y-auto bg-black/40 p-4"
      onMouseDown={(e) => e.target === e.currentTarget && onClose()}
    >
      <div role="dialog" aria-modal="true" aria-labelledby="promote-title" className="card w-full max-w-lg bg-card p-5 shadow-xl">
        <div className="flex items-start justify-between gap-3">
          <div>
            <h2 id="promote-title" className="font-serif text-xl font-semibold">Add to items</h2>
            <p className="mt-1 text-sm text-muted">
              Creates an item for each product, with its code, your new price and the supplier price.
              Sending to Tally is optional and separate.
            </p>
          </div>
          <button type="button" className="btn-ghost h-9 px-3" onClick={onClose}>Close</button>
        </div>

        {!done && (
          <fieldset className="mt-4">
            <legend className="label">Items</legend>
            <div className="space-y-1.5 text-sm">
              {selection && (
                <label className="flex items-center gap-2">
                  <input type="radio" name="pscope" checked={scope === "selected"} onChange={() => setScope("selected")} />
                  Selected items ({selection.count})
                </label>
              )}
              {filtered && (
                <label className="flex items-center gap-2">
                  <input type="radio" name="pscope" checked={scope === "filtered"} onChange={() => setScope("filtered")} />
                  Items matching the current filter ({filtered.count})
                </label>
              )}
              <label className="flex items-center gap-2">
                <input type="radio" name="pscope" checked={scope === "all"} onChange={() => setScope("all")} />
                All included items ({includedTotal})
              </label>
            </div>
          </fieldset>
        )}

        <div className="mt-4 text-sm" aria-live="polite">
          {!p && preview.isLoading && <p className="text-muted">Checking…</p>}
          {p && (
            <ul className="space-y-1">
              <li>{done ? "Created" : "Will create"}: <b>{p.create}</b> new items</li>
              {p.link_existing > 0 && (
                <li>
                  {p.link_existing} match an item you already have, and are linked, not duplicated
                  {p.examples.length > 0 && <span className="text-muted"> (e.g. {p.examples.slice(0, 2).join(", ")})</span>}
                </li>
              )}
              {p.reuse > 0 && <li>{p.reuse} already have an item from another catalog (prices refreshed)</li>}
              {p.already > 0 && <li className="text-muted">{p.already} already added</li>}
              {p.renamed > 0 && (
                <li className="text-muted">
                  {p.renamed} get their code added to the name, because another product has the same name
                </li>
              )}
            </ul>
          )}
          <p className="mt-2 text-xs text-muted">Adding items also fixes their codes: they can no longer change.</p>
        </div>

        {err && <p className="err mt-3" role="alert">{err}</p>}
        <div className="mt-5 flex justify-end gap-2">
          {done ? (
            <button type="button" className="btn-primary" onClick={onClose}>Done</button>
          ) : (
            <button type="button" className="btn-primary" disabled={!p || todo === 0 || promote.isPending} onClick={() => promote.mutate()}>
              {promote.isPending ? "Adding…" : p && todo === 0 ? "Nothing to add" : `Add ${todo} to items`}
            </button>
          )}
        </div>
      </div>
    </div>
  );
}
