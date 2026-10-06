import { useEffect, useState } from "react";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { api, ApiError } from "../../lib/api";
import { validMargin } from "../../lib/catalog";

export interface SupplierDefaults {
  bulk_margin_pct: string | null;
  rounding_step: number | null;
  group_map: Record<string, string>;
  add_automatically: boolean;
  mark_in_stock: boolean;
}

/** What this supplier usually gets, applied to each new catalog you upload from them. */
export function SupplierDefaultsCard({ partyId }: { partyId: string }) {
  const qc = useQueryClient();
  const q = useQuery({
    queryKey: ["supplier-defaults", partyId],
    queryFn: () => api<SupplierDefaults>(`/supplier-catalogs/suppliers/${partyId}/defaults`),
  });
  const d = q.data;
  const [margin, setMargin] = useState("");
  const [step, setStep] = useState("");
  const [from, setFrom] = useState("");
  const [to, setTo] = useState("");
  const [err, setErr] = useState<string | null>(null);
  const [saved, setSaved] = useState(false);

  useEffect(() => {
    if (!d) return;
    setMargin(d.bulk_margin_pct ? String(Number(d.bulk_margin_pct)) : "");
    setStep(d.rounding_step ? String(d.rounding_step) : "");
  }, [d]);

  const save = useMutation({
    mutationFn: (body: Record<string, unknown>) =>
      api<SupplierDefaults>(`/supplier-catalogs/suppliers/${partyId}/defaults`, { method: "PUT", body }),
    onSuccess: (r) => {
      setErr(null);
      setSaved(true);
      window.setTimeout(() => setSaved(false), 1500);
      qc.setQueryData(["supplier-defaults", partyId], r);
    },
    onError: (e) => setErr(e instanceof ApiError ? e.message : "Could not save. Try again."),
  });

  if (!d) return null;
  const marginOk = margin.trim() === "" || validMargin(margin);
  const mapEntries = Object.entries(d.group_map);

  return (
    <section className="card mt-5 max-w-2xl bg-card p-4" aria-label="Supplier defaults">
      <div className="flex items-center justify-between gap-3">
        <h3 className="font-serif text-base font-semibold">Defaults for new catalogs</h3>
        {saved && <span className="text-xs text-ok">Saved</span>}
      </div>
      <p className="mt-1 text-xs text-muted">
        Applied when you upload a catalog from this supplier, so repeat uploads need no decisions.
      </p>

      <div className="mt-3 flex flex-wrap items-end gap-4">
        <div>
          <label className="label" htmlFor="sd-margin">
            Margin %
          </label>
          <input
            id="sd-margin"
            className="field w-24"
            inputMode="decimal"
            placeholder="none"
            value={margin}
            onChange={(e) => setMargin(e.target.value)}
            onBlur={() =>
              marginOk &&
              save.mutate({ bulk_margin_pct: margin.trim() === "" ? null : Number(margin).toFixed(2) })
            }
          />
        </div>
        <div>
          <label className="label" htmlFor="sd-step">
            Round up to
          </label>
          <select
            id="sd-step"
            className="field w-28"
            value={step}
            onChange={(e) => {
              setStep(e.target.value);
              save.mutate({ rounding_step: e.target.value === "" ? null : Number(e.target.value) });
            }}
          >
            <option value="">none</option>
            <option value="1">Rs 1</option>
            <option value="5">Rs 5</option>
            <option value="10">Rs 10</option>
          </select>
        </div>
      </div>
      {!marginOk && (
        <p className="err" role="alert">
          Enter a margin from -99.99 to 1000.
        </p>
      )}

      <div className="mt-3 space-y-1.5 text-sm">
        <label className="flex items-start gap-2">
          <input
            type="checkbox"
            className="mt-1"
            checked={d.add_automatically}
            onChange={(e) => save.mutate({ add_automatically: e.target.checked })}
          />
          <span>
            Add the items to my item list automatically after an upload
            <span className="block text-xs text-muted">Otherwise you review first, then add.</span>
          </span>
        </label>
        <label className={`flex items-start gap-2 ${d.add_automatically ? "" : "opacity-50"}`}>
          <input
            type="checkbox"
            className="mt-1"
            disabled={!d.add_automatically}
            checked={d.mark_in_stock}
            onChange={(e) => save.mutate({ mark_in_stock: e.target.checked })}
          />
          <span>
            Mark them In stock
            <span className="block text-xs text-muted">Otherwise they start Out of stock.</span>
          </span>
        </label>
      </div>

      <div className="mt-4">
        <p className="label">Group names</p>
        <p className="text-xs text-muted">
          When our rules suggest a group name you would call something else, say so once.
        </p>
        {mapEntries.length > 0 && (
          <ul className="mt-2 space-y-1 text-sm">
            {mapEntries.map(([k, v]) => (
              <li key={k} className="flex items-center gap-2">
                <span className="capitalize">{k}</span>
                <span aria-hidden>→</span>
                <span className="font-medium">{v}</span>
                <button
                  type="button"
                  className="ml-auto text-xs text-muted underline"
                  onClick={() => save.mutate({ group_map: { [k]: "" } })}
                >
                  Remove
                </button>
              </li>
            ))}
          </ul>
        )}
        <div className="mt-2 flex flex-wrap items-end gap-2">
          <input
            className="field w-44"
            placeholder="Suggested group"
            aria-label="Suggested group"
            value={from}
            onChange={(e) => setFrom(e.target.value)}
          />
          <span aria-hidden>→</span>
          <input
            className="field w-44"
            placeholder="Your group"
            aria-label="Your group"
            value={to}
            onChange={(e) => setTo(e.target.value)}
          />
          <button
            type="button"
            className="btn-ghost h-9"
            disabled={!from.trim() || !to.trim() || save.isPending}
            onClick={() => {
              save.mutate({ group_map: { [from]: to } });
              setFrom("");
              setTo("");
            }}
          >
            Add
          </button>
        </div>
      </div>
      {err && (
        <p className="err" role="alert">
          {err}
        </p>
      )}
    </section>
  );
}
