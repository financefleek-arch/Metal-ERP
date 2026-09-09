import { useMemo, useState } from "react";
import { useQuery } from "@tanstack/react-query";
import { api } from "../lib/api";
import { inr } from "../lib/previewTotal";
import { useDebounced } from "../lib/useDebounced";
import { PaymentDialog } from "../components/PaymentDialog";
import { StatementSendDialog } from "../components/StatementSendDialog";
import type { AgeingBucket, AgeingRow, CollectionsRow } from "../lib/types";

/** Scope chips are radio-exclusive. "owes"/"overdue" read the ageing
 *  endpoint (money owed to us, bucketed); "overpaid"/"either" fall back to
 *  the plain collections list (ageing is meaningless for a credit balance). */
type Scope = "owes" | "overdue" | "overpaid" | "either";

const SCOPES: { key: Scope; label: string }[] = [
  { key: "owes", label: "Owes us" },
  { key: "overdue", label: "Overdue" },
  { key: "overpaid", label: "Overpaid" },
  { key: "either", label: "Either" },
];

const BUCKETS: { key: AgeingBucket; label: string }[] = [
  { key: "lt30", label: "<30 days" },
  { key: "d30", label: "30+" },
  { key: "d60", label: "60+" },
  { key: "d90p", label: ">3 months" },
];

const BUCKET_PILL: Record<AgeingBucket, { cls: string; label: string }> = {
  lt30: { cls: "bg-[#eee9df] text-muted", label: "<30d" },
  d30: { cls: "bg-[#f1e7d6] text-warn", label: "30+" },
  d60: { cls: "bg-[#f6e7dd] text-[#b5622f]", label: "60+" },
  d90p: { cls: "bg-[#f6dcd6] text-danger", label: ">3 mo" },
};

function initials(name: string): string {
  const parts = name.trim().split(/\s+/).filter(Boolean);
  return (parts[0]?.[0] ?? "").toUpperCase() + (parts[1]?.[0] ?? "").toUpperCase();
}

