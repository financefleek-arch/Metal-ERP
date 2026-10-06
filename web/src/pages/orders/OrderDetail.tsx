import { useEffect, useState } from "react";
import { Link } from "react-router-dom";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { api, ApiError } from "../../lib/api";
import { useDebounced } from "../../lib/useDebounced";
import type { PartyListItem } from "../../lib/types";
import type { Order } from "./OrdersPage";

const money = (v: string) => Number(v).toLocaleString("en-IN", { minimumFractionDigits: 0, maximumFractionDigits: 2 });
const errMsg = (e: unknown, fallback: string) => (e instanceof ApiError ? e.message : fallback);

const TONE: Record<Order["status"], string> = {
  new: "bg-[#f1e7d6] text-warn",
  accepted: "bg-[#e6efe8] text-ok",
  invoiced: "bg-[#e6efe8] text-ok",
  rejected: "bg-[#f4e3df] text-danger",
};
const LABEL: Record<Order["status"], string> = { new: "New", accepted: "Accepted", invoiced: "Invoiced", rejected: "Rejected" };

/** One order, for review: who it is, what they want, what changed since, and what to do next. */
export function OrderDetail({ order: o }: { order: Order }) {
  const qc = useQueryClient();
  const [err, setErr] = useState<string | null>(null);
  const [qty, setQty] = useState<Record<string, number>>({});
  const [rejecting, setRejecting] = useState(false);
  const [reason, setReason] = useState("");
  const [picking, setPicking] = useState(false);
  const [q, setQ] = useState("");
  const dq = useDebounced(q.trim(), 250);

  const open = (o.status === "new" || o.status === "accepted") && !o.invoice_id;
  // the boxes follow the saved quantities, and only a real change there resets what was typed
  const saved = o.lines.map((l) => `${l.id}:${l.qty}`).join(",");
  useEffect(() => {
    setQty(Object.fromEntries(o.lines.filter((l) => l.item_id).map((l) => [l.item_id as string, l.qty])));
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [o.id, saved]);
  useEffect(() => {
    setErr(null);
    setRejecting(false);
    setPicking(false);
  }, [o.id]);

  const refresh = (fresh?: Order) => {
    if (fresh) qc.setQueryData(["order", o.id], fresh);
    qc.invalidateQueries({ queryKey: ["orders"] });
    qc.invalidateQueries({ queryKey: ["order-counts"] });
    qc.invalidateQueries({ queryKey: ["order", o.id] });
  };
  const onError = (e: unknown) => setErr(errMsg(e, "That did not work. Try again."));
  const post = <T,>(path: string, body?: unknown) => api<T>(`/orders/${o.id}${path}`, { method: "POST", body: body ?? {} });

  const save = useMutation({
    mutationFn: () =>
      api<Order>(`/orders/${o.id}/lines`, {
        method: "PUT",
        body: { lines: Object.entries(qty).map(([item_id, q]) => ({ item_id, qty: q })) },
      }),
    onSuccess: (r) => {
      setErr(null);
      refresh(r);
    },
    onError,
  });
  const accept = useMutation({ mutationFn: () => post<Order>("/accept"), onSuccess: (r) => (setErr(null), refresh(r)), onError });
  const reject = useMutation({
    mutationFn: () => post<Order>("/reject", { reason }),
    onSuccess: (r) => (setErr(null), setRejecting(false), refresh(r)),
    onError,
  });
  const customer = useMutation({
    mutationFn: (body: { party_id?: string; create_new?: boolean }) => post<Order>("/customer", body),
    onSuccess: (r) => (setErr(null), setPicking(false), setQ(""), refresh(r)),
    onError,
  });
  const invoice = useMutation({ mutationFn: () => post<Order>("/invoice"), onSuccess: (r) => (setErr(null), refresh(r)), onError });

  const matches = useQuery({
    queryKey: ["order-party-search", dq],
    queryFn: () => api<PartyListItem[]>(`/parties?q=${encodeURIComponent(dq)}`),
    enabled: picking && dq.length >= 2,
  });

  const dirty = o.lines.some((l) => l.item_id && qty[l.item_id] !== l.qty);
  const busy = save.isPending || accept.isPending || reject.isPending || customer.isPending || invoice.isPending;

  return (
    <div className="max-w-2xl">
      <div className="flex flex-wrap items-start justify-between gap-3">
        <div>
          <h2 className="font-serif text-xl font-semibold">Order {o.number}</h2>
          <p className="text-xs text-muted">
            {o.customer_name} · {o.customer_phone} · placed {new Date(o.created_at).toLocaleString("en-IN")}
            {o.link_title ? ` · from "${o.link_title}"` : ""}
          </p>
        </div>
        <span className={`rounded-full px-3 py-1 text-xs font-semibold ${TONE[o.status]}`}>{LABEL[o.status]}</span>
      </div>

      {/* who is this */}
      <div
        className={`mt-3 rounded-md border p-3 text-sm ${
          o.matched_party_id ? "border-line bg-ground" : "border-[#e8d9bd] bg-[#faf4ec] text-[#7a5a1c]"
        }`}
      >
        {o.matched_party_id ? (
          <p>
            Customer: <b>{o.matched_party_name}</b>.
            {open && (
              <button type="button" className="ml-2 text-xs text-accent underline" onClick={() => setPicking((v) => !v)}>
                Change
              </button>
            )}
          </p>
        ) : (
          <p>
            <b>New customer, confirm.</b> No customer has this phone number yet
            {o.customer_firm ? `; they gave the firm "${o.customer_firm}"` : ""}.
          </p>
        )}
        <p className="mt-1 text-xs opacity-80">{o.wa_opt_in ? "Agreed to WhatsApp updates." : "Did not agree to WhatsApp updates."}</p>
        {open && !o.matched_party_id && (
          <div className="mt-2 flex flex-wrap gap-2">
            <button type="button" className="btn-primary h-8 px-3 text-xs" disabled={busy} onClick={() => customer.mutate({ create_new: true })}>
              Add as a new customer
            </button>
            <button type="button" className="btn-ghost h-8 px-3 text-xs" onClick={() => setPicking((v) => !v)}>
              Choose an existing customer
            </button>
          </div>
        )}
        {picking && (
          <div className="mt-2">
            <input className="field" placeholder="Search customers…" autoFocus value={q} onChange={(e) => setQ(e.target.value)} />
            {matches.data && matches.data.length > 0 && (
              <ul className="mt-1 max-h-48 divide-y divide-line overflow-y-auto rounded-md border border-line bg-card text-ink">
                {matches.data.slice(0, 8).map((p) => (
                  <li key={p.id}>
                    <button
                      type="button"
                      className="block w-full px-3 py-2 text-left text-sm hover:bg-ground"
                      onClick={() => customer.mutate({ party_id: p.id })}
                    >
                      {p.legal_name}
                      {p.phone ? <span className="ml-2 text-xs text-muted">{p.phone}</span> : null}
                    </button>
                  </li>
                ))}
              </ul>
            )}
          </div>
        )}
      </div>

      {/* what they want */}
      <table className="mt-4 w-full text-sm">
        <thead>
          <tr className="border-b border-line text-left text-[11px] uppercase tracking-wide text-muted">
            <th className="py-2 pr-2 font-medium">Item</th>
            <th className="px-2 py-2 text-right font-medium">Packs</th>
            <th className="px-2 py-2 text-right font-medium">Rate</th>
            <th className="py-2 pl-2 text-right font-medium">Amount</th>
          </tr>
        </thead>
        <tbody>
          {o.lines.map((l) => {
            const q2 = l.item_id ? (qty[l.item_id] ?? l.qty) : l.qty;
            const moved = l.current_rate && Number(l.current_rate) !== Number(l.rate);
            return (
              <tr key={l.id} className="border-b border-line align-top">
                <td className="py-2 pr-2">
                  {l.name}
                  {l.code && <span className="ml-1 text-xs text-muted">{l.code}</span>}
                  <div className="mt-0.5 flex flex-wrap gap-1">
                    {!l.item_id && <span className="rounded-sm bg-[#efe9df] px-1 text-[10px] text-muted">item removed</span>}
                    {l.item_id && !l.available && o.status !== "invoiced" && (
                      <span className="rounded-sm bg-[#f1e7d6] px-1 text-[10px] text-warn">not in stock now</span>
                    )}
                    {moved && (
                      <span className="rounded-sm bg-[#f1e7d6] px-1 text-[10px] text-warn">price now ₹{money(l.current_rate as string)}</span>
                    )}
                  </div>
                </td>
                <td className="px-2 py-2 text-right">
                  {open && l.item_id ? (
                    <span className="inline-flex items-center gap-1">
                      <input
                        aria-label={`Packs of ${l.name}`}
                        inputMode="numeric"
                        className="w-14 rounded-md border border-line bg-card py-1 text-center tabular-nums"
                        value={q2}
                        onChange={(e) => setQty((s) => ({ ...s, [l.item_id as string]: Math.min(999, Number(e.target.value.replace(/\D/g, "")) || 0) }))}
                      />
                      <button
                        type="button"
                        className="text-xs text-muted underline"
                        title="Remove this item"
                        onClick={() => setQty((s) => ({ ...s, [l.item_id as string]: 0 }))}
                      >
                        remove
                      </button>
                    </span>
                  ) : (
                    <span className="tabular-nums">{l.qty}</span>
                  )}
                </td>
                <td className="px-2 py-2 text-right tabular-nums">₹{money(l.rate)}</td>
                <td className="py-2 pl-2 text-right font-medium tabular-nums">
                  {q2 === 0 ? <span className="text-muted line-through">₹{money(l.amount)}</span> : `₹${money(String(Number(l.rate) * q2))}`}
                </td>
              </tr>
            );
          })}
          <tr>
            <td colSpan={3} className="py-2 pr-2 text-right font-semibold">
              Total (before GST)
            </td>
            <td className="py-2 pl-2 text-right text-base font-semibold tabular-nums">₹{money(o.total)}</td>
          </tr>
        </tbody>
      </table>
      {dirty && (
        <div className="mt-2 flex items-center gap-3 text-sm">
          <button type="button" className="btn-primary h-9" disabled={busy} onClick={() => save.mutate()}>
            {save.isPending ? "Saving…" : "Save changes"}
          </button>
          <span className="text-xs text-muted">Items set to 0 are removed.</span>
        </div>
      )}
      {o.note && (
        <p className="mt-3 text-sm">
          <span className="text-xs uppercase tracking-wide text-muted">Customer note</span>
          <br />
          {o.note}
        </p>
      )}
      {o.status === "rejected" && o.reject_reason && (
        <p className="mt-3 rounded-md border border-line bg-ground p-3 text-sm">
          <span className="text-xs uppercase tracking-wide text-muted">Reason given to the customer</span>
          <br />
          {o.reject_reason}
        </p>
      )}

      {err && (
        <p className="err" role="alert">
          {err}
        </p>
      )}

      {o.messages.length > 0 && (
        <div className="mt-4 text-xs text-muted">
          <p className="uppercase tracking-wide">WhatsApp</p>
          <ul className="mt-1 space-y-0.5">
            {o.messages.map((m, i) => (
              <li key={i}>
                {m.event ?? m.template} to {m.to_phone}:{" "}
                <span className={m.status === "failed" ? "text-danger" : "text-ok"}>{m.status}</span>
                {m.error ? ` (${m.error.slice(0, 120)})` : ""}
              </li>
            ))}
          </ul>
        </div>
      )}

      {/* what next */}
      <div className="mt-5 flex flex-wrap items-center gap-2 border-t border-line pt-4">
        {o.invoice_id ? (
          <Link to={`/invoices/${o.invoice_id}`} className="btn-primary">
            {o.invoice_status === "draft" ? "Open the draft invoice" : "Open the invoice"}
          </Link>
        ) : open ? (
          <>
            {o.status === "new" && (
              <button type="button" className="btn-ghost" disabled={busy || dirty} onClick={() => accept.mutate()}>
                Accept
              </button>
            )}
            <button
              type="button"
              className="btn-primary"
              disabled={busy || dirty || !o.matched_party_id}
              title={o.matched_party_id ? "" : "Confirm who the customer is first"}
              onClick={() => invoice.mutate()}
            >
              {invoice.isPending ? "Making…" : "Create draft invoice"}
            </button>
            <button type="button" className="btn-ghost" disabled={busy} onClick={() => setRejecting((v) => !v)}>
              Reject…
            </button>
          </>
        ) : null}
      </div>
      {rejecting && (
        <div className="mt-3 rounded-md border border-line bg-ground p-3">
          <label className="label" htmlFor="rej-reason">
            Why? The customer is told.
          </label>
          <input id="rej-reason" className="field" maxLength={1000} value={reason} onChange={(e) => setReason(e.target.value)} placeholder="e.g. Out of stock this month" />
          <div className="mt-2 flex gap-2">
            <button type="button" className="btn-primary h-9 bg-danger hover:bg-danger" disabled={!reason.trim() || busy} onClick={() => reject.mutate()}>
              Reject order
            </button>
            <button type="button" className="btn-ghost h-9" onClick={() => setRejecting(false)}>
              Cancel
            </button>
          </div>
        </div>
      )}
      {open && !o.matched_party_id && (
        <p className="mt-2 text-xs text-muted">Confirm who the customer is before making the invoice.</p>
      )}
    </div>
  );
}
