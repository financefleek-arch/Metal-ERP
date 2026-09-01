import { useState } from "react";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { api, ApiError, getToken } from "../../lib/api";
import { useIsDesktop } from "../../lib/useIsDesktop";
import type { ApproveResult, InwardBill } from "../../lib/inward";

function money(v: string | null): string {
  if (v == null) return "—";
  const n = Number(v);
  return Number.isNaN(n) ? v : n.toLocaleString("en-IN", { minimumFractionDigits: 2 });
}

function supplierAddress(staged: Record<string, unknown> | null): string | null {
  const a = staged?.address as Record<string, string | null> | undefined;
  if (!a) return null;
  const parts = [a.line1, a.line2, a.city, a.pincode].filter(Boolean);
  return parts.length ? parts.join(", ") : null;
}

function flagChip(flag: string | null) {
  if (!flag) return null;
  const map: Record<string, string> = {
    unknown_hsn: "bg-[#f4e3df] text-danger",
    low_confidence: "bg-[#f1e7d6] text-warn",
    ambiguous: "bg-[#f1e7d6] text-warn",
    new: "bg-[#efe9df] text-muted",
  };
  return (
    <span className={`ml-2 rounded-full px-2 py-0.5 text-[10px] ${map[flag] ?? "bg-line"}`}>
      {flag}
    </span>
  );
}

