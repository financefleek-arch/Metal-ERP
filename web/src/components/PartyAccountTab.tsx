import { useState } from "react";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { api, ApiError } from "../lib/api";
import { inr } from "../lib/previewTotal";
import { PaymentDialog } from "./PaymentDialog";
import type { Party, PartyLedgerEntry } from "../lib/types";

/** Party detail page's "Account" tab — running statement + record-payment entry. */
export function PartyAccountTab({ party }: { party: Party }) {
  const qc = useQueryClient();
  const [payingOpen, setPayingOpen] = useState(false);
  const [openAlloc, setOpenAlloc] = useState<string | null>(null);
  const [err, setErr] = useState<string | null>(null);

  const ledger = useQuery({
    queryKey: ["party-ledger", party.id],
    queryFn: () => api<PartyLedgerEntry[]>(`/parties/${party.id}/ledger`),
  });

  const reverse = useMutation({
    mutationFn: ({ paymentId, reason }: { paymentId: string; reason: string }) =>
      api(`/payments/${paymentId}/reverse`, { method: "POST", body: { reason } }),
    onSuccess: () => {
      setErr(null);
      ledger.refetch();
      qc.invalidateQueries({ queryKey: ["collections"] });
      qc.invalidateQueries({ queryKey: ["open-invoices", party.id] });
      qc.invalidateQueries({ queryKey: ["invoice"] });
      qc.invalidateQueries({ queryKey: ["invoices"] });
    },
    onError: (e) => setErr(e instanceof ApiError ? e.message : "Could not reverse payment"),
  });

  function confirmReverse(paymentId: string, label: string) {
    const reason = window.prompt(
      `Reverse ${label}? This won't delete it — it stays on the statement as reversed, ` +
        `and its allocations stop counting toward any balance.\n\nReason (e.g. "cheque bounced", "wrong entry"):`,
    );
    if (reason === null) return; // cancelled
    if (!reason.trim()) {
      setErr("A reason is required to reverse a payment.");
      return;
    }
    reverse.mutate({ paymentId, reason: reason.trim() });
  }

  // entries are newest-first; the most recent row's running balance is the
  // current balance — negative means the party has an unapplied credit.
  const entries = ledger.data ?? [];
  const balance = Number(entries[0]?.running_balance ?? "0");
  const isCredit = balance < 0;

  return (
    <div>
      {err && <p className="err mb-3">{err}</p>}
      <div className="card p-4">
        <div className="label mb-0.5">{isCredit ? "Credit balance" : "Outstanding balance"}</div>
        <div className={`font-serif text-2xl font-semibold ${isCredit ? "text-ok" : ""}`}>
          {inr(Math.abs(balance))}
        </div>
        <button
          className="btn-primary mt-3 h-10 px-4 text-sm"
          onClick={() => setPayingOpen(true)}
        >
          + Record payment
        </button>
      </div>

      <div className="label mb-1.5 mt-5">Statement</div>
      <div className="card divide-y divide-[#f3eee4] overflow-hidden">
        {ledger.isLoading && (
          <div className="px-3.5 py-6 text-center text-xs text-muted">Loading…</div>
        )}
        {!ledger.isLoading && entries.length === 0 && (
          <div className="px-3.5 py-8 text-center text-xs text-muted">
            No invoices or payments yet.
          </div>
        )}
        {entries.map((e) => {
          const isPayment = e.kind === "payment";
          const isReversed = isPayment && e.status === "reversed";
          const hasAllocations = isPayment && (e.allocations?.length ?? 0) > 0;
          return (
            <div key={e.ref_id} className="px-3.5 py-3">
              <div className="flex items-start gap-2.5">
                <span
                  className={`mt-1.5 h-2 w-2 flex-none rounded-full ${
                    isReversed ? "bg-line" : isPayment ? "bg-ok" : "bg-warn"
                  }`}
                />
                <div className="min-w-0 flex-1">
                  <button
                    className={`block text-left text-sm font-semibold ${
                      isReversed ? "text-muted line-through" : ""
                    } ${hasAllocations ? "hover:underline" : ""}`}
                    onClick={() => hasAllocations && setOpenAlloc((k) => (k === e.ref_id ? null : e.ref_id))}
                  >
                    {e.ref_label}
                  </button>
                  <div className="text-[11px] text-muted">
                    {new Date(e.date).toLocaleDateString()}
                    {isReversed && " · reversed"}
                  </div>
                  {hasAllocations && openAlloc === e.ref_id && (
                    <div className="mt-2 rounded-md border border-line bg-ground px-2.5 py-2 text-[11px]">
                      {e.allocations!.map((a, i) => (
                        <div key={i} className="flex justify-between py-0.5">
                          <span>
                            {a.type === "on_account"
                              ? "On account (unapplied)"
                              : "Applied to an invoice"}
                          </span>
                          <span>{inr(a.amount)}</span>
                        </div>
                      ))}
                    </div>
                  )}
                </div>
                <div className="flex-none text-right">
                  <div
                    className={`text-sm font-semibold tabular-nums ${
                      isReversed ? "text-muted" : isPayment ? "text-ok" : "text-danger"
                    }`}
                  >
                    {isPayment ? "− " : "+ "}
                    {inr(isPayment ? e.credit : e.debit)}
                  </div>
                  <div className="text-[10px] text-muted">bal {inr(e.running_balance)}</div>
                  {isPayment && !isReversed && (
                    <button
                      type="button"
                      className="mt-1 text-[10px] font-semibold text-danger hover:underline disabled:opacity-50"
                      disabled={reverse.isPending}
                      onClick={() => confirmReverse(e.ref_id, e.ref_label)}
                    >
                      {reverse.isPending ? "Reversing…" : "Reverse"}
                    </button>
                  )}
                </div>
              </div>
            </div>
          );
        })}
      </div>

      {payingOpen && (
        <PaymentDialog
          partyId={party.id}
          partyName={party.legal_name}
          outstandingBalance={isCredit ? null : entries[0]?.running_balance}
          onClose={() => setPayingOpen(false)}
          onSaved={() => {
            setPayingOpen(false);
            ledger.refetch();
          }}
        />
      )}
    </div>
  );
}
