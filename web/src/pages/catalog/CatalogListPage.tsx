import { useEffect, useRef, useState } from "react";
import { Link, useNavigate, useSearchParams } from "react-router-dom";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { api, ApiError, apiUpload } from "../../lib/api";
import { SupplierPicker, type SupplierChoice } from "../../components/catalog/SupplierPicker";
import {
  marginLabel,
  type CatalogListItem,
  type CatalogStatus,
  type CatalogUploadOut,
} from "../../lib/catalog";

const STATUS_LABEL: Record<CatalogStatus, string> = {
  extracting: "Reading PDF",
  ready: "Ready",
  error: "Error",
};

const STATUS_CLASS: Record<CatalogStatus, string> = {
  extracting: "bg-accent-soft text-accent",
  ready: "bg-[#e6efe8] text-ok",
  error: "bg-[#f4e3df] text-danger",
};

const MAX_MB = 60;

function importNote(c: CatalogUploadOut): string {
  const parts: string[] = [];
  if (c.matched_items > 0)
    parts.push(`${c.matched_items} products were already known: same code, group and name.`);
  if (c.new_products > 0) parts.push(`${c.new_products} are new.`);
  if (c.suggestions > 0)
    parts.push(`${c.suggestions} look like products you already have. Check the code column.`);
  return parts.join(" ");
}

