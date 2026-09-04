import { useMemo, useState } from "react";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { api, ApiError } from "../lib/api";
import { inr } from "../lib/previewTotal";
import type {
  OpenInvoiceForAllocation,
  PaymentCreate,
  PaymentMode,
  PaymentOut,
} from "../lib/types";

const MODES: { key: PaymentMode; label: string }[] = [
  { key: "cash", label: "Cash" },
  { key: "upi", label: "UPI" },
  { key: "bank", label: "Bank" },
  { key: "cheque", label: "Cheque" },
];

function round2(n: number): number {
  return Math.round((n + Number.EPSILON) * 100) / 100;
}

function parseAmt(v: string): number {
  const n = parseFloat(v.replace(/,/g, ""));
  return isFinite(n) && n > 0 ? n : 0;
}

/** one row of the allocation table — string-typed so partial input never NaNs */
interface AllocRow {
  invoiceId: string;
  number: number;
  date: string;
  balanceDue: number;
  daysOld: number;
  apply: string;
  /** true once the operator has hand-edited this row — stops FIFO re-fill from overwriting it */
  edited: boolean;
}

/** Derive the allocation table from the server's open-invoice list, the
 *  typed amount, and any rows the operator has hand-edited — pure, no
 *  stored/re-seeded state, so a query refetch or a keystroke never race
 *  each other into a flicker. */
function buildRows(
  invoices: OpenInvoiceForAllocation[],
  amount: number,
  edits: Record<string, string>,
): AllocRow[] {
  let remaining = amount;
  return invoices.map((iv) => {
    const balanceDue = Number(iv.balance_due);
    const edited = Object.prototype.hasOwnProperty.call(edits, iv.invoice_id);
    let apply: string;
    if (edited) {
      apply = edits[iv.invoice_id];
      remaining -= parseAmt(apply);
    } else {
      const fill = Math.max(0, Math.min(balanceDue, remaining));
      remaining -= fill;
      apply = fill > 0 ? String(round2(fill)) : "0";
    }
    return {
      invoiceId: iv.invoice_id,
      number: iv.number,
      date: iv.date,
      balanceDue,
      daysOld: iv.days_old,
      apply,
      edited,
    };
  });
}

/**
 * Shared "Record payment" dialog — the ONE component opened from three
 * places (Collections row, Party Account tab, Invoice balance strip), each
 * just pre-scoping props. Never navigates away; the caller's screen updates
 * in place via query invalidation once saved.
 */
