import { useEffect, useState } from "react";
import { useParams } from "react-router-dom";

interface Line {
  name: string;
  code: string | null;
  qty: number;
  pack_qty: number;
  rate: string;
  amount: string;
}
interface OrderView {
  number: string;
  status: "new" | "accepted" | "invoiced" | "rejected";
  status_text: string;
  total: string;
  placed_at: string;
  shop_name: string;
  shop_phone: string | null;
  reject_reason: string | null;
  lines: Line[];
}

const money = (v: string) => Number(v).toLocaleString("en-IN", { minimumFractionDigits: 0, maximumFractionDigits: 2 });
const TONE: Record<OrderView["status"], string> = {
  new: "bg-[#f1e7d6] text-warn",
  accepted: "bg-[#e6efe8] text-ok",
  invoiced: "bg-[#e6efe8] text-ok",
  rejected: "bg-[#f4e3df] text-danger",
};
const LABEL: Record<OrderView["status"], string> = {
  new: "Received",
  accepted: "Confirmed",
  invoiced: "Invoiced",
  rejected: "Not accepted",
};

/** A customer checking on their own order from the link in their message. No login. */
export function PublicOrderPage() {
  const { token = "" } = useParams();
  const [o, setO] = useState<OrderView | null>(null);
  const [state, setState] = useState<"loading" | "ok" | "gone" | "error">("loading");

  useEffect(() => {
    let cancelled = false;
    const load = async () => {
      try {
        const res = await fetch(`/api/public/orders/${encodeURIComponent(token)}`);
        if (cancelled) return;
        if (res.status === 404) return setState("gone");
        if (!res.ok) return setState("error");
        setO((await res.json()) as OrderView);
        setState("ok");
      } catch {
        if (!cancelled) setState("error");
      }
    };
    void load();
    // an order moves along while the customer waits: look again now and then
    const h = window.setInterval(load, 30_000);
    return () => {
      cancelled = true;
      window.clearInterval(h);
    };
  }, [token]);

  if (state !== "ok" || !o) {
    return (
      <div className="grid min-h-[100dvh] place-items-center bg-ground p-6 text-center text-sm text-muted">
        <p role="status">
          {state === "loading"
            ? "Loading…"
            : state === "gone"
              ? "We could not find that order. Please check the link."
              : "Something went wrong. Please try again."}
        </p>
      </div>
    );
  }

  return (
    <div className="min-h-[100dvh] bg-ground text-ink">
      <header className="bg-ink px-4 py-3 text-ground">
        <div className="mx-auto flex max-w-lg items-baseline justify-between gap-3">
          <h1 className="font-serif text-lg font-semibold">{o.shop_name}</h1>
          {o.shop_phone && (
            <a href={`tel:${o.shop_phone}`} className="text-xs text-[#b9b3a7] underline">
              {o.shop_phone}
            </a>
          )}
        </div>
      </header>
      <main className="mx-auto max-w-lg px-4 pb-10 pt-5">
        <div className="flex items-start justify-between gap-3">
          <div>
            <h2 className="font-serif text-2xl font-semibold">{o.number}</h2>
            <p className="text-xs text-muted">Placed {new Date(o.placed_at).toLocaleString("en-IN")}</p>
          </div>
          <span className={`rounded-full px-3 py-1 text-xs font-semibold ${TONE[o.status]}`}>{LABEL[o.status]}</span>
        </div>
        <p className="mt-3 text-sm" role="status">
          {o.status_text}
        </p>
        {o.reject_reason && (
          <p className="mt-2 rounded-md border border-line bg-card p-3 text-sm">
            <span className="text-xs uppercase tracking-wide text-muted">From the shop</span>
            <br />
            {o.reject_reason}
          </p>
        )}
        <ul className="card mt-4 divide-y divide-line bg-card">
          {o.lines.map((l, i) => (
            <li key={i} className="flex items-start justify-between gap-3 p-3 text-sm">
              <div className="min-w-0">
                <p className="font-medium leading-snug">{l.name}</p>
                <p className="text-xs text-muted tabular-nums">
                  {l.qty} pack{l.qty === 1 ? "" : "s"} × ₹{money(l.rate)}
                </p>
              </div>
              <p className="shrink-0 font-semibold tabular-nums">₹{money(l.amount)}</p>
            </li>
          ))}
          <li className="flex justify-between p-3 text-sm">
            <span className="font-semibold">Total</span>
            <span className="text-base font-semibold tabular-nums">₹{money(o.total)}</span>
          </li>
        </ul>
        <p className="mt-3 text-xs text-muted">Prices are before GST unless the shop says otherwise.</p>
      </main>
    </div>
  );
}
