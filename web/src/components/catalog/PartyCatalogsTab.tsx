import { Link } from "react-router-dom";
import { useQuery } from "@tanstack/react-query";
import { api } from "../../lib/api";
import { marginLabel, type SupplierCatalogs } from "../../lib/catalog";
import type { Party } from "../../lib/types";

/** Party page, "Catalogs" tab: what you hold from this supplier. */
export function PartyCatalogsTab({ party }: { party: Party }) {
  const data = useQuery({
    queryKey: ["supplier-catalogs-of", party.id],
    queryFn: () => api<SupplierCatalogs>(`/supplier-catalogs/by-supplier/${party.id}`),
  });
  const d = data.data;

  return (
    <div className="max-w-2xl">
      <div className="flex flex-wrap items-start justify-between gap-3">
        <div>
          <h2 className="font-serif text-lg font-semibold">Catalogs from {party.legal_name}</h2>
          {d && (
            <p className="mt-1 text-sm text-muted">
              {d.product_count} product{d.product_count === 1 ? "" : "s"}
              {d.product_count > 0 && ` · ${d.promoted_count} in your item list`}
            </p>
          )}
        </div>
        <Link
          to={`/catalogs?supplier=${party.id}`}
          className="btn-primary"
          aria-label={`Upload a catalog from ${party.legal_name}`}
        >
          Upload a catalog
        </Link>
      </div>

      {data.isLoading && <p className="mt-4 text-sm text-muted">Loading…</p>}
      {data.isError && <p className="err mt-4">Could not load catalogs. Try again.</p>}
      {d && d.catalogs.length === 0 && (
        <p className="mt-4 text-sm text-muted">
          No catalogs from this supplier yet. When you upload one, products you already have keep
          their code, group and name.
        </p>
      )}
      {d && d.catalogs.length > 0 && (
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
    </div>
  );
}