export function CollectionsPage() {
  const [q, setQ] = useState("");
  const dq = useDebounced(q.trim(), 250);
  const [scope, setScope] = useState<Scope>("owes");
  const [bucket, setBucket] = useState<AgeingBucket | null>(null);
  const [payingFor, setPayingFor] = useState<{ party_id: string; legal_name: string; balance: string } | null>(null);
  const [statementFor, setStatementFor] = useState<{ party_id: string; legal_name: string; phone: string | null } | null>(null);

  const ageingView = scope === "owes" || scope === "overdue";

  const ageing = useQuery({
    queryKey: ["collections-ageing", dq],
    queryFn: () => {
      const p = new URLSearchParams();
      if (dq) p.set("q", dq);
      return api<AgeingRow[]>(`/collections/ageing?${p.toString()}`);
    },
    enabled: ageingView,
  });

  const legacy = useQuery({
    queryKey: ["collections", dq, scope],
    queryFn: () => {
      const p = new URLSearchParams({ scope, sort: "balance" });
      if (dq) p.set("q", dq);
      return api<CollectionsRow[]>(`/collections?${p.toString()}`);
    },
    enabled: !ageingView,
  });

  const ageingRows = useMemo(() => ageing.data ?? [], [ageing.data]);

  const bucketTotals = useMemo(() => {
    const t: Record<AgeingBucket, number> = { lt30: 0, d30: 0, d60: 0, d90p: 0 };
    for (const r of ageingRows) {
      t.lt30 += Number(r.lt30);
      t.d30 += Number(r.d30);
      t.d60 += Number(r.d60);
      t.d90p += Number(r.d90p);
    }
    return t;
  }, [ageingRows]);

  const grandTotal = bucketTotals.lt30 + bucketTotals.d30 + bucketTotals.d60 + bucketTotals.d90p;

  const visibleAgeing = useMemo(() => {
    let rows = ageingRows;
    if (scope === "overdue") rows = rows.filter((r) => r.is_overdue);
    if (bucket) rows = rows.filter((r) => Number(r[bucket]) > 0);
    return rows;
  }, [ageingRows, scope, bucket]);

  const overdueCount = ageingRows.filter((r) => r.is_overdue).length;
  const overdueTotal = ageingRows.filter((r) => r.is_overdue).reduce((s, r) => s + Number(r.total), 0);

  function pickScope(next: Scope) {
    setScope(next);
    setBucket(null);
  }

  return (
    <div className="mx-auto flex max-w-2xl flex-col gap-4">
      <h1 className="font-serif text-lg font-semibold">Collections</h1>

      <div className="flex flex-wrap gap-1.5">
        {SCOPES.map((s) => (
          <button
            key={s.key}
            onClick={() => pickScope(s.key)}
            className={`rounded-full border px-3 py-1 text-xs ${
              scope === s.key
                ? s.key === "overdue"
                  ? "border-danger bg-danger text-white"
                  : "border-ink bg-ink text-ground"
                : "border-line bg-card text-muted hover:bg-ground"
            }`}
          >
            {s.label}
          </button>
        ))}
      </div>

      {ageingView && (
        <>
          <div className="grid grid-cols-4 gap-1.5">
            {BUCKETS.map((b) => {
              const on = bucket === b.key;
              return (
                <button
                  key={b.key}
                  onClick={() => setBucket(on ? null : b.key)}
                  className={`rounded-lg border px-1.5 py-2 text-center ${
                    on ? "border-accent ring-2 ring-inset ring-accent-soft" : "border-line"
                  } ${b.key === "d90p" ? "bg-[#fdf3f1]" : "bg-card"}`}
                >
                  <div className="label text-[9px]">{b.label}</div>
                  <div className="mt-0.5 font-mono text-xs font-semibold">
                    {inr(bucketTotals[b.key])}
                  </div>
                </button>
              );
            })}
          </div>
          <p className="-mt-2 text-[11px] text-muted">
            {scope === "overdue" ? (
              <>
                Overdue: <b>{overdueCount}</b> {overdueCount === 1 ? "party" : "parties"} ·{" "}
                {inr(overdueTotal)}
              </>
            ) : (
              <>
                Total outstanding <b>{inr(grandTotal)}</b> across {ageingRows.length}{" "}
                {ageingRows.length === 1 ? "party" : "parties"}
              </>
            )}
            {bucket && ` · showing ${BUCKETS.find((b) => b.key === bucket)?.label}`}
          </p>
        </>
      )}

      <input
        className="field"
        placeholder="Search a party…"
        value={q}
        onChange={(e) => setQ(e.target.value)}
      />

      <div className="card overflow-hidden">
        {ageingView ? (
          <AgeingList
            rows={visibleAgeing}
            loading={ageing.isLoading}
            emptyText={
              dq
                ? "No matches."
                : scope === "overdue"
                  ? "Nobody is more than 30 days overdue."
                  : "Nobody owes you anything right now."
            }
            onPay={(r) => setPayingFor({ party_id: r.party_id, legal_name: r.legal_name, balance: r.total })}
            onStatement={(r) =>
              setStatementFor({ party_id: r.party_id, legal_name: r.legal_name, phone: r.phone })
            }
          />
        ) : (
          <LegacyList
            rows={legacy.data ?? []}
            loading={legacy.isLoading}
            emptyText={
              dq
                ? "No matches."
                : scope === "overpaid"
                  ? "No party is currently overpaid."
                  : "No party has a non-zero balance."
            }
            onPay={(r) =>
              setPayingFor({
                party_id: r.party_id,
                legal_name: r.legal_name,
                balance: r.outstanding_balance,
              })
            }
          />
        )}
      </div>

      <p className="text-[11px] leading-snug text-muted">
        {scope === "owes"
          ? "Parties who owe you money, aged by each bill's due date. A party drops off once fully paid."
          : scope === "overdue"
            ? "Parties whose oldest unpaid balance is more than 30 days past due — chase these first."
            : scope === "overpaid"
              ? "Parties with an unapplied credit — they've paid more than they currently owe."
              : "Every party with a non-zero balance in either direction."}
      </p>

      {payingFor && (
        <PaymentDialog
          partyId={payingFor.party_id}
          partyName={payingFor.legal_name}
          outstandingBalance={Number(payingFor.balance) > 0 ? payingFor.balance : null}
          onClose={() => setPayingFor(null)}
          onSaved={() => {
            setPayingFor(null);
            ageing.refetch();
            legacy.refetch();
          }}
        />
      )}

      {statementFor && (
        <StatementSendDialog
          partyId={statementFor.party_id}
          partyName={statementFor.legal_name}
          partyPhone={statementFor.phone}
          onClose={() => setStatementFor(null)}
        />
      )}
    </div>
  );
}