export function CatalogListPage() {
  const nav = useNavigate();
  const qc = useQueryClient();
  const fileRef = useRef<HTMLInputElement>(null);
  const [dragOver, setDragOver] = useState(false);
  const [title, setTitle] = useState("");
  const [supplier, setSupplier] = useState<SupplierChoice | null>(null);
  const [noSupplier, setNoSupplier] = useState(false);

  // Arriving from a supplier's party page: that supplier is already chosen.
  const [params] = useSearchParams();
  const presetSupplier = params.get("supplier");
  useEffect(() => {
    if (!presetSupplier) return;
    api<{ id: string; legal_name: string }>(`/parties/${presetSupplier}`)
      .then((p) => setSupplier({ id: p.id, name: p.legal_name }))
      .catch(() => {});
  }, [presetSupplier]);
  const [notice, setNotice] = useState<string | null>(null);
  const [err, setErr] = useState<string | null>(null);

  const list = useQuery({
    queryKey: ["supplier-catalogs"],
    queryFn: () => api<CatalogListItem[]>("/supplier-catalogs"),
  });

  const upload = useMutation({
    mutationFn: (file: File) => {
      const fd = new FormData();
      fd.append("file", file);
      if (title.trim()) fd.append("title", title.trim());
      if (supplier) fd.append("supplier_party_id", supplier.id);
      return apiUpload<CatalogUploadOut>("/supplier-catalogs", fd);
    },
    onSuccess: (c) => {
      qc.invalidateQueries({ queryKey: ["supplier-catalogs"] });
      if (c.already_exists) setNotice("You uploaded this file before. Opening that catalog.");
      else if (c.matched_items > 0 || c.suggestions > 0)
        sessionStorage.setItem(`catalog-import-note:${c.id}`, importNote(c));
      nav(`/catalogs/${c.id}`);
    },
    onError: (e) => setErr(e instanceof ApiError ? e.message : "Upload failed. Try again."),
  });

  function pick(files: FileList | null) {
    setErr(null);
    setNotice(null);
    const file = files?.[0];
    if (!file) return;
    if (!supplier && !noSupplier) {
      setErr("Pick the supplier first, so the same products keep the same codes next time.");
      return;
    }
    if (file.size > MAX_MB * 1024 * 1024) {
      setErr(`That file is over ${MAX_MB} MB. Split the catalog and upload the parts.`);
      return;
    }
    if (!file.name.toLowerCase().endsWith(".pdf")) {
      setErr("Upload a PDF file.");
      return;
    }
    upload.mutate(file);
  }

  return (
    <div className="max-w-4xl">
      <div className="mb-5">
        <h1 className="font-serif text-2xl font-semibold">Supplier catalogs</h1>
        <p className="mt-1 text-sm text-muted">
          Turn a supplier's price-list PDF into your own priced items, barcode labels and a
          customer catalog.
        </p>
      </div>

      <div
        className={`card mb-6 border-2 border-dashed p-5 text-center ${
          dragOver ? "border-accent bg-accent-soft" : ""
        }`}
        onDragOver={(e) => {
          e.preventDefault();
          setDragOver(true);
        }}
        onDragLeave={() => setDragOver(false)}
        onDrop={(e) => {
          e.preventDefault();
          setDragOver(false);
          pick(e.dataTransfer.files);
        }}
      >
        {upload.isPending ? (
          <div role="status" aria-live="polite">
            <p className="font-medium">Reading the catalog…</p>
            <p className="mt-1 text-sm text-muted">
              Finding every item, its photo and price. A large catalog can take about half a
              minute.
            </p>
          </div>
        ) : (
          <>
            <p className="font-medium">Drop a supplier catalog PDF here</p>
            <p className="mt-1 text-sm text-muted">
              Each product needs a photo with a price line under it, like “Rs 190 for 6 pcs”.
              Up to {MAX_MB} MB.
            </p>
            <div className="mx-auto mt-3 max-w-sm text-left">
              <label className="label" htmlFor="catalog-supplier">
                Supplier
              </label>
              {noSupplier ? (
                <p className="rounded-md border border-line bg-card px-3 py-2 text-sm text-muted">
                  No supplier. Every product gets a new code each time.
                </p>
              ) : (
                <SupplierPicker id="catalog-supplier" value={supplier} onPick={setSupplier} />
              )}
              <p className="mt-1 text-xs text-muted">
                Next time this supplier sends a catalog, products you already have keep their
                code, group and name.
              </p>
              {!supplier && (
                <label className="mt-2 flex items-center gap-2 text-xs text-muted">
                  <input
                    type="checkbox"
                    checked={noSupplier}
                    onChange={(e) => setNoSupplier(e.target.checked)}
                  />
                  Upload without a supplier
                </label>
              )}
            </div>
            <input
              ref={fileRef}
              id="catalog-file"
              type="file"
              accept="application/pdf,.pdf"
              className="sr-only"
              onChange={(e) => {
                pick(e.target.files);
                e.target.value = "";
              }}
            />
            <div className="mt-3 flex flex-wrap items-center justify-center gap-3">
              <button
                type="button"
                className="btn-primary"
                disabled={!supplier && !noSupplier}
                onClick={() => fileRef.current?.click()}
              >
                Choose PDF
              </button>
            </div>
            {!supplier && !noSupplier && (
              <p className="mt-2 text-xs text-muted">Pick a supplier above to choose a PDF.</p>
            )}
            <details className="mt-3 text-left text-sm">
              <summary className="cursor-pointer text-center text-muted">Options</summary>
              <div className="mx-auto mt-3 grid max-w-md grid-cols-1 gap-3">
                <div>
                  <label className="label" htmlFor="catalog-title">
                    Catalog name
                  </label>
                  <input
                    id="catalog-title"
                    className="field"
                    placeholder="From the file name"
                    value={title}
                    onChange={(e) => setTitle(e.target.value)}
                  />
                </div>
              </div>
            </details>
          </>
        )}
        {err && (
          <p className="err mt-3" role="alert">
            {err}
          </p>
        )}
        {notice && <p className="mt-3 text-sm text-muted">{notice}</p>}
      </div>

      {list.isLoading && <p className="text-sm text-muted">Loading…</p>}
      {list.isError && <p className="err">Could not load catalogs. Try again.</p>}

      {list.data && list.data.length === 0 && (
        <p className="text-sm text-muted">No catalogs yet. Upload one above to start.</p>
      )}

      {list.data && list.data.length > 0 && (
        <div className="card overflow-x-auto">
          <table className="w-full text-sm">
            <thead>
              <tr className="border-b border-line text-left text-xs uppercase tracking-wide text-muted">
                <th className="px-4 py-2 font-medium">Catalog</th>
                <th className="px-4 py-2 text-right font-medium">Pages</th>
                <th className="px-4 py-2 text-right font-medium">Items</th>
                <th className="px-4 py-2 font-medium">Supplier</th>
                <th className="px-4 py-2 text-right font-medium">Bulk margin</th>
                <th className="px-4 py-2 font-medium">Status</th>
              </tr>
            </thead>
            <tbody>
              {list.data.map((c) => (
                <tr key={c.id} className="border-b border-line last:border-0 hover:bg-ground">
                  <td className="px-4 py-3">
                    <Link to={`/catalogs/${c.id}`} className="font-medium text-accent hover:underline">
                      {c.title}
                    </Link>
                    <div className="text-xs text-muted">{c.source_filename}</div>
                  </td>
                  <td className="px-4 py-3 text-right tabular-nums">{c.page_count}</td>
                  <td className="px-4 py-3 text-right tabular-nums">{c.item_count}</td>
                  <td className="px-4 py-3 text-xs">
                    {c.supplier_party_id && c.supplier_name ? (
                      <Link to={`/parties/${c.supplier_party_id}`} className="text-accent hover:underline">
                        {c.supplier_name}
                      </Link>
                    ) : (
                      <span className="text-muted">Not set</span>
                    )}
                  </td>
                  <td className="px-4 py-3 text-right tabular-nums">
                    {marginLabel(c.bulk_margin_pct)}
                  </td>
                  <td className="px-4 py-3">
                    <span
                      className={`rounded-full px-2 py-0.5 text-[10px] font-medium ${STATUS_CLASS[c.status]}`}
                    >
                      {STATUS_LABEL[c.status]}
                    </span>
                  </td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      )}
    </div>
  );
}
