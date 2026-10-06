import { Link } from "react-router-dom";
import { useQuery } from "@tanstack/react-query";
import { api } from "../../lib/api";
import { useAuth } from "../../lib/auth";
import { marginLabel, type SupplierCatalogs } from "../../lib/catalog";
import type { Party } from "../../lib/types";
import { SupplierDefaultsCard } from "./SupplierDefaultsCard";

/** Party page, "Catalogs" tab: what you hold from this supplier. */
export function PartyCatalogsTab({ party }: { party: Party }) {
  const { me } = useAuth();
  const lists = !!me?.ext_supplier_catalog;
  const data = useQuery({
    queryKey: ["supplier-catalogs-of", party.id],
    queryFn: () => api<SupplierCatalogs>(`/supplier-catalogs/by-supplier/${party.id}`),
    enabled: lists,
  });
  const d = data.data;
  const billsQ = useQuery({
    queryKey: ["bills-of", party.id],
    queryFn: () =>
      api<{
        bills: { id: string; bill_no: string | null; bill_date: string | null; grand_total: string | null; status: string }[];
        spend: string;
      }>(`/documents/by-supplier/${party.id}`),
    enabled: !!me?.ext_inward_import,
  });
  const bills = billsQ.data;

  return (
    <div className="max-w-2xl">
      <div className="flex flex-wrap items-start justify-between gap-3">
        <div>
          <h2 className="font-serif text-lg font-semibold">Documents from {party.legal_name}</h2>
          {d && (
            <p className="mt-1 text-sm text-muted">
              {d.product_count} product{d.product_count === 1 ? "" : "s"}
              {d.product_count > 0 && ` · ${d.promoted_count} in your item list`}
            </p>
          )}
        </div>
        <Link
          to={`/documents?supplier=${party.id}`}
          className="btn-primary"
          aria-label={`Upload a document from ${party.legal_name}`}
        >
          Upload a document
        </Link>
      </div>

      {bills && (
        <section className="mt-4">
          <h3 className="label">Bills</h3>
          {bills.bills.length === 0 ? (
            <p className="text-sm text-muted">No bills matched to this supplier yet.</p>
          ) : (
            <>
              <p className="text-sm text-muted">
                Spent with them (approved bills):{" "}
                <b className="tabular-nums text-ink">₹{Number(bills.spend).toLocaleString("en-IN")}</b>
              </p>
              <ul className="mt-2 divide-y divide-line rounded-lg border border-line">
                {bills.bills.map((b) => (
                  <li key={b.id} className="flex items-center justify-between gap-2 px-3 py-2">
                    <Link to={`/inward/${b.id}`} className="text-accent hover:underline">
                      {b.bill_no ? `Bill ${b.bill_no}` : "Bill"}
                    </Link>
                    <span className="text-xs text-muted tabular-nums">
                      {b.bill_date ?? ""} {b.grand_total ? `· ₹${b.grand_total}` : ""} · {b.status.replace("_", " ")}
                    </span>
                  </li>
                ))}
              </ul>
            </>
          )}
        </section>
      )}

      {lists && data.isLoading && <p className="mt-4 text-sm text-muted">Loading…</p>}
      {lists && data.isError && <p className="err mt-4">Could not load catalogs. Try again.</p>}
      {lists && d && d.catalogs.length === 0 && (
        <p className="mt-4 text-sm text-muted">
          No catalogs from this supplier yet. When you upload one, products you already have keep
          their code, group and name.
        </p>
      )}
      {lists && d && d.catalogs.length > 0 && (
        <ul className="mt-4 divide-y divide-line rounded-lg border border-line">
          {d.catalogs.map((c) => (
            <li key={c.id} className="flex flex-wrap items-center justify-between gap-2 px-3 py-2.5">
              <div className="min-w-0">
                <Link to={`/catalogs/${c.id}`} className="font-medium text-accent hover:underline">
                  {c.title}
                </Link>
                <p className="text-xs text-muted">
                  {c.item_count} items · {c.page_count} pages · {new Date(c.created_at).toLocaleDateString()}
                </p>
              </div>
              <span className="text-sm tabular-nums text-muted">{marginLabel(c.bulk_margin_pct)}</span>
            </li>
          ))}
        </ul>
      )}
      {lists && <SupplierDefaultsCard partyId={party.id} />}
    </div>
  );
}
