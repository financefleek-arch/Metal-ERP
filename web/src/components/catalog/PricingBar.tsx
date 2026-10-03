import { useEffect, useState } from "react";
import { useMutation, useQueryClient } from "@tanstack/react-query";
import { api, ApiError } from "../../lib/api";
import {
  marginExample,
  trimMargin,
  validMargin,
  type CatalogDetail,
  type RoundingStep,
} from "../../lib/catalog";

const QUICK = ["10", "15", "20", "25", "30", "40", "50"];

/** One bulk margin for the whole catalog, plus the rounding step. Items with their own
 *  (item-level) margin keep it. New prices are computed on the server. */
export function PricingBar({
  catalog,
  onResetItemMargins,
  resetting,
}: {
  catalog: CatalogDetail;
  onResetItemMargins: () => void;
  resetting: boolean;
}) {
  const qc = useQueryClient();
  const [margin, setMargin] = useState(trimMargin(catalog.bulk_margin_pct));
  const [step, setStep] = useState<RoundingStep>(catalog.rounding_step as RoundingStep);
  const [err, setErr] = useState<string | null>(null);
  const [saved, setSaved] = useState(false);

  useEffect(() => {
    setMargin(trimMargin(catalog.bulk_margin_pct));
    setStep(catalog.rounding_step as RoundingStep);
  }, [catalog.bulk_margin_pct, catalog.rounding_step]);

  const valid = validMargin(margin);
  const dirty =
    valid &&
    (Number(margin) !== Number(catalog.bulk_margin_pct) || step !== catalog.rounding_step);

  const save = useMutation({
    mutationFn: () =>
      api<CatalogDetail>(`/supplier-catalogs/${catalog.id}`, {
        method: "PATCH",
        body: { bulk_margin_pct: Number(margin).toFixed(2), rounding_step: step },
      }),
    onSuccess: (c) => {
      setErr(null);
      setSaved(true);
      window.setTimeout(() => setSaved(false), 1800);
      qc.setQueryData(["supplier-catalog", catalog.id], c);
      qc.invalidateQueries({ queryKey: ["catalog-items", catalog.id] });
      qc.invalidateQueries({ queryKey: ["supplier-catalogs"] });
      // new prices make existing customer catalogs out of date
      qc.invalidateQueries({ queryKey: ["customer-catalogs", catalog.id] });
    },
    onError: (e) => setErr(e instanceof ApiError ? e.message : "Could not save prices. Try again."),
  });

  const example = valid ? marginExample(Number(margin)) : "";
  const below = valid && Number(margin) < 0;

  return (
    <section className="card mb-4 p-4" aria-label="Pricing">
      <div className="flex flex-wrap items-end gap-x-6 gap-y-3">
        <div>
          <label className="label" htmlFor="price-margin">
            Bulk margin on supplier prices
          </label>
          <div className="flex items-center gap-2">
            <div className="relative">
              <input
                id="price-margin"
                className="field w-28 pr-7 text-right font-mono tabular-nums"
                inputMode="decimal"
                aria-invalid={!valid && margin.trim() !== ""}
                value={margin}
                onChange={(e) => {
                  setMargin(e.target.value);
                  setErr(null);
                }}
                onKeyDown={(e) => {
                  if (e.key === "Enter" && dirty && !save.isPending) save.mutate();
                }}
              />
              <span
                className="pointer-events-none absolute right-2.5 top-1/2 -translate-y-1/2 text-muted"
                aria-hidden
              >
                %
              </span>
            </div>
            <span
              className={`min-w-[8rem] rounded-full px-2.5 py-1 text-center text-xs font-medium ${
                below ? "bg-[#f4e3df] text-danger" : "bg-accent-soft text-accent"
              }`}
              aria-live="polite"
            >
              {example || "—"}
            </span>
          </div>
        </div>

        <div>
          <label className="label" htmlFor="price-step">
            Round to
          </label>
          <select
            id="price-step"
            className="field w-36"
            value={step}
            onChange={(e) => setStep(Number(e.target.value) as RoundingStep)}
          >
            <option value={1}>nearest ₹1</option>
            <option value={5}>nearest ₹5</option>
            <option value={10}>nearest ₹10</option>
          </select>
        </div>

        <div className="flex items-center gap-2">
          <button
            type="button"
            className="btn-primary"
            disabled={!dirty || save.isPending}
            onClick={() => save.mutate()}
          >
            {save.isPending ? "Saving…" : "Apply to all"}
          </button>
          {saved && (
            <span className="text-sm text-ok" role="status">
              Prices updated ✓
            </span>
          )}
        </div>

        <div className="flex flex-wrap items-center gap-1.5" aria-label="Common margins">
          {QUICK.map((q) => (
            <button
              key={q}
              type="button"
              className="rounded-full border border-line px-2.5 py-1 font-mono text-xs text-muted hover:bg-ground"
              onClick={() => setMargin(q)}
            >
              {q}%
            </button>
          ))}
        </div>
      </div>

      <p className="mt-3 text-sm text-muted">
        New price = supplier price + margin, rounded. Supplier prices are never changed.
        {below && <span className="ml-1 text-danger">This sells below the supplier price.</span>}
      </p>
      {!valid && margin.trim() !== "" && (
        <p className="err" role="alert">
          Enter a margin from -99.99 to 1000, with up to 2 decimals.
        </p>
      )}
      {err && (
        <p className="err" role="alert">
          {err}
        </p>
      )}
      {catalog.item_margin_count > 0 && (
        <p className="mt-2 text-sm">
          <span className="font-medium">{catalog.item_margin_count}</span> item
          {catalog.item_margin_count === 1 ? " has" : "s have"} their own margin and ignore the bulk
          margin.{" "}
          <button
            type="button"
            className="text-accent underline disabled:opacity-50"
            disabled={resetting}
            onClick={onResetItemMargins}
          >
            Reset them to {trimMargin(catalog.bulk_margin_pct)}%
          </button>
        </p>
      )}
    </section>
  );
}
