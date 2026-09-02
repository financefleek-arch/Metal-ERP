import { useState } from "react";
import { useNavigate } from "react-router-dom";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { api, ApiError } from "../../lib/api";
import { downloadFile } from "../../lib/download";
import { inr } from "../../lib/previewTotal";
import type { InvoiceListItem, InvoiceStatus } from "../../lib/types";

type Scope = "" | InvoiceStatus;

const FILTERS: { key: Scope; label: string }[] = [
  { key: "", label: "All" },
  { key: "draft", label: "Drafts" },
  { key: "final", label: "Final" },
  { key: "cancelled", label: "Cancelled" },
];

function statusBadge(s: InvoiceStatus) {
  const cls =
    s === "final"
      ? "bg-[#e3efe6] text-[#3f7a4f]"
      : s === "cancelled"
        ? "bg-[#f1e0e0] text-danger"
        : "bg-[#f1e7d6] text-warn";
  return (
    <span className={`rounded-sm px-1.5 py-0.5 text-[10px] font-bold uppercase ${cls}`}>{s}</span>
  );
}

export function InvoiceListPage() {
  const nav = useNavigate();
  const qc = useQueryClient();
  const [q, setQ] = useState("");
  const [scope, setScope] = useState<Scope>("");
  const [err, setErr] = useState<string | null>(null);

  const params = new URLSearchParams();
  if (q.trim()) params.set("q", q.trim());
  if (scope) params.set("status", scope);

  const list = useQuery({
    queryKey: ["invoices", q, scope],
    queryFn: () => api<InvoiceListItem[]>(`/invoices?${params.toString()}`),
  });

  const dup = useMutation({
    mutationFn: (id: string) => api<{ id: string }>(`/invoices/${id}/duplicate`, { method: "POST" }),
    onSuccess: (r) => {
      qc.invalidateQueries({ queryKey: ["invoices"] });
      nav(`/invoices/${r.id}`);
    },
    onError: (e) => setErr(e instanceof ApiError ? e.message : "Duplicate failed"),
  });

  const cancel = useMutation({
    mutationFn: (id: string) => api(`/invoices/${id}/cancel`, { method: "POST" }),
    onSuccess: () => qc.invalidateQueries({ queryKey: ["invoices"] }),
    onError: (e) => setErr(e instanceof ApiError ? e.message : "Cancel failed"),
  });

  const del = useMutation({
    mutationFn: (id: string) => api(`/invoices/${id}`, { method: "DELETE" }),
    onSuccess: () => qc.invalidateQueries({ queryKey: ["invoices"] }),
    onError: (e) => setErr(e instanceof ApiError ? e.message : "Delete failed"),
  });

  function openPdf(id: string) {
    // stream endpoint needs the bearer header — fetch as blob then save with
    // the server-provided "<Party> <date> <total>.pdf" filename
    downloadFile(`/invoices/${id}/pdf`, `invoice-${id}.pdf`).catch(() =>
      setErr("PDF not ready — open the invoice and re-render."),
    );
  }

  return (
    <div className="mx-auto flex max-w-5xl flex-col gap-3">
      <div className="flex flex-wrap items-center justify-between gap-2">
        <h1 className="font-serif text-lg font-semibold">Sales invoices</h1>
        <button className="btn-primary h-9 px-4 text-sm" onClick={() => nav("/invoices/new")}>
          + New invoice
        </button>
      </div>

      <div className="card flex flex-col gap-2 p-3">
        <div className="flex flex-wrap items-center gap-2">
          <input
            className="field h-8 max-w-xs text-xs"
            placeholder="search by party name…"
            value={q}
            onChange={(e) => setQ(e.target.value)}
          />
          <div className="flex flex-wrap gap-1.5">
            {FILTERS.map((f) => (
              <button
                key={f.key}
                onClick={() => setScope(f.key)}
                className={`rounded-full border px-3 py-1 text-xs ${
                  scope === f.key
                    ? "border-ink bg-ink text-ground"
                    : "border-line bg-card text-muted hover:bg-ground"
                }`}
              >
                {f.label}
              </button>
            ))}
          </div>
        </div>
      </div>

      {err && <p className="err">{err}</p>}

      <div className="card overflow-hidden">
        <div className="hidden grid-cols-[70px_100px_1fr_130px_90px_180px] gap-2 border-b border-line bg-ground px-3 py-2 text-[10px] font-semibold uppercase tracking-wide text-muted md:grid">
          <span>No.</span>
          <span>Date</span>
          <span>Party</span>
          <span className="text-right">Amount</span>
          <span>Status</span>
          <span className="text-right">Actions</span>
        </div>

        {list.isLoading && <div className="px-3 py-8 text-center text-xs text-muted">Loading…</div>}
        {!list.isLoading && list.data?.length === 0 && (
          <div className="px-3 py-10 text-center text-xs text-muted">
            {q || scope ? "No matches." : "No invoices yet — start one with “+ New invoice”."}
          </div>
        )}

        {list.data?.map((iv) => (
          <div
            key={iv.id}
            className="grid grid-cols-2 gap-2 border-b border-[#f3eee4] px-3 py-3 text-sm md:grid-cols-[70px_100px_1fr_130px_90px_180px] md:items-center md:py-2"
          >
            <button
              className="text-left font-mono font-semibold text-accent hover:underline"
              onClick={() => nav(`/invoices/${iv.id}`)}
            >
              {iv.number ?? "—"}
            </button>
            <span className="text-xs text-muted md:text-sm">
              {new Date(iv.date).toLocaleDateString("en-IN", {
                day: "2-digit",
                month: "short",
                year: "2-digit",
              })}
            </span>
            <button
              className="col-span-2 truncate text-left hover:underline md:col-span-1"
              onClick={() => nav(`/invoices/${iv.id}`)}
            >
              {iv.party_name}
            </button>
            <span className="text-right font-mono">
              {iv.grand_total ? inr(iv.grand_total) : "—"}
            </span>
            <span>{statusBadge(iv.status)}</span>
            <div className="col-span-2 flex flex-wrap justify-end gap-1.5 md:col-span-1">
              {iv.status === "final" && (
                <button
                  className="rounded-md border border-line px-2 py-1 text-[11px] hover:bg-ground"
                  onClick={() => openPdf(iv.id)}
                >
                  PDF
                </button>
              )}
              <button
                className="rounded-md border border-line px-2 py-1 text-[11px] hover:bg-ground"
                onClick={() => dup.mutate(iv.id)}
              >
                Duplicate
              </button>
              {iv.status === "draft" && (
                <button
                  className="rounded-md border border-line px-2 py-1 text-[11px] text-danger hover:bg-ground"
                  onClick={() => {
                    if (confirm("Delete this draft?")) del.mutate(iv.id);
                  }}
                >
                  Delete
                </button>
              )}
              {iv.status === "final" && (
                <button
                  className="rounded-md border border-line px-2 py-1 text-[11px] text-danger hover:bg-ground"
                  onClick={() => {
                    if (confirm(`Cancel invoice #${iv.number}? The number is not reused.`))
                      cancel.mutate(iv.id);
                  }}
                >
                  Cancel
                </button>
              )}
            </div>
          </div>
        ))}
      </div>
    </div>
  );
}
