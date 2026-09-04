import { useMemo, useState } from "react";
import { useQuery } from "@tanstack/react-query";
import { api } from "../lib/api";
import { inr } from "../lib/previewTotal";
import { useDebounced } from "../lib/useDebounced";
import { PaymentDialog } from "../components/PaymentDialog";
import type { CollectionsRow } from "../lib/types";

type Sort = "balance" | "oldest";

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
  const [payingFor, setPayingFor] = useState<CollectionsRow | null>(null);

  const list = useQuery({
    queryKey: ["collections", dq, sort],
    queryFn: () => {
      const p = new URLSearchParams({ sort });
      if (dq) p.set("q", dq);
      return api<CollectionsRow[]>(`/collections?${p.toString()}`);
    },
  });

  const rows = list.data ?? [];
  const totals = useMemo(
    () => ({
      outstanding: rows.reduce((s, r) => s + Number(r.outstanding_balance), 0),
      count: rows.length,
    }),
    [list.data],
  );

  return (
    <div className="mx-auto flex max-w-2xl flex-col gap-4">
      <h1 className="font-serif text-lg font-semibold">Collections</h1>

      <div className="grid grid-cols-2 gap-2.5">
        <div className="card p-3">
          <div className="label mb-0.5">Outstanding</div>
          <div className="font-serif text-lg font-semibold">{inr(totals.outstanding)}</div>
        </div>
        <div className="card p-3">
          <div className="label mb-0.5">Parties owing</div>
          <div className="font-serif text-lg font-semibold">{totals.count}</div>
        </div>
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
            {dq ? "No matches." : "Nobody owes you anything right now."}
          </div>
        )}
        {rows.map((r) => (
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
                {r.phone ?? "—"} · {r.open_invoice_count} open bill
                {r.open_invoice_count === 1 ? "" : "s"}
              </span>
            </span>
            <span className="flex-none text-right">
              <span className="block font-serif text-sm font-semibold">
                {inr(r.outstanding_balance)}
              </span>
              {r.oldest_unpaid_days != null && (
                <span className={`block text-[10px] ${ageClass(r.oldest_unpaid_days)}`}>
                  oldest {r.oldest_unpaid_days}d
                </span>
              )}
            </span>
            <span className="flex-none text-xs text-muted">›</span>
          </button>
        ))}
      </div>

      <p className="text-[11px] leading-snug text-muted">
        Only parties with an outstanding balance appear here — not the full Parties list. A
        party drops off once fully paid.
      </p>

      {payingFor && (
        <PaymentDialog
          partyId={payingFor.party_id}
          partyName={payingFor.legal_name}
          outstandingBalance={payingFor.outstanding_balance}
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
