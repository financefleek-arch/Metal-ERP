import { useEffect, useRef, useState } from "react";
import { useNavigate } from "react-router-dom";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { api, ApiError } from "../../lib/api";
import { downloadFile } from "../../lib/download";
import { inr } from "../../lib/previewTotal";
import { WhatsappBadge } from "../../components/WhatsappStatus";
import type { InvoiceListItem, InvoicePaymentStatus, InvoiceStatus } from "../../lib/types";

type Scope = "" | InvoiceStatus;

const FILTERS: { key: Scope; label: string }[] = [
  { key: "", label: "All" },
  { key: "draft", label: "Drafts" },
  { key: "final", label: "Final" },
  { key: "cancelled", label: "Cancelled" },
];

/** A row with a number is final; only the exceptions get a coloured word. */
function stateLabel(s: InvoiceStatus): { text: string; cls: string } | null {
  if (s === "draft") return { text: "Draft", cls: "text-warn" };
  if (s === "cancelled") return { text: "Cancelled", cls: "text-danger" };
  return null;
}

function PaymentDot({ s }: { s: InvoicePaymentStatus | null }) {
  if (!s) return null;
  const cls =
    s === "paid" ? "bg-ok" : s === "partial" ? "bg-warn" : "bg-danger";
  const word = s === "paid" ? "Paid" : s === "partial" ? "Partial" : "Unpaid";
  return (
    <span className="inline-flex items-center gap-1.5 font-semibold">
      <span className={`h-[7px] w-[7px] rounded-full ${cls}`} />
      {word}
    </span>
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

  const del = useMutation({
    mutationFn: (id: string) => api(`/invoices/${id}`, { method: "DELETE" }),
    onSuccess: () => qc.invalidateQueries({ queryKey: ["invoices"] }),
    onError: (e) => setErr(e instanceof ApiError ? e.message : "Delete failed"),
  });

  const canDelete = (s: InvoiceStatus) => s === "draft" || s === "cancelled";

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

      <div className="card overflow-visible">
        <div className="hidden grid-cols-[48px_minmax(0,1fr)_128px_150px_84px] gap-3 border-b border-line bg-ground px-3 py-2 text-[10px] font-semibold uppercase tracking-wide text-muted md:grid">
          <span>No.</span>
          <span>Party</span>
          <span className="text-right">Amount</span>
          <span>WhatsApp</span>
          <span className="text-right">Actions</span>
        </div>

        {list.isLoading && <div className="px-3 py-8 text-center text-xs text-muted">Loading…</div>}
        {!list.isLoading && list.data?.length === 0 && (
          <div className="px-3 py-10 text-center text-xs text-muted">
            {q || scope ? "No matches." : "No invoices yet — start one with “+ New invoice”."}
          </div>
        )}

        {list.data?.map((iv) => {
          const st = stateLabel(iv.status);
          const dateStr = new Date(iv.date).toLocaleDateString("en-IN", {
            day: "2-digit",
            month: "short",
            year: "2-digit",
          });
          return (
            <div
              key={iv.id}
              className="grid grid-cols-[36px_1fr_auto] items-start gap-x-3 gap-y-2 border-b border-[#f3eee4] px-3 py-3 text-sm last:border-0 hover:bg-[#fcfbf8] md:grid-cols-[48px_minmax(0,1fr)_128px_150px_84px] md:items-center md:py-2.5"
            >
              {/* number */}
              <button
                className="text-left font-mono font-semibold text-accent hover:underline"
                onClick={() => nav(`/invoices/${iv.id}`)}
              >
                {iv.number ?? "—"}
              </button>

              {/* party + meta line */}
              <button
                className="col-start-2 min-w-0 text-left"
                onClick={() => nav(`/invoices/${iv.id}`)}
              >
                <div
                  className={`truncate font-semibold hover:underline ${
                    iv.party_id ? "" : "italic text-muted"
                  }`}
                >
                  {iv.party_name}
                </div>
                <div className="mt-0.5 flex flex-wrap items-center gap-x-2 gap-y-0.5 text-[11px] text-muted">
                  {st && (
                    <span className={`font-semibold uppercase tracking-wide ${st.cls}`}>
                      {st.text}
                    </span>
                  )}
                  <span>{dateStr}</span>
                  <PaymentDot s={iv.payment_status} />
                </div>
              </button>

              {/* amount */}
              <span className="col-start-3 row-start-1 text-right font-mono font-semibold md:col-start-auto md:row-start-auto">
                {iv.grand_total ? inr(iv.grand_total) : "—"}
              </span>

              {/* whatsapp — only meaningful once final */}
              <span className="col-start-2 md:col-start-auto">
                {iv.status === "final" ? (
                  <WhatsappBadge status={iv.whatsapp_status} />
                ) : (
                  <span className="hidden md:inline text-[11px] text-[#b7b1a4]">—</span>
                )}
              </span>

              {/* actions: PDF stays one tap, everything else in the menu */}
              <div className="col-start-3 row-start-1 flex items-center justify-end gap-1 md:col-start-auto md:row-start-auto">
                {iv.status === "final" && (
                  <button
                    className="rounded-md border border-line px-2 py-1 text-[11px] hover:bg-ground"
                    onClick={() => openPdf(iv.id)}
                  >
                    PDF
                  </button>
                )}
                <RowMenu
                  iv={iv}
                  onOpen={() => nav(`/invoices/${iv.id}`)}
                  onPdf={() => openPdf(iv.id)}
                  onDuplicate={() => dup.mutate(iv.id)}
                  onDelete={
                    canDelete(iv.status)
                      ? () => {
                          const msg =
                            iv.status === "draft"
                              ? "Delete this draft?"
                              : `Delete cancelled invoice #${iv.number} permanently?`;
                          if (confirm(msg)) del.mutate(iv.id);
                        }
                      : undefined
                  }
                />
              </div>
            </div>
          );
        })}
      </div>
    </div>
  );
}

