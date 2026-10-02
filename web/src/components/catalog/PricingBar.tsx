import { useEffect, useState } from "react";
import { useMutation, useQueryClient } from "@tanstack/react-query";
import { api, ApiError } from "../../lib/api";
import {
  percentLabel,
  trimMultiplier,
  validMultiplier,
  type CatalogDetail,
  type RoundingStep,
} from "../../lib/catalog";

const QUICK = ["1.10", "1.20", "1.25", "1.30", "1.50"];

/** One multiplier for the whole catalog, plus the rounding step. Items with their own
 *  multiplier keep it. New prices are computed on the server. */
export function PricingBar({
  catalog,
  onResetOverrides,
  resetting,
}: {
  catalog: CatalogDetail;
  onResetOverrides: () => void;
  resetting: boolean;
}) {
  const qc = useQueryClient();
  const [mult, setMult] = useState(trimMultiplier(catalog.multiplier));
  const [step, setStep] = useState<RoundingStep>(catalog.rounding_step as RoundingStep);
  const [err, setErr] = useState<string | null>(null);
  const [saved, setSaved] = useState(false);

  useEffect(() => {
    setMult(trimMultiplier(catalog.multiplier));
    setStep(catalog.rounding_step as RoundingStep);
  }, [catalog.multiplier, catalog.rounding_step]);

  const valid = validMultiplier(mult);
  const dirty =
    valid && (Number(mult) !== Number(catalog.multiplier) || step !== catalog.rounding_step);

  const save = useMutation({
    mutationFn: () =>
      api<CatalogDetail>(`/supplier-catalogs/${catalog.id}`, {
        method: "PATCH",
        body: { multiplier: Number(mult).toFixed(3), rounding_step: step },
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

  const pct = valid ? percentLabel(Number(mult)) : "";
  const below = valid && Number(mult) < 1;

  return (
    <section className="card mb-4 p-4" aria-label="Pricing">
      <div className="flex flex-wrap items-end gap-x-6 gap-y-3">
        <div>
          <label className="label" htmlFor="price-mult">
            Multiply supplier prices by
          </label>
          <div className="flex items-center gap-2">
            <span className="text-lg text-muted" aria-hidden>
              ×
            </span>
            <input
              id="price-mult"
              className="field w-28 text-right font-mono tabular-nums"
              inputMode="decimal"
              aria-invalid={!valid && mult.trim() !== ""}
              value={mult}
              onChange={(e) => {
                setMult(e.target.value);
                setErr(null);
              }}
              onKeyDown={(e) => {
                if (e.key === "Enter" && dirty && !save.isPending) save.mutate();
              }}
            />
            <span
              className={`min-w-[5.5rem] rounded-full px-2.5 py-1 text-center text-xs font-medium ${
                below ? "bg-[#f4e3df] text-danger" : "bg-accent-soft text-accent"
              }`}
              aria-live="polite"
            >
              {pct || "—"}
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

        <div className="flex flex-wrap items-center gap-1.5" aria-label="Common multipliers">
          {QUICK.map((q) => (
            <button
              key={q}
              type="button"
              className="rounded-full border border-line px-2.5 py-1 font-mono text-xs text-muted hover:bg-ground"
              onClick={() => setMult(trimMultiplier(q))}
            >
              ×{trimMultiplier(q)}
            </button>
          ))}
        </div>
      </div>

      <p className="mt-3 text-sm text-muted">
        New price = supplier price × multiplier, rounded. Supplier prices are never changed.
        {below && (
          <span className="ml-1 text-danger">This sells below the supplier price.</span>
        )}
      </p>
      {!valid && mult.trim() !== "" && (
        <p className="err" role="alert">
          Enter a number from 0.001 to 99.999, with up to 3 decimals.
        </p>
      )}
      {err && (
        <p className="err" role="alert">
          {err}
        </p>
      )}
      {catalog.override_count > 0 && (
        <p className="mt-2 text-sm">
          <span className="font-medium">{catalog.override_count}</span> item
          {catalog.override_count === 1 ? " uses" : "s use"} their own multiplier and ignore this
          one.{" "}
          <button
            type="button"
            className="text-accent underline disabled:opacity-50"
            disabled={resetting}
            onClick={onResetOverrides}
          >
            Reset them to ×{trimMultiplier(catalog.multiplier)}
          </button>
        </p>
      )}
    </section>
  );
}
