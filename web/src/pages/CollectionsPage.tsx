import { useMemo, useState } from "react";
import { useQuery } from "@tanstack/react-query";
import { api } from "../lib/api";
import { inr } from "../lib/previewTotal";
import { useDebounced } from "../lib/useDebounced";
import { PaymentDialog } from "../components/PaymentDialog";
import type { CollectionsRow } from "../lib/types";

type Sort = "balance" | "oldest";
type Scope = "outstanding" | "overpaid" | "either";

const SCOPES: { key: Scope; label: string }[] = [
  { key: "outstanding", label: "Owes us" },
  { key: "overpaid", label: "Overpaid" },
  { key: "either", label: "Either" },
];

function ageClass(days: number | null): string {
  if (days == null) return "text-muted";
  if (days >= 30) return "text-danger";
  if (days >= 14) return "text-warn";
  return "text-muted";
}

function initials(name: string): string {
  const parts = name.trim().split(/\s+/).filter(Boolean);
  return (parts[0]?.[0] ?? "").toUpperCase() + (parts[1]?.[0] ?? "").toUpperCase();
}

export function CollectionsPage() {
  const [q, setQ] = useState("");
  const dq = useDebounced(q.trim(), 250);
  const [sort, setSort] = useState<Sort>("balance");
  const [scope, setScope] = useState<Scope>("outstanding");
  const [payingFor, setPayingFor] = useState<CollectionsRow | null>(null);

  const list = useQuery({
    queryKey: ["collections", dq, sort, scope],
    queryFn: () => {
      const p = new URLSearchParams({ sort, scope });
      if (dq) p.set("q", dq);
      return api<CollectionsRow[]>(`/collections?${p.toString()}`);
    },
  });

  const rows = list.data ?? [];
  const totals = useMemo(
    () => ({
      // net across whatever's showing — a mixed "Either" view nets out,
      // which is the honest total, not a sum of absolute values
      net: rows.reduce((s, r) => s + Number(r.outstanding_balance), 0),
      count: rows.length,
    }),
    [list.data],
  );
  const netLabel = totals.net < 0 ? "Net credit owed" : "Outstanding";

  return (
    <div className="mx-auto flex max-w-2xl flex-col gap-4">
      <h1 className="font-serif text-lg font-semibold">Collections</h1>

      <div className="grid grid-cols-2 gap-2.5">
        <div className="card p-3">
          <div className="label mb-0.5">{netLabel}</div>
          <div
            className={`font-serif text-lg font-semibold ${totals.net < 0 ? "text-ok" : ""}`}
          >
            {inr(Math.abs(totals.net))}
          </div>
        </div>
        <div className="card p-3">
          <div className="label mb-0.5">Parties</div>
          <div className="font-serif text-lg font-semibold">{totals.count}</div>
        </div>
      </div>

      <div className="flex flex-wrap gap-1.5">
        {SCOPES.map((s) => (
          <button
            key={s.key}
            onClick={() => setScope(s.key)}
            className={`rounded-full border px-3 py-1 text-xs ${
              scope === s.key
                ? "border-ink bg-ink text-ground"
                : "border-line bg-card text-muted hover:bg-ground"
            }`}
          >
            {s.label}
          </button>
        ))}
      </div>

      <div className="flex items-center gap-2">
        <input
          className="field flex-1"
          placeholder="Search a party…"
          value={q}
          onChange={(e) => setQ(e.target.value)}
        />
        <div className="flex overflow-hidden rounded-md border border-line">
          <button
            className={`px-2.5 py-2 text-[11px] font-semibold ${
              sort === "balance" ? "bg-ink text-ground" : "bg-card text-muted"
            }`}
            onClick={() => setSort("balance")}
          >
            ₹ Balance
          </button>
          <button
            className={`px-2.5 py-2 text-[11px] font-semibold ${
              sort === "oldest" ? "bg-ink text-ground" : "bg-card text-muted"
            }`}
            onClick={() => setSort("oldest")}
          >
            Oldest
          </button>
        </div>
      </div>

      <div className="card overflow-hidden">
        {list.isLoading && <div className="px-3 py-6 text-center text-xs text-muted">Loading…</div>}
        {!list.isLoading && rows.length === 0 && (
          <div className="px-3 py-8 text-center text-xs text-muted">
            {dq
              ? "No matches."
              : scope === "overpaid"
                ? "No party is currently overpaid."
                : "Nobody owes you anything right now."}
          </div>
        )}
        {rows.map((r) => {
          const balance = Number(r.outstanding_balance);
          const isCredit = balance < 0;
          return (
            <button
              key={r.party_id}
              className="flex w-full items-center gap-2.5 border-b border-[#f3eee4] px-3.5 py-3 text-left last:border-b-0 hover:bg-accent-soft"
              onClick={() => setPayingFor(r)}
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
                <span
                  className={`block font-serif text-sm font-semibold ${isCredit ? "text-ok" : ""}`}
                >
                  {inr(Math.abs(balance))}
                </span>
                {isCredit ? (
                  <span className="block text-[10px] text-ok">credit</span>
                ) : (
                  r.oldest_unpaid_days != null && (
                    <span className={`block text-[10px] ${ageClass(r.oldest_unpaid_days)}`}>
                      oldest {r.oldest_unpaid_days}d
                    </span>
                  )
                )}
              </span>
              <span className="flex-none text-xs text-muted">›</span>
            </button>
          );
        })}
      </div>

      <p className="text-[11px] leading-snug text-muted">
        {scope === "outstanding"
          ? "Only parties who owe you money appear here — not the full Parties list. A party drops off once fully paid."
          : scope === "overpaid"
            ? "Parties with an unapplied credit — they've paid more than they currently owe."
            : "Every party with a non-zero balance in either direction."}
      </p>

      {payingFor && (
        <PaymentDialog
          partyId={payingFor.party_id}
          partyName={payingFor.legal_name}
          // a negative balance is a credit, not something to label
          // "outstanding" in the dialog's subheading — omit it there
          outstandingBalance={
            Number(payingFor.outstanding_balance) > 0 ? payingFor.outstanding_balance : null
          }
          onClose={() => setPayingFor(null)}
          onSaved={() => {
            setPayingFor(null);
            list.refetch();
          }}
        />
      )}
    </div>
  );
}