// --------------------------------------------------------------------------
// per-row overflow menu
// --------------------------------------------------------------------------

function RowMenu({
  iv,
  onOpen,
  onPdf,
  onDuplicate,
  onDelete,
}: {
  iv: InvoiceListItem;
  onOpen: () => void;
  onPdf: () => void;
  onDuplicate: () => void;
  onDelete?: () => void;
}) {
  const [open, setOpen] = useState(false);
  const ref = useRef<HTMLDivElement>(null);

  useEffect(() => {
    if (!open) return;
    const close = (e: MouseEvent) => {
      if (ref.current && !ref.current.contains(e.target as Node)) setOpen(false);
    };
    document.addEventListener("mousedown", close);
    return () => document.removeEventListener("mousedown", close);
  }, [open]);

  const item = "block w-full rounded px-2.5 py-1.5 text-left text-xs hover:bg-ground";

  return (
    <div className="relative" ref={ref}>
      <button
        className="rounded-md px-2 py-1 text-base leading-none text-muted hover:bg-ground"
        aria-label="More actions"
        onClick={() => setOpen((v) => !v)}
      >
        ⋯
      </button>
      {open && (
        <div className="absolute right-0 top-[110%] z-20 min-w-[170px] rounded-lg border border-line bg-card p-1 shadow-xl">
          <button
            className={item}
            onClick={() => {
              setOpen(false);
              onOpen();
            }}
          >
            {iv.status === "draft" ? "Open / edit" : "Open"}
          </button>
          {iv.status === "final" && (
            <button
              className={item}
              onClick={() => {
                setOpen(false);
                onPdf();
              }}
            >
              Download PDF
            </button>
          )}
          <button
            className={item}
            onClick={() => {
              setOpen(false);
              onDuplicate();
            }}
          >
            Duplicate
          </button>
          {onDelete && (
            <>
              <hr className="my-1 border-line" />
              <button
                className={`${item} text-danger`}
                onClick={() => {
                  setOpen(false);
                  onDelete();
                }}
              >
                {iv.status === "draft" ? "Delete draft" : "Delete permanently"}
              </button>
            </>
          )}
        </div>
      )}
    </div>
  );
}
