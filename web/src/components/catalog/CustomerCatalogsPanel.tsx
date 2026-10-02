import { useState } from "react";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { api, ApiError } from "../../lib/api";
import { downloadFile } from "../../lib/download";
import { fileSize, type CustomerCatalog } from "../../lib/catalog";

/** The customer catalogs made from this supplier catalog, newest first. A version turns
 *  "out of date" when prices or items change after it was made. */
export function CustomerCatalogsPanel({
  catalogId,
  onCreate,
}: {
  catalogId: string;
  onCreate: () => void;
}) {
  const qc = useQueryClient();
  const [err, setErr] = useState<string | null>(null);
  const [confirm, setConfirm] = useState<string | null>(null);

  const list = useQuery({
    queryKey: ["customer-catalogs", catalogId],
    queryFn: () => api<CustomerCatalog[]>(`/supplier-catalogs/${catalogId}/customer-catalogs`),
  });

  const remove = useMutation({
    mutationFn: (id: string) =>
      api(`/supplier-catalogs/${catalogId}/customer-catalogs/${id}`, { method: "DELETE" }),
    onSuccess: () => {
      setConfirm(null);
      setErr(null);
      qc.invalidateQueries({ queryKey: ["customer-catalogs", catalogId] });
    },
    onError: (e) => setErr(e instanceof ApiError ? e.message : "Could not delete. Try again."),
  });

  const versions = list.data ?? [];
  if (list.isLoading) return null;

  return (
    <section className="card mb-4 p-4" aria-label="Customer catalogs">
      <div className="flex flex-wrap items-center justify-between gap-2">
        <div>
          <h2 className="font-serif text-lg font-semibold">Customer catalogs</h2>
          <p className="text-sm text-muted">
            PDFs with your prices to hand to customers. Each change of prices or items makes the
            older versions out of date.
          </p>
        </div>
        <button type="button" className="btn-primary" onClick={onCreate}>
          Create customer catalog
        </button>
      </div>

      {err && (
        <p className="err" role="alert">
          {err}
        </p>
      )}

      {versions.length === 0 ? (
        <p className="mt-3 text-sm text-muted">None yet.</p>
      ) : (
        <ul className="mt-3 divide-y divide-line">
          {versions.map((v) => (
            <li key={v.id} className="flex flex-wrap items-center gap-x-4 gap-y-1 py-2 text-sm">
              <span className="w-10 font-mono text-xs text-muted">v{v.version}</span>
              <span className="min-w-[10rem] flex-1 font-medium">{v.title}</span>
              <span className="text-xs text-muted">
                {v.item_count} items · {v.page_count} pages · {fileSize(v.byte_size)} ·{" "}
                {new Date(v.created_at).toLocaleDateString("en-IN", {
                  day: "numeric",
                  month: "short",
                  year: "numeric",
                })}
              </span>
              {v.stale && (
                <span className="rounded-full bg-[#f1e7d6] px-2 py-0.5 text-[10px] font-medium text-warn">
                  Out of date
                </span>
              )}
              <button
                type="button"
                className="btn-ghost h-8 px-3"
                onClick={() =>
                  downloadFile(
                    `/supplier-catalogs/${catalogId}/customer-catalogs/${v.id}/file`,
                    `catalog-v${v.version}.pdf`,
                  ).catch(() => setErr("Could not download that file."))
                }
              >
                Download
              </button>
              {confirm === v.id ? (
                <span className="flex items-center gap-2">
                  <button
                    type="button"
                    className="btn-primary h-8 bg-danger px-3 hover:bg-danger"
                    disabled={remove.isPending}
                    onClick={() => remove.mutate(v.id)}
                  >
                    Delete
                  </button>
                  <button type="button" className="btn-ghost h-8 px-3" onClick={() => setConfirm(null)}>
                    Keep
                  </button>
                </span>
              ) : (
                <button type="button" className="btn-ghost h-8 px-3" onClick={() => setConfirm(v.id)}>
                  Delete
                </button>
              )}
            </li>
          ))}
        </ul>
      )}
      {versions.some((v) => v.stale) && (
        <p className="mt-2 text-xs text-muted">
          “Out of date” means prices or items changed after that version was made. Create a new
          version to get the current prices.
        </p>
      )}
    </section>
  );
}