export function PaymentDialog({
  partyId,
  partyName,
  outstandingBalance,
  /** preselect/pin this invoice's row when opened from an invoice page */
  focusInvoiceId,
  onClose,
  onSaved,
}: {
  partyId: string;
  partyName: string;
  outstandingBalance?: string | null;
  focusInvoiceId?: string;
  onClose: () => void;
  onSaved: (p: PaymentOut) => void;
}) {
  const qc = useQueryClient();
  const [date, setDate] = useState(() => new Date().toISOString().slice(0, 10));
  const [amount, setAmount] = useState("");
  const [mode, setMode] = useState<PaymentMode>("cash");
  const [refNo, setRefNo] = useState("");
  const [notes, setNotes] = useState("");
  /** rows the operator has hand-edited, by invoice id — everything else is FIFO-derived */
  const [edits, setEdits] = useState<Record<string, string>>({});
  const [err, setErr] = useState<string | null>(null);

  const openInvoices = useQuery({
    queryKey: ["open-invoices", partyId],
    queryFn: () => api<OpenInvoiceForAllocation[]>(`/parties/${partyId}/open-invoices`),
  });

  const amountNum = parseAmt(amount);

  const rows = useMemo(
    () => buildRows(openInvoices.data ?? [], amountNum, edits),
    [openInvoices.data, amountNum, edits],
  );

  // total across every open invoice — what "Pay in full" fills in, so the
  // operator never has to type back a number already on screen
  const totalOutstanding = useMemo(
    () =>
      round2((openInvoices.data ?? []).reduce((s, iv) => s + Number(iv.balance_due), 0)),
    [openInvoices.data],
  );

  function payInFull() {
    setEdits({});
    setAmount(totalOutstanding > 0 ? String(totalOutstanding) : "");
  }

  const allocatedTotal = useMemo(
    () => round2(rows.reduce((sum, r) => sum + parseAmt(r.apply), 0)),
    [rows],
  );
  const onAccount = round2(Math.max(0, amountNum - allocatedTotal));
  const overAllocated = allocatedTotal > amountNum + 0.005;

  function patchRow(invoiceId: string, apply: string) {
    setEdits((es) => ({ ...es, [invoiceId]: apply }));
  }

  const save = useMutation({
    mutationFn: () => {
      const body: PaymentCreate = {
        party_id: partyId,
        date,
        amount: String(amountNum),
        mode,
        ref_no: refNo.trim() || null,
        notes: notes.trim() || null,
        ledger_name: null,
        allocations: rows
          .filter((r) => parseAmt(r.apply) > 0)
          .map((r) => ({
            invoice_id: r.invoiceId,
            type: "against_invoice" as const,
            amount: String(parseAmt(r.apply)),
          })),
      };
      return api<PaymentOut>("/payments", { method: "POST", body });
    },
    onSuccess: (p) => {
      qc.invalidateQueries({ queryKey: ["collections"] });
      qc.invalidateQueries({ queryKey: ["party-ledger", partyId] });
      qc.invalidateQueries({ queryKey: ["open-invoices", partyId] });
      qc.invalidateQueries({ queryKey: ["invoice"] });
      qc.invalidateQueries({ queryKey: ["invoices"] });
      onSaved(p);
    },
    onError: (e) => setErr(e instanceof ApiError ? e.message : "Could not save payment"),
  });

  const canSave = amountNum > 0 && !overAllocated && !save.isPending;

  return (
    <div
      className="fixed inset-0 z-50 flex items-end justify-center bg-black/30 sm:items-center sm:p-4"
      onClick={onClose}
    >
      <div
        className="flex max-h-[92dvh] w-full flex-col overflow-hidden rounded-t-2xl border border-line bg-card sm:max-w-md sm:rounded-2xl"
        onClick={(e) => e.stopPropagation()}
      >
        <div className="flex items-center justify-between px-4 pb-1 pt-4">
          <h2 className="font-serif text-base font-semibold">Record payment</h2>
          <button
            className="grid h-7 w-7 place-items-center rounded-full bg-ground text-muted"
            onClick={onClose}
          >
            ×
          </button>
        </div>
        <div className="px-4 pb-2 text-xs text-muted">
          {partyName}
          {outstandingBalance != null && ` · ${inr(outstandingBalance)} outstanding`}
        </div>

        <div className="flex-1 overflow-y-auto px-4">
          {err && <p className="err">{err}</p>}

          <div className="mb-3 grid grid-cols-2 gap-2.5">
            <div>
              <label className="label">Date</label>
              <input
                type="date"
                className="field"
                value={date}
                onChange={(e) => setDate(e.target.value)}
              />
            </div>
            <div>
              <label className="label flex items-center justify-between">
                <span>Amount received</span>
                {totalOutstanding > 0 && (
                  <button
                    type="button"
                    className="normal-case tracking-normal text-accent hover:underline"
                    onClick={payInFull}
                  >
                    Pay in full
                  </button>
                )}
              </label>
              <input
                className="field text-right font-semibold"
                inputMode="decimal"
                placeholder="0.00"
                value={amount}
                onChange={(e) => setAmount(e.target.value)}
              />
            </div>
          </div>

          <div className="mb-3">
            <label className="label">Mode</label>
            <div className="grid grid-cols-4 overflow-hidden rounded-md border border-line">
              {MODES.map((m) => (
                <button
                  key={m.key}
                  type="button"
                  className={`border-r border-line py-2 text-xs font-semibold last:border-r-0 ${
                    mode === m.key ? "bg-accent text-white" : "bg-card text-muted"
                  }`}
                  onClick={() => setMode(m.key)}
                >
                  {m.label}
                </button>
              ))}
            </div>
          </div>

          <div className="mb-4 grid grid-cols-2 gap-2.5">
            <div>
              <label className="label">Ref / UTR no.</label>
              <input
                className="field"
                value={refNo}
                onChange={(e) => setRefNo(e.target.value)}
              />
            </div>
            <div>
              <label className="label">Notes (optional)</label>
              <input
                className="field"
                value={notes}
                onChange={(e) => setNotes(e.target.value)}
              />
            </div>
          </div>

          <div className="mb-1.5 flex items-baseline justify-between">
            <label className="label mb-0">Apply against open bills</label>
            <span className="text-[10px] font-semibold text-accent">oldest first, edit any row</span>
          </div>

          {openInvoices.isLoading && (
            <div className="py-4 text-center text-xs text-muted">Loading open bills…</div>
          )}
          {!openInvoices.isLoading && rows.length === 0 && (
            <div className="rounded-md border border-line bg-ground px-3 py-4 text-center text-xs text-muted">
              No open bills for this party.
            </div>
          )}
          {rows.length > 0 && (
            <div className="mb-3 overflow-hidden rounded-md border border-line">
              <div className="grid grid-cols-[1fr_74px_84px] gap-2 bg-ground px-3 py-1.5 text-[10px] font-semibold uppercase tracking-wide text-muted">
                <span>Invoice</span>
                <span className="text-right">Due</span>
                <span className="text-right">Apply ₹</span>
              </div>
              {rows.map((r) => (
                <div
                  key={r.invoiceId}
                  className={`grid grid-cols-[1fr_74px_84px] items-center gap-2 border-t border-[#f3eee4] px-3 py-2 ${
                    r.invoiceId === focusInvoiceId ? "bg-accent-soft" : ""
                  }`}
                >
                  <div>
                    <div className="text-sm font-semibold">#{r.number}</div>
                    <div className="text-[10px] text-muted">
                      {new Date(r.date).toLocaleDateString()} · {r.daysOld}d old
                    </div>
                  </div>
                  <div className="text-right text-xs text-muted">{inr(r.balanceDue)}</div>
                  <div>
                    <input
                      className={`field h-8 text-right text-xs ${r.edited ? "border-accent bg-accent-soft" : ""}`}
                      inputMode="decimal"
                      value={r.apply}
                      onChange={(e) => patchRow(r.invoiceId, e.target.value)}
                    />
                  </div>
                </div>
              ))}
            </div>
          )}

          <div className="mb-3 flex justify-between px-1 text-xs text-muted">
            <span>Allocated</span>
            <span>
              {inr(allocatedTotal)} of {inr(amountNum)}
            </span>
          </div>

          {overAllocated && (
            <p className="err mb-3">Allocated amount exceeds the payment amount.</p>
          )}

          {!overAllocated && onAccount > 0 && amountNum > 0 && (
            <div className="mb-4 rounded-md border border-dashed border-accent bg-accent-soft px-3.5 py-3">
              <div className="text-[11px] font-semibold uppercase tracking-wide text-accent">
                On account credit
              </div>
              <div className="mt-1 font-serif text-lg font-semibold text-accent">
                {inr(onAccount)}
              </div>
              <p className="mt-1.5 text-[11px] leading-snug text-ink/70">
                {rows.length === 0
                  ? "No open bills to apply this to."
                  : "Not applied to a specific bill."}{" "}
                Kept as a credit on the party and offered first against their next invoice.
              </p>
            </div>
          )}
        </div>

        <div className="border-t border-line px-4 py-3">
          <button
            className="btn-primary h-11 w-full text-sm"
            disabled={!canSave}
            onClick={() => save.mutate()}
          >
            {save.isPending ? "Saving…" : "Save payment"}
          </button>
        </div>
      </div>
    </div>
  );
}
