import { useState } from "react";
import { NavLink, useNavigate, useParams } from "react-router-dom";
import { useQuery } from "@tanstack/react-query";
import { api } from "../../lib/api";
import { useIsDesktop } from "../../lib/useIsDesktop";
import { OrderDetail } from "./OrderDetail";

export interface OrderLine {
  id: string;
  item_id: string | null;
  name: string;
  code: string | null;
  qty: number;
  pack_qty: number;
  rate: string;
  amount: string;
  available: boolean;
  current_rate: string | null;
}
export interface Order {
  id: string;
  number: string;
  status: "new" | "accepted" | "invoiced" | "rejected";
  customer_name: string;
  customer_phone: string;
  customer_firm: string | null;
  note: string | null;
  wa_opt_in: boolean;
  matched_party_id: string | null;
  matched_party_name: string | null;
  item_count: number;
  total: string;
  reject_reason: string | null;
  invoice_id: string | null;
  link_title: string | null;
  created_at: string;
  invoice_status: string | null;
  messages: { template: string; event: string | null; to_phone: string; status: string; error: string | null; sent_at: string | null }[];
  lines: OrderLine[];
}

const money = (v: string) => Number(v).toLocaleString("en-IN", { minimumFractionDigits: 0, maximumFractionDigits: 2 });
const TONE: Record<Order["status"], string> = {
  new: "bg-[#f1e7d6] text-warn",
  accepted: "bg-[#e6efe8] text-ok",
  invoiced: "bg-[#e6efe8] text-ok",
  rejected: "bg-[#f4e3df] text-danger",
};
const LABEL: Record<Order["status"], string> = {
  new: "New",
  accepted: "Accepted",
  invoiced: "Invoiced",
  rejected: "Rejected",
};
const FILTERS: [string, string][] = [
  ["", "All"],
  ["new", "New"],
  ["accepted", "Accepted"],
  ["invoiced", "Invoiced"],
  ["rejected", "Rejected"],
];

/** Orders customers placed from your catalog links: review, confirm the customer, make the invoice. */
export function OrdersPage() {
  const { id: selectedId } = useParams();
  const nav = useNavigate();
  const isDesktop = useIsDesktop();
  const [filter, setFilter] = useState("");
  // WhatsApp goes out just after Accept/Reject returns, so keep looking for a short while
  const [burstUntil, setBurstUntil] = useState(0);

  const list = useQuery({
    queryKey: ["orders", filter],
    queryFn: () => api<Order[]>(`/orders${filter ? `?status=${filter}` : ""}`),
    refetchInterval: 20_000,
  });
  const counts = useQuery({
    queryKey: ["order-counts"],
    queryFn: () => api<Record<string, number>>("/orders/counts"),
    refetchInterval: 20_000,
  });
  const detail = useQuery({
    queryKey: ["order", selectedId],
    queryFn: () => api<Order>(`/orders/${selectedId}`),
    enabled: !!selectedId,
    refetchInterval: (q) => {
      const inFlight = q.state.data?.messages.some((m) => m.status === "pending" || m.status === "sent");
      return inFlight || Date.now() < burstUntil ? 3000 : false;
    },
  });

  const showRail = isDesktop || !selectedId;
  const showDetail = isDesktop || !!selectedId;
  const o = detail.data;

  return (
    <div className="mx-auto flex min-h-[calc(100dvh-6.5rem)] max-w-6xl flex-col rounded-xl border border-line bg-card md:h-full md:min-h-0 md:flex-row md:overflow-hidden">
      <div
        className={`${showRail ? "flex" : "hidden"} w-full shrink-0 flex-col border-b border-line bg-ground md:flex md:w-[340px] md:border-b-0 md:border-r`}
      >
        <div className="border-b border-line p-3">
          <span className="text-sm font-semibold">Orders</span>
          <div className="mt-2 flex flex-wrap gap-1.5">
            {FILTERS.map(([k, label]) => (
              <button
                key={k}
                type="button"
                aria-pressed={filter === k}
                onClick={() => setFilter(k)}
                className={`rounded-full border px-3 py-1 text-xs ${
                  filter === k ? "border-accent bg-accent text-ground" : "border-line bg-card text-muted hover:bg-ground"
                }`}
              >
                {label}
                {k && counts.data?.[k] ? <span className="ml-1 tabular-nums opacity-70">{counts.data[k]}</span> : null}
              </button>
            ))}
          </div>
        </div>
        <div className="flex-1 overflow-y-auto">
          {list.isLoading && <p className="p-4 text-xs text-muted">Loading…</p>}
          {list.data?.length === 0 && (
            <p className="p-4 text-xs text-muted">
              No orders yet. Share a catalog link from the Items page and customers can order from it.
            </p>
          )}
          {list.data?.map((x) => (
            <NavLink
              key={x.id}
              to={`/orders/${x.id}`}
              className={({ isActive }) =>
                `block border-b border-line px-3 py-3 text-xs hover:bg-accent-soft/60 md:py-2.5 ${isActive ? "bg-accent-soft" : ""}`
              }
            >
              <div className="flex items-center justify-between gap-2">
                <span className="font-semibold">{x.number}</span>
                <span className={`rounded-full px-2 py-0.5 text-[10px] font-medium ${TONE[x.status]}`}>{LABEL[x.status]}</span>
              </div>
              <div className="mt-0.5 truncate font-medium">
                {x.customer_name}
                {x.customer_firm ? ` · ${x.customer_firm}` : ""}
              </div>
              <div className="mt-0.5 flex items-center justify-between text-muted">
                <span>
                  {x.item_count} item{x.item_count === 1 ? "" : "s"} · {new Date(x.created_at).toLocaleDateString("en-IN")}
                </span>
                <span className="font-mono">₹{money(x.total)}</span>
              </div>
              {!x.matched_party_id && x.status === "new" && (
                <span className="mt-1 inline-block rounded-full bg-[#efe9df] px-2 py-0.5 text-[10px] text-muted">
                  new customer, confirm
                </span>
              )}
            </NavLink>
          ))}
        </div>
      </div>

      <div className={`${showDetail ? "flex" : "hidden"} min-w-0 flex-1 flex-col md:flex`}>
        {!isDesktop && selectedId && (
          <button className="border-b border-line px-4 py-3 text-left text-sm font-medium text-accent md:hidden" onClick={() => nav("/orders")}>
            ← Orders
          </button>
        )}
        <div className="min-w-0 flex-1 overflow-y-auto p-4 md:p-6">
          {!selectedId ? (
            <div className="grid h-full place-items-center text-sm text-muted">Select an order.</div>
          ) : detail.isLoading ? (
            <p className="text-sm text-muted">Loading…</p>
          ) : o ? (
            <OrderDetail order={o} onChanged={() => setBurstUntil(Date.now() + 20_000)} />
          ) : (
            <p className="err">Could not load that order.</p>
          )}
        </div>
      </div>
    </div>
  );
}
