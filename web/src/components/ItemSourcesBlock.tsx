import { Link } from "react-router-dom";
import { useQuery } from "@tanstack/react-query";
import { api } from "../lib/api";
import type { ItemSources } from "../lib/types";

/** "Came from": the supplier price lists and inward bills behind an item. */
export function ItemSourcesBlock({ itemId }: { itemId: string }) {
  const q = useQuery({
    queryKey: ["item-sources", itemId],
    queryFn: () => api<ItemSources>(`/items/${itemId}/sources`),
  });
  const d = q.data;
  if (!d || (d.catalogs.length === 0 && d.bills.length === 0)) return null;
  return (
    <section className="rounded-lg border border-line p-3" aria-label="Came from">
      <h3 className="mb-2 text-[10px] font-semibold uppercase tracking-wide text-muted">Came from</h3>
      <ul className="flex flex-col gap-1.5 text-xs">
        {d.catalogs.map((c) => (
          <li key={c.catalog_id} className="flex flex-wrap items-baseline gap-x-2">
            <span className="rounded-sm bg-accent-soft px-1 text-[9px] font-bold uppercase text-accent">
              price list
            </span>
            <Link to={`/catalogs/${c.catalog_id}`} className="font-medium text-accent hover:underline">
              {c.title}
            </Link>
            <span className="text-muted">
              {c.supplier_name ? `${c.supplier_name} · ` : ""}ref {c.supplier_code} · cost
              ₹{c.cost_price}
            </span>
          </li>
        ))}
        {d.bills.map((b) => (
          <li key={b.bill_id} className="flex flex-wrap items-baseline gap-x-2">
            <span className="rounded-sm bg-[#f1e7d6] px-1 text-[9px] font-bold uppercase text-warn">
              bill
            </span>
            <Link to={`/inward/${b.bill_id}`} className="font-medium text-accent hover:underline">
              {b.bill_no ? `Bill ${b.bill_no}` : "Inward bill"}
            </Link>
            <span className="text-muted">
              {b.supplier_name ? `${b.supplier_name} · ` : ""}
              {b.bill_date ?? ""}
              {b.quantity ? ` · ${b.quantity} ${b.uom ?? ""}` : ""}
              {b.rate ? ` at ₹${b.rate}` : ""}
            </span>
          </li>
        ))}
      </ul>
    </section>
  );
}
