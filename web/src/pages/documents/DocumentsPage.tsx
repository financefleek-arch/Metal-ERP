import { useMemo, useRef, useState } from "react";
import { Link, useNavigate, useSearchParams } from "react-router-dom";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { api, ApiError, apiUpload } from "../../lib/api";
import { useAuth } from "../../lib/auth";
import type { CatalogListItem, CatalogUploadOut } from "../../lib/catalog";
import type { InwardBillListItem } from "../../lib/inward";
import { SupplierPicker, type SupplierChoice } from "../../components/catalog/SupplierPicker";

const MAX_MB = 60;
type Kind = "bill" | "price_list";

interface Detect {
  kind: Kind;
  reasons: string[];
  pages: number;
  bills_enabled: boolean;
  price_lists_enabled: boolean;
}

interface Row {
  key: string;
  kind: Kind;
  title: string;
  sub: string;
  supplier: string | null;
  supplierId: string | null;
  size: string;
  status: string;
  tone: string;
  to: string;
  at: string;
}

/** What the upload found, shown once on the price list's review page. */
function importNote(c: CatalogUploadOut): string {
  const parts: string[] = [];
  if (c.matched_items > 0)
    parts.push(`${c.matched_items} products were already known: same code, group and name.`);
  if (c.new_products > 0) parts.push(`${c.new_products} are new.`);
  if (c.suggestions > 0)
    parts.push(`${c.suggestions} look like products you already have. Check the code column.`);
  if ((c.unread_price_lines ?? 0) > 0)
    parts.push(
      `${c.unread_price_lines} price lines had no picture, so those products were not read: add them by hand.`,
    );
  if ((c.price_changes ?? 0) > 0)
    parts.push(`${c.price_changes} prices moved since the last list: use the Price changed filter.`);
  return parts.join(" ");
}

const KIND_LABEL: Record<Kind, string> = { bill: "Bill", price_list: "Price list" };

/**
 * Supplier documents: one front door for a supplier's bills and price lists. Drop a PDF, see
 * what it looks like (and switch if that is wrong), then it goes to the right place. The two
 * kinds stay separate underneath; this is where they meet.
 */
