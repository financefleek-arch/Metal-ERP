import { useRef, useState } from "react";
import { Link, useNavigate } from "react-router-dom";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { api, ApiError, apiUpload } from "../../lib/api";
import {
  validCodePrefix,
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

export function CatalogListPage() {
  const nav = useNavigate();
  const qc = useQueryClient();
  const fileRef = useRef<HTMLInputElement>(null);
  const [dragOver, setDragOver] = useState(false);
  const [title, setTitle] = useState("");
  const [prefix, setPrefix] = useState("");
  const [notice, setNotice] = useState<string | null>(null);
  const [err, setErr] = useState<string | null>(null);

  const list = useQuery({
    queryKey: ["supplier-catalogs"],
    queryFn: () => api<CatalogListItem[]>("/supplier-catalogs"),
  });

  const prefixBad = prefix.trim() !== "" && !validCodePrefix(prefix);

  const upload = useMutation({
    mutationFn: (file: File) => {
      const fd = new FormData();
      fd.append("file", file);
      if (title.trim()) fd.append("title", title.trim());
      if (prefix.trim()) fd.append("code_prefix", prefix.trim());
      return apiUpload<CatalogUploadOut>("/supplier-catalogs", fd);
    },
    onSuccess: (c) => {
      qc.invalidateQueries({ queryKey: ["supplier-catalogs"] });
      if (c.already_exists) setNotice("You uploaded this file before. Opening that catalog.");
      nav(`/catalogs/${c.id}`);
    },
    onError: (e) => setErr(e instanceof ApiError ? e.message : "Upload failed. Try again."),
  });

  function pick(files: FileList | null) {
    setErr(null);
    setNotice(null);
    const file = files?.[0];
    if (!file) return;
    if (prefixBad) {
      setErr("Fix the code prefix first: 2 to 8 letters or digits.");
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
                onClick={() => fileRef.current?.click()}
              >
                Choose PDF
              </button>
            </div>
            <details className="mt-3 text-left text-sm">
              <summary className="cursor-pointer text-center text-muted">Options</summary>
              <div className="mx-auto mt-3 grid max-w-md grid-cols-1 gap-3 sm:grid-cols-2">
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
                <div>
                  <label className="label" htmlFor="catalog-up-prefix">
                    Item code prefix
                  </label>
                  <input
                    id="catalog-up-prefix"
                    className="field uppercase"
                    placeholder="From settings"
                    maxLength={8}
                    value={prefix}
                    onChange={(e) => setPrefix(e.target.value)}
                  />
                  {prefixBad && <p className="err">Use 2 to 8 letters or digits.</p>}
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
                <th className="px-4 py-2 font-medium">Code prefix</th>
                <th className="px-4 py-2 text-right font-medium">Multiplier</th>
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
                  <td className="px-4 py-3 font-mono text-xs">{c.code_prefix}</td>
                  <td className="px-4 py-3 text-right tabular-nums">
                    ×{Number(c.multiplier).toFixed(2)}
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