function AgeingList({
  rows,
  loading,
  emptyText,
  onPay,
  onStatement,
}: {
  rows: AgeingRow[];
  loading: boolean;
  emptyText: string;
  onPay: (r: AgeingRow) => void;
  onStatement: (r: AgeingRow) => void;
}) {
  if (loading) return <div className="px-3 py-6 text-center text-xs text-muted">Loading…</div>;
  if (rows.length === 0)
    return <div className="px-3 py-8 text-center text-xs text-muted">{emptyText}</div>;
  return (
    <>
      {rows.map((r) => {
        const pill = BUCKET_PILL[r.worst_bucket];
        return (
          <div
            key={r.party_id}
            className="flex w-full items-center gap-2.5 border-b border-[#f3eee4] px-3.5 py-3 last:border-b-0"
          >
            <span className="grid h-8.5 w-8.5 flex-none place-items-center rounded-full bg-accent-soft text-xs font-semibold text-accent">
              {initials(r.legal_name)}
            </span>
            <button className="min-w-0 flex-1 text-left" onClick={() => onPay(r)}>
              <span className="block truncate text-sm font-semibold">{r.legal_name}</span>
              <span className="block text-[11px] text-muted">
                {r.phone ?? "—"}
                {" · "}
                {r.open_invoice_count > 0
                  ? `${r.open_invoice_count} open bill${r.open_invoice_count === 1 ? "" : "s"}`
                  : "opening balance"}
                {r.oldest_bill_number != null && ` · oldest INV-${r.oldest_bill_number}`}
                {r.last_payment_date && ` · last paid ${r.last_payment_date}`}
              </span>
              {r.is_overdue && (
                <button
                  type="button"
                  className="mt-1 text-[10.5px] font-semibold text-accent underline"
                  onClick={(e) => {
                    e.stopPropagation();
                    onStatement(r);
                  }}
                >
                  Send statement
                </button>
              )}
            </button>
            <button className="flex-none text-right" onClick={() => onPay(r)}>
              <span className="block font-serif text-sm font-semibold">{inr(r.total)}</span>
              <span
                className={`mt-0.5 inline-block rounded-[3px] px-1.5 py-px text-[10px] font-bold uppercase tracking-wide ${pill.cls}`}
              >
                {pill.label}
              </span>
            </button>
          </div>
        );
      })}
    </>
  );
}

function LegacyList({
  rows,
  loading,
  emptyText,
  onPay,
}: {
  rows: CollectionsRow[];
  loading: boolean;
  emptyText: string;
  onPay: (r: CollectionsRow) => void;
}) {
  if (loading) return <div className="px-3 py-6 text-center text-xs text-muted">Loading…</div>;
  if (rows.length === 0)
    return <div className="px-3 py-8 text-center text-xs text-muted">{emptyText}</div>;
  return (
    <>
      {rows.map((r) => {
        const balance = Number(r.outstanding_balance);
        const isCredit = balance < 0;
        return (
          <button
            key={r.party_id}
            className="flex w-full items-center gap-2.5 border-b border-[#f3eee4] px-3.5 py-3 text-left last:border-b-0 hover:bg-accent-soft"
            onClick={() => onPay(r)}
          >
            <span className="grid h-8.5 w-8.5 flex-none place-items-center rounded-full bg-accent-soft text-xs font-semibold text-accent">
              {initials(r.legal_name)}
            </span>
            <span className="min-w-0 flex-1">
              <span className="block truncate text-sm font-semibold">{r.legal_name}</span>
              <span className="block text-[11px] text-muted">
                {r.phone ?? "—"} ·{" "}
                {r.open_invoice_count > 0
                  ? `${r.open_invoice_count} open bill${r.open_invoice_count === 1 ? "" : "s"}`
                  : "no open bills"}
              </span>
            </span>
            <span className="flex-none text-right">
              <span className={`block font-serif text-sm font-semibold ${isCredit ? "text-ok" : ""}`}>
                {inr(Math.abs(balance))}
              </span>
              {isCredit && <span className="block text-[10px] text-ok">credit</span>}
            </span>
            <span className="flex-none text-xs text-muted">›</span>
          </button>
        );
      })}
    </>
  );
}