export function DocumentsPage() {
  const { me } = useAuth();
  const nav = useNavigate();
  const qc = useQueryClient();
  const [params] = useSearchParams();
  const fileRef = useRef<HTMLInputElement>(null);
  const [file, setFile] = useState<File | null>(null);
  const [detect, setDetect] = useState<Detect | null>(null);
  const [kind, setKind] = useState<Kind | null>(null);
  const [supplier, setSupplier] = useState<SupplierChoice | null>(null);
  const [noSupplier, setNoSupplier] = useState(false);
  const [title, setTitle] = useState("");
  const [filter, setFilter] = useState<"all" | Kind>("all");
  const [err, setErr] = useState<string | null>(null);
  const [dragOver, setDragOver] = useState(false);

  const bills = !!me?.ext_inward_import;
  const lists = !!me?.ext_supplier_catalog;

  // arriving from a supplier's page: that supplier is already chosen
  const presetId = params.get("supplier");
  const preset = useQuery({
    queryKey: ["supplier-preset", presetId],
    enabled: !!presetId,
    queryFn: () => api<{ id: string; legal_name: string }>(`/parties/${presetId}`),
  });
  const chosen: SupplierChoice | null =
    supplier ?? (preset.data ? { id: preset.data.id, name: preset.data.legal_name } : null);

  const catalogs = useQuery({
    queryKey: ["supplier-catalogs"],
    queryFn: () => api<CatalogListItem[]>("/supplier-catalogs"),
    enabled: lists,
  });
  const inward = useQuery({
    queryKey: ["inward-bills"],
    queryFn: () => api<InwardBillListItem[]>("/inward-bills"),
    enabled: bills,
    refetchInterval: (q) =>
      (q.state.data ?? []).some((b) => b.status === "extracting") ? 2000 : false,
  });

  const rows: Row[] = useMemo(() => {
    const out: Row[] = [];
    for (const c of catalogs.data ?? [])
      out.push({
        key: `c-${c.id}`,
        kind: "price_list",
        title: c.title,
        sub: `${c.item_count} items · ${c.page_count} pages`,
        supplier: c.supplier_name,
        supplierId: c.supplier_party_id,
        size: c.source_filename,
        status: c.status === "ready" ? "Ready" : c.status === "error" ? "Error" : "Reading",
        tone: c.status === "ready" ? "bg-[#e6efe8] text-ok" : "bg-[#f1e7d6] text-warn",
        to: `/catalogs/${c.id}`,
        at: c.created_at,
      });
    for (const b of inward.data ?? [])
      out.push({
        key: `b-${b.id}`,
        kind: "bill",
        title: b.bill_no ? `Bill ${b.bill_no}` : b.source_filename,
        sub: `${b.bill_date ?? "no date"}${b.grand_total ? ` · ₹${b.grand_total}` : ""}`,
        supplier: b.supplier_name,
        supplierId: null,
        size: b.source_filename,
        status:
          b.status === "needs_review"
            ? "Needs review"
            : b.status === "approved"
              ? "Approved"
              : b.status === "extracting"
                ? "Reading"
                : b.status,
        tone:
          b.status === "approved"
            ? "bg-[#e6efe8] text-ok"
            : b.status === "needs_review"
              ? "bg-[#f1e7d6] text-warn"
              : "bg-line text-muted",
        to: `/inward/${b.id}`,
        at: b.created_at,
      });
    out.sort((a, b) => (a.at < b.at ? 1 : -1));
    return out;
  }, [catalogs.data, inward.data]);
  const shown = rows.filter((r) => filter === "all" || r.kind === filter);

  async function choose(f: File | null | undefined) {
    setErr(null);
    setDetect(null);
    setKind(null);
    if (!f) return;
    if (f.size > MAX_MB * 1024 * 1024) return setErr(`That file is over ${MAX_MB} MB.`);
    if (!f.name.toLowerCase().endsWith(".pdf")) return setErr("Upload a PDF file.");
    setFile(f);
    try {
      const fd = new FormData();
      fd.append("file", f);
      const d = await apiUpload<Detect>("/documents/detect", fd);
      setDetect(d);
      setKind(d.kind);
    } catch (e) {
      setErr(e instanceof ApiError ? e.message : "Could not look at that file. Try again.");
    }
  }

  const upload = useMutation({
    mutationFn: async () => {
      if (!file || !kind) throw new Error("no file");
      const fd = new FormData();
      if (kind === "price_list") {
        fd.append("file", file);
        if (title.trim()) fd.append("title", title.trim());
        if (chosen) fd.append("supplier_party_id", chosen.id);
        const c = await apiUpload<CatalogUploadOut>("/supplier-catalogs", fd);
        const note = importNote(c);
        if (note && !c.already_exists) {
          try {
            sessionStorage.setItem(`catalog-import-note:${c.id}`, note);
          } catch {
            /* the note is a convenience */
          }
        }
        return { to: `/catalogs/${c.id}` };
      }
      fd.append("files", file);
      const made = await apiUpload<{ id: string }[]>("/inward-bills", fd);
      return { to: `/inward/${made[0].id}` };
    },
    onSuccess: (r) => {
      qc.invalidateQueries({ queryKey: ["supplier-catalogs"] });
      qc.invalidateQueries({ queryKey: ["inward-bills"] });
      nav(r.to);
    },
    onError: (e) => setErr(e instanceof ApiError ? e.message : "Upload failed. Try again."),
  });

  const needsSupplier = kind === "price_list" && !chosen && !noSupplier;

  return (
    <div className="mx-auto max-w-4xl">
      <div className="mb-5">
        <h1 className="font-serif text-2xl font-semibold">Supplier documents</h1>
        <p className="mt-1 text-sm text-muted">
          A supplier&apos;s bill or price list: drop the PDF and we work out which it is.
        </p>
      </div>

      <div
        className={`card mb-6 border-2 border-dashed p-5 ${dragOver ? "border-accent bg-accent-soft" : ""}`}
        onDragOver={(e) => {
          e.preventDefault();
          setDragOver(true);
        }}
        onDragLeave={() => setDragOver(false)}
        onDrop={(e) => {
          e.preventDefault();
          setDragOver(false);
          void choose(e.dataTransfer.files?.[0]);
        }}
      >
        <input
          ref={fileRef}
          type="file"
          accept="application/pdf,.pdf"
          className="sr-only"
          onChange={(e) => {
            void choose(e.target.files?.[0]);
            e.target.value = "";
          }}
        />
        {upload.isPending ? (
          <div role="status" className="text-center">
            <p className="font-medium">Reading the document…</p>
            <p className="mt-1 text-sm text-muted">A large price list can take about half a minute.</p>
          </div>
        ) : !file ? (
          <div className="text-center">
            <p className="font-medium">Drop a supplier PDF here</p>
            <button type="button" className="btn-primary mt-3" onClick={() => fileRef.current?.click()}>
              Choose PDF
            </button>
          </div>
        ) : (
          <div>
            <p className="text-sm">
              <span className="font-medium">{file.name}</span>{" "}
              <button
                type="button"
                className="ml-2 text-xs text-accent underline"
                onClick={() => {
                  setFile(null);
                  setDetect(null);
                  setKind(null);
                }}
              >
                Choose another
              </button>
            </p>
            {!detect && !err && <p className="mt-2 text-sm text-muted">Looking at it…</p>}
            {detect && kind && (
              <>
                <p className="mt-3 text-sm" role="status">
                  Looks like a <b>{kind === "bill" ? "bill" : "price list"}</b>
                  {detect.reasons.length > 0 && (
                    <span className="text-muted"> ({detect.reasons.join("; ")})</span>
                  )}
                  .
                </p>
                <div className="mt-2 flex flex-wrap gap-2" role="group" aria-label="What is it?">
                  {(["bill", "price_list"] as Kind[]).map((k) => {
                    const on = k === "bill" ? detect.bills_enabled : detect.price_lists_enabled;
                    if (!on) return null;
                    return (
                      <button
                        key={k}
                        type="button"
                        aria-pressed={kind === k}
                        className={`rounded-full border px-3 py-1 text-xs ${
                          kind === k ? "border-accent bg-accent text-ground" : "border-line bg-card text-muted"
                        }`}
                        onClick={() => setKind(k)}
                      >
                        {k === "bill" ? "It is a bill" : "It is a price list"}
                      </button>
                    );
                  })}
                </div>

                {kind === "price_list" && (
                  <div className="mt-4 max-w-sm">
                    <label className="label" htmlFor="doc-supplier">
                      Supplier
                    </label>
                    {noSupplier ? (
                      <p className="rounded-md border border-line bg-card px-3 py-2 text-sm text-muted">
                        No supplier. Every product gets a new code each time.
                      </p>
                    ) : (
                      <SupplierPicker id="doc-supplier" value={chosen} onPick={setSupplier} />
                    )}
                    {!chosen && (
                      <label className="mt-2 flex items-center gap-2 text-xs text-muted">
                        <input type="checkbox" checked={noSupplier} onChange={(e) => setNoSupplier(e.target.checked)} />
                        Upload without a supplier
                      </label>
                    )}
                    <label className="label mt-3" htmlFor="doc-title">
                      Name (optional)
                    </label>
                    <input
                      id="doc-title"
                      className="field"
                      placeholder="From the file name"
                      value={title}
                      onChange={(e) => setTitle(e.target.value)}
                    />
                  </div>
                )}
                {kind === "bill" && (
                  <p className="mt-3 text-xs text-muted">
                    The supplier is read from the bill. You check it before anything is approved.
                  </p>
                )}
                <button
                  type="button"
                  className="btn-primary mt-4"
                  disabled={needsSupplier}
                  onClick={() => upload.mutate()}
                >
                  {kind === "bill" ? "Read the bill" : "Read the price list"}
                </button>
                {needsSupplier && <p className="mt-2 text-xs text-muted">Pick a supplier first.</p>}
              </>
            )}
          </div>
        )}
        {err && (
          <p className="err mt-3" role="alert">
            {err}
          </p>
        )}
      </div>

      <div className="mb-3 flex flex-wrap items-center gap-2">
        {(
          [
            ["all", "All"],
            ...(bills ? [["bill", "Bills"]] : []),
            ...(lists ? [["price_list", "Price lists"]] : []),
          ] as [string, string][]
        ).map(([k, label]) => (
          <button
            key={k}
            type="button"
            aria-pressed={filter === k}
            className={`rounded-full border px-3 py-1 text-xs ${
              filter === k ? "border-accent bg-accent text-ground" : "border-line bg-card text-muted"
            }`}
            onClick={() => setFilter(k as "all" | Kind)}
          >
            {label}
          </button>
        ))}
      </div>

      {(catalogs.isLoading || inward.isLoading) && <p className="text-sm text-muted">Loading…</p>}
      {rows.length === 0 && !catalogs.isLoading && !inward.isLoading && (
        <p className="text-sm text-muted">Nothing here yet. Drop a supplier PDF above.</p>
      )}
      {shown.length > 0 && (
        <div className="card overflow-x-auto">
          <table className="w-full text-sm">
            <thead>
              <tr className="border-b border-line text-left text-xs uppercase tracking-wide text-muted">
                <th className="px-4 py-2 font-medium">Document</th>
                <th className="px-4 py-2 font-medium">Kind</th>
                <th className="px-4 py-2 font-medium">Supplier</th>
                <th className="px-4 py-2 font-medium">Status</th>
              </tr>
            </thead>
            <tbody>
              {shown.map((r) => (
                <tr key={r.key} className="border-b border-line last:border-0 hover:bg-ground">
                  <td className="px-4 py-3">
                    <Link to={r.to} className="font-medium text-accent hover:underline">
                      {r.title}
                    </Link>
                    <div className="text-xs text-muted">{r.sub}</div>
                  </td>
                  <td className="px-4 py-3 text-xs">{KIND_LABEL[r.kind]}</td>
                  <td className="px-4 py-3 text-xs">
                    {r.supplierId && r.supplier ? (
                      <Link to={`/parties/${r.supplierId}`} className="text-accent hover:underline">
                        {r.supplier}
                      </Link>
                    ) : (
                      (r.supplier ?? <span className="text-muted">Not set</span>)
                    )}
                  </td>
                  <td className="px-4 py-3">
                    <span className={`rounded-full px-2 py-0.5 text-[10px] font-medium ${r.tone}`}>{r.status}</span>
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