export function InwardReviewPane({ billId }: { billId: string }) {
  const qc = useQueryClient();
  const isDesktop = useIsDesktop();
  const [actionErr, setActionErr] = useState<string | null>(null);
  // Mobile-only: which of the two panes is showing.
  const [mobileTab, setMobileTab] = useState<"bill" | "review">("review");

  const bill = useQuery({
    queryKey: ["inward-bill", billId],
    queryFn: () => api<InwardBill>(`/inward-bills/${billId}`),
  });

  const approve = useMutation({
    mutationFn: () =>
      api<ApproveResult>(`/inward-bills/${billId}/approve`, { method: "POST" }),
    onSuccess: () => {
      setActionErr(null);
      void qc.invalidateQueries({ queryKey: ["inward-bill", billId] });
      void qc.invalidateQueries({ queryKey: ["inward-bills"] });
    },
    onError: (e: unknown) =>
      setActionErr(
        e instanceof ApiError
          ? Array.isArray((e as ApiError).message)
            ? (e as ApiError).message
            : String((e as ApiError).message)
          : "Approve failed",
      ),
  });

  const reject = useMutation({
    mutationFn: (reason: string) =>
      api(`/inward-bills/${billId}/reject`, { method: "POST", body: { reason } }),
    onSuccess: () => {
      void qc.invalidateQueries({ queryKey: ["inward-bill", billId] });
      void qc.invalidateQueries({ queryKey: ["inward-bills"] });
    },
  });

  const reExtract = useMutation({
    mutationFn: () => api(`/inward-bills/${billId}/re-extract`, { method: "POST" }),
    onSuccess: () => void qc.invalidateQueries({ queryKey: ["inward-bill", billId] }),
  });

  if (bill.isLoading) return <div className="p-6 text-sm text-muted">Loading…</div>;
  if (!bill.data) return <div className="p-6 text-sm text-danger">Not found.</div>;
  const b = bill.data;

  const recon = b.reconciliation;
  const reconciled = recon.reconciled === true;
  const isApproved = b.status === "approved";
  const isRejected = b.status === "rejected";

  const showBill = isDesktop || mobileTab === "bill";
  const showReview = isDesktop || mobileTab === "review";

  const pdfPane = (
    <div className="flex flex-1 flex-col border-line bg-[#d9d4ca] p-4 md:border-r">
      {isDesktop ? (
        <embed
          src={`/api/inward-bills/${billId}/pdf#toolbar=1`}
          type="application/pdf"
          className="h-[calc(100%-1.5rem)] w-full rounded border border-[#b8b1a2] bg-white"
        />
      ) : (
        <div className="rounded border border-[#b8b1a2] bg-white p-4 text-center text-sm">
          <div className="mb-3 text-4xl">📄</div>
          <div className="mb-3 break-all text-xs text-muted">{b.source_filename}</div>
          <a
            className="btn-primary"
            href={`/api/inward-bills/${billId}/pdf`}
            target="_blank"
            rel="noopener noreferrer"
            onClick={(e) => {
              e.preventDefault();
              void openPdf(billId);
            }}
          >
            Open bill PDF
          </a>
        </div>
      )}
      <p className="mt-2 text-center text-[11px] text-muted md:block">{b.source_filename}</p>
    </div>
  );

  const reviewPane = (
    <div className="flex flex-1 flex-col gap-3 overflow-y-auto p-4">
      {/* action bar */}
      <div className="sticky top-0 z-10 -mx-4 flex flex-col gap-2 border-b border-line bg-card/95 px-4 pb-2 backdrop-blur md:static md:mx-0 md:flex-row md:items-center md:justify-between md:border-0 md:bg-transparent md:px-0 md:pb-0 md:backdrop-blur-none">
        <div className="text-sm">
          <span className="font-mono font-semibold">{b.bill_no ?? "—"}</span>
          <span className="ml-2 text-xs text-muted">{b.status}</span>
        </div>
        <div className="grid grid-cols-3 gap-2 md:flex">
          {!isApproved && !isRejected && (
            <>
              <button
                className="btn-ghost h-9 px-3 text-xs"
                onClick={() => reExtract.mutate()}
                disabled={reExtract.isPending}
              >
                Re-extract
              </button>
              <button
                className="btn-ghost h-9 px-3 text-xs"
                onClick={() => {
                  const r = window.prompt("Reject reason?");
                  if (r) reject.mutate(r);
                }}
              >
                Reject
              </button>
              <button
                className="btn-primary h-9 px-3 text-xs"
                disabled={b.approve_blockers.length > 0 || approve.isPending}
                onClick={() => approve.mutate()}
                title={b.approve_blockers.join("; ")}
              >
                {b.approve_blockers.length
                  ? `Approve — ${b.approve_blockers.length} blocker${
                      b.approve_blockers.length > 1 ? "s" : ""
                    }`
                  : "Approve"}
              </button>
            </>
          )}
          {isApproved && (
            <a
              className="btn-primary col-span-3 h-9 px-3 text-xs"
              href={`/api/inward-bills/${billId}/xml?token=${getToken()}`}
              onClick={(e) => {
                e.preventDefault();
                void downloadXml(billId, b.bill_no ?? billId);
              }}
            >
              Download Tally XML
            </a>
          )}
        </div>
      </div>

      {actionErr && <p className="err">{actionErr}</p>}
      {b.error_message && (
        <p className="rounded-md bg-[#f4e3df] px-3 py-2 text-xs text-danger">
          {b.error_message}
        </p>
      )}
      {isRejected && (
        <p className="rounded-md bg-[#f4e3df] px-3 py-2 text-xs text-danger">
          Rejected: {b.reject_reason}
        </p>
      )}

      {/* supplier block */}
      <div className="card p-3">
        <div className="mb-2 flex items-center justify-between">
          <b className="text-[13px]">Supplier</b>
          {b.supplier.matched_party_id ? (
            <span className="rounded-full bg-[#e6efe8] px-2 py-0.5 text-[10px] text-ok">
              ✓ matched
            </span>
          ) : (
            <span className="rounded-full bg-[#efe9df] px-2 py-0.5 text-[10px] text-muted">
              NEW — will be created
            </span>
          )}
        </div>
        <div className="grid grid-cols-1 gap-2 text-xs sm:grid-cols-2 lg:grid-cols-3">
          <div>
            <div className="label">Party</div>
            <div className="field flex h-8 items-center text-xs">
              {b.supplier.matched_party_name ??
                (b.supplier.staged?.legal_name as string) ??
                "—"}
            </div>
          </div>
          <div>
            <div className="label">GSTIN</div>
            <div className="field flex h-8 items-center font-mono text-[11px]">
              {(b.supplier.staged?.gstin as string) ?? "—"}
            </div>
          </div>
          <div>
            <div className="label">Phone</div>
            <div className="field flex h-8 items-center font-mono text-[11px]">
              {(b.supplier.staged?.phone as string) ?? "—"}
            </div>
          </div>
          <div className="sm:col-span-2">
            <div className="label">Address</div>
            <div className="field flex h-8 items-center truncate text-[11px]">
              {supplierAddress(b.supplier.staged) ?? "—"}
            </div>
          </div>
          <div>
            <div className="label">Supply</div>
            <div className="field flex h-8 items-center text-xs">
              {b.supplier.supply_type ?? "—"} · POS{" "}
              {b.supplier.place_of_supply_state_code ?? "—"}
            </div>
          </div>
        </div>
        {!b.supplier.matched_party_id && (
          <p className="mt-2 text-[11px] text-muted">
            A new supplier party is staged (name · GSTIN · PAN · phone · one
            address). Created only on Approve.
          </p>
        )}
      </div>

      {/* totals check */}
      <div
        className={`rounded-xl border p-3 text-xs ${
          reconciled ? "border-ok/40 bg-[#e6efe8]" : "border-danger/40 bg-[#f4e3df]"
        }`}
      >
        <div className="flex flex-wrap items-baseline gap-x-4 gap-y-1">
          <b className={reconciled ? "text-ok" : "text-danger"}>
            {reconciled ? "Totals reconcile ✓" : "Totals do NOT reconcile"}
          </b>
          <span className="font-mono">
            {money(recon.taxable_total)} + CGST {money(recon.cgst_total)} + SGST{" "}
            {money(recon.sgst_total)}
            {recon.igst_total && recon.igst_total !== "None"
              ? ` + IGST ${money(recon.igst_total)}`
              : ""}{" "}
            + RO {money(recon.round_off)} = {money(recon.grand_total)}
          </span>
          {!reconciled && recon.discrepancy && (
            <span className="text-danger">off by {recon.discrepancy}</span>
          )}
        </div>
      </div>

      {/* lines — mobile cards */}
      <div className="flex flex-col gap-2 md:hidden">
        {b.lines.map((ln) => (
          <div
            key={ln.id}
            className={`rounded-lg border border-line p-3 text-xs ${
              ln.review_flag ? "bg-[#faf4ec]" : "bg-card"
            }`}
          >
            <div className="flex items-start gap-2">
              <span className="text-muted">{ln.sl_no}</span>
              <span className="flex-1 font-medium">{ln.description}</span>
            </div>
            <div className="mt-1 flex flex-wrap gap-x-3 gap-y-0.5 text-[11px] text-muted">
              <span className="font-mono">HSN {ln.hsn ?? "—"}</span>
              <span>
                {ln.quantity ?? "—"} {ln.uom ?? ""}
              </span>
              <span className="font-mono">@ {money(ln.unit_rate)}</span>
            </div>
            <div className="mt-1.5 flex items-center justify-between gap-2">
              <span className="font-mono font-semibold">{money(ln.line_total)}</span>
              <span className="flex items-center">
                {ln.matched_item_id ? (
                  <span className="rounded-full bg-[#e6efe8] px-2 py-0.5 text-[10px] text-ok">
                    {ln.match_method}
                  </span>
                ) : (
                  <span className="rounded-full bg-[#efe9df] px-2 py-0.5 text-[10px] text-muted">
                    NEW
                  </span>
                )}
                {flagChip(ln.review_flag)}
              </span>
            </div>
          </div>
        ))}
      </div>

      {/* lines — desktop dense table */}
      <div className="hidden overflow-hidden rounded-xl border border-line md:block">
        <div className="grid grid-cols-[24px_1fr_72px_44px_44px_70px_84px_150px] gap-2 bg-[#efe9df] px-3 py-1.5 text-[9px] font-semibold uppercase tracking-wide text-muted">
          <span>#</span>
          <span>Description</span>
          <span>HSN</span>
          <span>Qty</span>
          <span>UOM</span>
          <span>Rate</span>
          <span className="text-right">Amount</span>
          <span>Catalogue match</span>
        </div>
        {b.lines.map((ln) => (
          <div
            key={ln.id}
            className={`grid grid-cols-[24px_1fr_72px_44px_44px_70px_84px_150px] items-center gap-2 border-t border-line px-3 py-1.5 text-[11px] ${
              ln.review_flag ? "bg-[#faf4ec]" : ""
            }`}
          >
            <span className="text-muted">{ln.sl_no}</span>
            <span className="truncate">{ln.description}</span>
            <span className="font-mono text-[10px] text-muted">{ln.hsn ?? "—"}</span>
            <span>{ln.quantity ?? "—"}</span>
            <span>{ln.uom ?? "—"}</span>
            <span className="font-mono">{money(ln.unit_rate)}</span>
            <span className="text-right font-mono">{money(ln.line_total)}</span>
            <span className="flex items-center truncate">
              {ln.matched_item_id ? (
                <span className="rounded-full bg-[#e6efe8] px-2 py-0.5 text-[10px] text-ok">
                  {ln.match_method}
                </span>
              ) : (
                <span className="rounded-full bg-[#efe9df] px-2 py-0.5 text-[10px] text-muted">
                  NEW
                </span>
              )}
              {flagChip(ln.review_flag)}
            </span>
          </div>
        ))}
      </div>

      {b.approve_blockers.length > 0 && (
        <ul className="rounded-lg border border-dashed border-warn/50 bg-[#faf4ec] p-3 text-[11px] text-warn">
          {b.approve_blockers.map((r) => (
            <li key={r}>• {r}</li>
          ))}
        </ul>
      )}
    </div>
  );

  return (
    <div className="flex h-full flex-col md:grid md:grid-cols-[minmax(320px,420px)_1fr]">
      {/* mobile pane toggle */}
      <div className="flex gap-1 border-b border-line p-2 md:hidden">
        {(["bill", "review"] as const).map((t) => (
          <button
            key={t}
            onClick={() => setMobileTab(t)}
            className={`flex-1 rounded-md py-2 text-xs font-medium capitalize ${
              mobileTab === t ? "bg-ink text-ground" : "bg-ground text-muted"
            }`}
          >
            {t === "bill" ? "Bill PDF" : "Review"}
          </button>
        ))}
      </div>

      {showBill && pdfPane}
      {showReview && reviewPane}
    </div>
  );
}

async function openPdf(billId: string) {
  const res = await fetch(`/api/inward-bills/${billId}/pdf`, {
    headers: { Authorization: `Bearer ${getToken()}` },
  });
  if (!res.ok) return;
  const blob = await res.blob();
  const url = URL.createObjectURL(blob);
  window.open(url, "_blank", "noopener,noreferrer");
  // Revoke after a delay so the new tab has time to load it.
  setTimeout(() => URL.revokeObjectURL(url), 60_000);
}

async function downloadXml(billId: string, name: string) {
  const res = await fetch(`/api/inward-bills/${billId}/xml`, {
    headers: { Authorization: `Bearer ${getToken()}` },
  });
  if (!res.ok) return;
  const blob = await res.blob();
  const url = URL.createObjectURL(blob);
  const a = document.createElement("a");
  a.href = url;
  a.download = `inward-${name}.xml`;
  a.click();
  URL.revokeObjectURL(url);
}
