import { useState } from "react";
import { Link } from "react-router-dom";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { api, ApiError, getToken } from "../lib/api";
import { ShareLinksPanel } from "../components/catalog/ShareLinksPanel";
import { fileSize, type CustomerCatalog, type OutputJob } from "../lib/catalog";

async function openFile(c: CustomerCatalog) {
  const t = getToken();
  const res = await fetch(`/api/customer-catalogs/${c.id}/file`, {
    headers: t ? { Authorization: `Bearer ${t}` } : {},
  });
  if (!res.ok) throw new ApiError(res.status, "That file is no longer available.");
  const url = URL.createObjectURL(await res.blob());
  window.open(url, "_blank", "noopener");
  window.setTimeout(() => URL.revokeObjectURL(url), 60_000);
}

/** Customer catalogs made from items: open, rebuild as a new version, delete. */
export function CustomerCatalogsPage() {
  const qc = useQueryClient();
  const [err, setErr] = useState<string | null>(null);
  const [busy, setBusy] = useState<string | null>(null);
  const [showOld, setShowOld] = useState(false);
  const list = useQuery({ queryKey: ["customer-catalogs"], queryFn: () => api<CustomerCatalog[]>("/customer-catalogs") });

  const onError = (e: unknown) => {
    setBusy(null);
    setErr(e instanceof ApiError ? e.message : "That did not work. Try again.");
  };
  const done = () => {
    setBusy(null);
    setErr(null);
    qc.invalidateQueries({ queryKey: ["customer-catalogs"] });
  };

  const rebuild = useMutation({
    mutationFn: async (c: CustomerCatalog) => {
      setBusy(c.id);
      const t = getToken();
      const res = await fetch(`/api/customer-catalogs/${c.id}/rebuild`, {
        method: "POST",
        headers: { "Content-Type": "application/json", ...(t ? { Authorization: `Bearer ${t}` } : {}) },
        body: JSON.stringify({}),
      });
      if (res.status === 202) {
        let job = (await res.json()) as OutputJob;
        while (job.status !== "done" && job.status !== "error") {
          await new Promise((r) => window.setTimeout(r, 1500));
          job = await api<OutputJob>(`/customer-catalogs/jobs/${job.id}`);
        }
        if (job.status === "error") throw new ApiError(500, job.error ?? "The catalog could not be built.");
        return;
      }
      if (!res.ok) {
        const d = (await res.json().catch(() => ({}))) as { detail?: unknown };
        throw new ApiError(res.status, typeof d.detail === "string" ? d.detail : "Could not rebuild.");
      }
    },
    onSuccess: done,
    onError,
  });
  const remove = useMutation({
    mutationFn: (c: CustomerCatalog) => {
      setBusy(c.id);
      return api(`/customer-catalogs/${c.id}`, { method: "DELETE" });
    },
    onSuccess: done,
    onError,
  });

  const rows = (list.data ?? []).filter((c) => showOld || c.latest);
  const older = (list.data ?? []).filter((c) => !c.latest).length;

  return (
    <div className="mx-auto max-w-4xl">
      <div className="flex flex-wrap items-end justify-between gap-3">
        <div>
          <h1 className="font-serif text-2xl font-semibold">Customer catalogs</h1>
          <p className="mt-1 text-sm text-muted">
            PDFs made from your items, with your prices and photos. To make one, select items on the{" "}
            <Link to="/items" className="text-accent underline underline-offset-2">
              Items page
            </Link>{" "}
            and choose Make customer catalog.
          </p>
        </div>
        {older > 0 && (
          <label className="flex items-center gap-1.5 text-sm">
            <input type="checkbox" checked={showOld} onChange={(e) => setShowOld(e.target.checked)} />
            Show older versions ({older})
          </label>
        )}
      </div>

      {err && (
        <p className="err" role="alert">
          {err}
        </p>
      )}

      <div className="card mt-4 divide-y divide-line bg-card">
        {list.isLoading && <p className="p-4 text-sm text-muted">Loading…</p>}
        {list.data && rows.length === 0 && (
          <p className="p-6 text-center text-sm text-muted">No customer catalogs yet.</p>
        )}
        {rows.map((c) => (
          <div key={c.id} className="flex flex-wrap items-center gap-x-4 gap-y-2 p-3">
            <div className="min-w-0 flex-1">
              <p className="truncate font-medium">
                {c.title} <span className="text-xs font-normal text-muted">v{c.version}</span>
                {c.stale && (
                  <span className="ml-2 rounded-full border border-warn px-2 py-0.5 text-[10px] font-medium text-warn">
                    Out of date
                  </span>
                )}
              </p>
              <p className="text-xs text-muted tabular-nums">
                {c.item_count.toLocaleString("en-IN")} items · {c.page_count} pages · {fileSize(c.byte_size)} ·{" "}
                {new Date(c.created_at).toLocaleDateString("en-IN")} ·{" "}
                {c.selection_kind === "filter" ? "follows a filter" : "chosen items"}
              </p>
            </div>
            <div className="flex gap-2">
              <button type="button" className="btn-ghost h-8 px-3 text-xs" onClick={() => openFile(c).catch(onError)}>
                Open PDF
              </button>
              {c.latest && (
                <button
                  type="button"
                  className={`${c.stale ? "btn-primary" : "btn-ghost"} h-8 px-3 text-xs`}
                  disabled={busy === c.id}
                  onClick={() => rebuild.mutate(c)}
                >
                  {busy === c.id && rebuild.isPending ? "Building…" : "Make new version"}
                </button>
              )}
              <button
                type="button"
                className="btn-ghost h-8 px-3 text-xs"
                disabled={busy === c.id}
                onClick={() => {
                  if (window.confirm(`Delete "${c.title}" v${c.version}?`)) remove.mutate(c);
                }}
              >
                Delete
              </button>
            </div>
          </div>
        ))}
      </div>

      <ShareLinksPanel />
    </div>
  );
}
