import { useEffect, useState } from "react";
import { useQuery } from "@tanstack/react-query";
import { api, ApiError, getToken } from "../../lib/api";
import { COLUMN_CHOICES, type CatalogColumns, type OutputJob } from "../../lib/catalog";

export type PdfTarget = { ids: string[] } | { filter: object } | { all_included: true };

interface Check {
  selected: number;
  included: number;
  no_price: number;
  missing_photos: number;
}

const SYNC_LIMIT = 100; // mirrors the server: larger lists build in the background

const authHeaders = (): Record<string, string> => {
  const t = getToken();
  return t ? { Authorization: `Bearer ${t}` } : {};
};

function save(blob: Blob, name: string) {
  const url = URL.createObjectURL(blob);
  const a = document.createElement("a");
  a.href = url;
  a.download = name;
  document.body.appendChild(a);
  a.click();
  a.remove();
  window.setTimeout(() => URL.revokeObjectURL(url), 10_000);
}

/**
 * PDF in, PDF out: a customer PDF straight from this price list, with your prices, codes and groups.
 * The products do not have to be in your items, and stock plays no part. An unchanged list is not
 * built again: the server hands back the PDF it already made.
 */
export function PriceListPdfDialog({
  catalogId,
  defaultTitle,
  target,
  onClose,
}: {
  catalogId: string;
  defaultTitle: string;
  target: PdfTarget;
  onClose: () => void;
}) {
  const [title, setTitle] = useState(defaultTitle);
  const [columns, setColumns] = useState<CatalogColumns>(3);
  const [grouped, setGrouped] = useState(true);
  const [showCode, setShowCode] = useState(true);
  const [piece, setPiece] = useState(false);
  const [busy, setBusy] = useState(false);
  const [done, setDone] = useState<string | null>(null);
  const [err, setErr] = useState<string | null>(null);

  useEffect(() => {
    const onKey = (e: KeyboardEvent) => e.key === "Escape" && onClose();
    window.addEventListener("keydown", onKey);
    return () => window.removeEventListener("keydown", onKey);
  }, [onClose]);

  const layout = {
    columns,
    group_by: grouped ? "group" : "none",
    contents: grouped,
    show_code: showCode,
    price_basis: piece ? "piece" : "pack",
  };
  const check = useQuery({
    queryKey: ["price-list-pdf-check", catalogId, target],
    queryFn: () =>
      api<Check>(`/supplier-catalogs/${catalogId}/customer-pdf/check`, {
        method: "POST",
        body: { ...target, ...layout },
      }),
  });
  const c = check.data;

  async function make() {
    setBusy(true);
    setErr(null);
    setDone(null);
    try {
      let job = await api<OutputJob>(`/supplier-catalogs/${catalogId}/customer-pdf`, {
        method: "POST",
        body: { ...target, ...layout, title: title.trim() || undefined },
      });
      while (job.status === "queued" || job.status === "running") {
        await new Promise((r) => window.setTimeout(r, 1500));
        job = await api<OutputJob>(`/supplier-catalogs/${catalogId}/outputs/${job.id}`);
      }
      if (job.status === "error") throw new ApiError(500, job.error ?? "The PDF could not be built.");
      const res = await fetch(`/api/supplier-catalogs/${catalogId}/outputs/${job.id}/file`, {
        headers: authHeaders(),
      });
      if (!res.ok) throw new ApiError(res.status, "That file is no longer available. Try again.");
      const name = `${(title.trim() || "catalog").replace(/[^A-Za-z0-9]+/g, "-").toLowerCase()}.pdf`;
      save(await res.blob(), name);
      setDone(job.page_count ? `${job.page_count} pages downloaded.` : "Downloaded.");
    } catch (e) {
      setErr(e instanceof ApiError ? e.message : "Could not make the PDF. Try again.");
    } finally {
      setBusy(false);
    }
  }

  const included = c?.included ?? 0;
  return (
    <div
      className="fixed inset-0 z-50 grid place-items-center overflow-y-auto bg-black/40 p-4"
      onMouseDown={(e) => {
        if (e.target === e.currentTarget) onClose();
      }}
    >
      <div role="dialog" aria-modal="true" aria-labelledby="pp-title" className="card w-full max-w-md bg-card p-5 shadow-xl">
        <div className="flex items-start justify-between gap-3">
          <div>
            <h2 id="pp-title" className="font-serif text-xl font-semibold">
              Customer PDF
            </h2>
            <p className="mt-1 text-sm text-muted">
              Your prices, codes and groups with the photos from this list. Supplier prices and codes are never
              shown. Nothing is added to your items.
            </p>
          </div>
          <button type="button" className="btn-ghost h-9 px-3" onClick={onClose}>
            Close
          </button>
        </div>

        <div className="mt-4">
          <label className="label" htmlFor="pp-name">
            Title on the cover
          </label>
          <input id="pp-name" className="field" maxLength={200} value={title} onChange={(e) => setTitle(e.target.value)} />
        </div>

        <fieldset className="mt-3">
          <legend className="label">Layout</legend>
          <div className="flex flex-wrap gap-1.5">
            {COLUMN_CHOICES.map((ch) => (
              <label
                key={ch.value}
                className={`cursor-pointer rounded-full border px-3 py-1 text-xs ${
                  columns === ch.value ? "border-accent bg-accent text-ground" : "border-line bg-card text-muted"
                }`}
              >
                <input
                  type="radio"
                  name="pp-cols"
                  className="sr-only"
                  checked={columns === ch.value}
                  onChange={() => setColumns(ch.value)}
                />
                {ch.name}
              </label>
            ))}
          </div>
          <div className="mt-2 space-y-1.5 text-sm">
            <label className="flex items-center gap-1.5">
              <input type="checkbox" checked={grouped} onChange={(e) => setGrouped(e.target.checked)} />
              Group the products, with a contents page
            </label>
            <label className="flex items-center gap-1.5">
              <input type="checkbox" checked={showCode} onChange={(e) => setShowCode(e.target.checked)} />
              Item code
            </label>
            <label className="flex items-center gap-1.5">
              <input type="checkbox" checked={piece} onChange={(e) => setPiece(e.target.checked)} />
              Show the price per piece instead of per pack
            </label>
          </div>
        </fieldset>

        <p className="mt-3 text-sm" aria-live="polite">
          {c ? (
            <>
              <span className="font-medium tabular-nums">{c.included.toLocaleString("en-IN")}</span> of{" "}
              <span className="tabular-nums">{c.selected.toLocaleString("en-IN")}</span> products go in
              {c.no_price > 0 && <span className="text-muted"> · {c.no_price} left out (no price)</span>}
              {c.missing_photos > 0 && <span className="text-muted"> · {c.missing_photos} without a photo</span>}
              {c.included > SYNC_LIMIT && <span className="text-muted"> · built in the background</span>}
            </>
          ) : (
            <span className="text-muted">{check.isError ? "Could not check the list." : "Checking the list…"}</span>
          )}
        </p>
        {err && (
          <p className="err" role="alert">
            {err}
          </p>
        )}
        {done && (
          <p className="mt-2 text-sm text-ok" role="status">
            {done}
          </p>
        )}
        <div className="mt-4 flex justify-end gap-2">
          <button type="button" className="btn-ghost" onClick={onClose}>
            {done ? "Done" : "Cancel"}
          </button>
          <button type="button" className="btn-primary" disabled={busy || included < 1} onClick={make}>
            {busy ? "Making the PDF…" : "Download PDF"}
          </button>
        </div>
      </div>
    </div>
  );
}
