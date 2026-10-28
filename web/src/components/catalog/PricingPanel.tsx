import { useEffect, useState } from "react";
import { useMutation, useQueryClient } from "@tanstack/react-query";
import { api, ApiError } from "../../lib/api";
import {
  marginExample,
  trimMargin,
  validMargin,
  type CatalogDetail,
  type GroupSummary,
  type RoundingStep,
} from "../../lib/catalog";
import { EditableNumber } from "./Editable";

const QUICK = ["15", "20", "25", "30"];

/**
 * Step 1 of the review: the margin on supplier prices (one for the list, optionally one per
 * group) and the rounding step. Saving only changes this price list's New price column; the
 * shop's items are updated separately, after they have been added.
 */
export function PricingPanel({
  catalog,
  groups,
  resetting,
  onResetItemMargins,
  onClose,
}: {
  catalog: CatalogDetail;
  groups: GroupSummary[];
  resetting: boolean;
  onResetItemMargins: () => void;
  onClose: () => void;
}) {
  const qc = useQueryClient();
  const [margin, setMargin] = useState(trimMargin(catalog.bulk_margin_pct));
  const [step, setStep] = useState<RoundingStep>(catalog.rounding_step as RoundingStep);
  const [asDefault, setAsDefault] = useState(true);
  const [err, setErr] = useState<string | null>(null);
  const [byGroup, setByGroup] = useState(false);

  useEffect(() => {
    setMargin(trimMargin(catalog.bulk_margin_pct));
    setStep(catalog.rounding_step as RoundingStep);
  }, [catalog.bulk_margin_pct, catalog.rounding_step]);

  const valid = validMargin(margin);
  const dirty =
    valid && (Number(margin) !== Number(catalog.bulk_margin_pct) || step !== catalog.rounding_step);
  const below = valid && Number(margin) < 0;
  const named = groups.filter((g) => g.category_id !== null);
  const withOwn = named.filter((g) => g.margin_pct !== null).length;

  const refresh = () => {
    qc.invalidateQueries({ queryKey: ["catalog-items", catalog.id] });
    qc.invalidateQueries({ queryKey: ["catalog-groups", catalog.id] });
    qc.invalidateQueries({ queryKey: ["supplier-catalog", catalog.id] });
    qc.invalidateQueries({ queryKey: ["supplier-catalogs"] });
    qc.invalidateQueries({ queryKey: ["prices-preview", catalog.id] });
  };
  const onError = (e: unknown) =>
    setErr(e instanceof ApiError ? e.message : "Could not save prices. Try again.");

  const save = useMutation({
    mutationFn: async () => {
      const body = { bulk_margin_pct: Number(margin).toFixed(2), rounding_step: step };
      const c = await api<CatalogDetail>(`/supplier-catalogs/${catalog.id}`, {
        method: "PATCH",
        body,
      });
      if (asDefault && catalog.supplier_party_id) {
        await api(`/supplier-catalogs/suppliers/${catalog.supplier_party_id}/defaults`, {
          method: "PUT",
          body,
        });
        qc.invalidateQueries({ queryKey: ["supplier-defaults", catalog.supplier_party_id] });
      }
      return c;
    },
    onSuccess: (c) => {
      setErr(null);
      qc.setQueryData(["supplier-catalog", catalog.id], c);
      refresh();
      onClose();
    },
    onError,
  });

  const groupMargin = useMutation({
    mutationFn: (v: { id: string; margin: string }) =>
      api<{ updated: number }>(`/supplier-catalogs/${catalog.id}/groups/${v.id}/margin`, {
        method: "PUT",
        body: { margin_pct: v.margin === "" ? null : Number(v.margin).toFixed(2) },
      }),
    onSuccess: () => {
      setErr(null);
      refresh();
    },
    onError,
  });

  return (
    <div className="border-t border-line p-4" role="region" aria-label="Prices">
      <div className="flex flex-wrap items-end gap-x-5 gap-y-3">
        <div>
          <label className="label" htmlFor="price-margin">
            Margin on supplier prices
          </label>
          <div className="flex items-center gap-2">
            <div className="relative">
              <input
                id="price-margin"
                className="field w-24 pr-7 text-right font-mono tabular-nums"
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
              className={`min-w-[7.5rem] rounded-full px-2.5 py-1 text-center text-xs font-medium ${
                below ? "bg-[#f4e3df] text-danger" : "bg-accent-soft text-accent"
              }`}
              aria-live="polite"
            >
              {valid ? marginExample(Number(margin)) : "—"}
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

        <div className="flex flex-wrap items-center gap-1.5" role="group" aria-label="Common margins">
          {QUICK.map((q) => (
            <button
              key={q}
              type="button"
              aria-pressed={margin === q}
              className={`rounded-full border px-2.5 py-1 font-mono text-xs ${
                margin === q
                  ? "border-accent bg-accent text-white"
                  : "border-line text-muted hover:bg-ground"
              }`}
              onClick={() => setMargin(q)}
            >
              {q}%
            </button>
          ))}
        </div>

        {catalog.supplier_party_id && (
          <label className="flex items-center gap-2 text-sm">
            <input
              type="checkbox"
              checked={asDefault}
              onChange={(e) => setAsDefault(e.target.checked)}
            />
            Use for this supplier&apos;s next price lists
          </label>
        )}

        <div className="ml-auto flex gap-2">
          <button type="button" className="btn-ghost" onClick={onClose}>
            Cancel
          </button>
          <button
            type="button"
            className="btn-primary"
            disabled={!dirty || save.isPending}
            onClick={() => save.mutate()}
          >
            {save.isPending ? "Saving…" : "Save prices"}
          </button>
        </div>
      </div>

      <p className="mt-2 text-xs text-muted">
        New price = supplier price + margin, rounded. Supplier prices are never changed, and your
        items only change when you choose Update prices.
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
          {catalog.item_margin_count === 1 ? " has" : "s have"} their own margin and ignore the list
          and group margins.{" "}
          <button
            type="button"
            className="text-accent underline disabled:opacity-50"
            disabled={resetting}
            onClick={onResetItemMargins}
          >
            Reset them
          </button>
        </p>
      )}

      {named.length > 0 && (
        <div className="mt-3 border-t border-line pt-3">
          <button
            type="button"
            className="text-sm text-accent hover:underline"
            aria-expanded={byGroup}
            onClick={() => setByGroup((v) => !v)}
          >
            {byGroup ? "Hide margin by group" : "Margin by group"}
            {withOwn > 0 && !byGroup && (
              <span className="ml-1 text-muted">({withOwn} set)</span>
            )}
          </button>
          {byGroup && (
            <>
              <p className="mt-1 text-xs text-muted">
                A group&apos;s margin replaces the list margin for its products. Leave it empty to
                follow the list margin ({trimMargin(catalog.bulk_margin_pct)}%).
              </p>
              <ul className="mt-2 grid gap-x-6 sm:grid-cols-2">
                {named.map((g) => (
                  <li
                    key={g.category_id}
                    className="flex items-center gap-2 border-b border-line py-1.5 last:border-0"
                  >
                    <span className="min-w-0 flex-1 truncate text-sm">{g.name}</span>
                    <span className="w-12 text-right text-xs tabular-nums text-muted">
                      {g.item_count}
                    </span>
                    <EditableNumber
                      kind="margin"
                      allowEmpty
                      placeholder={trimMargin(catalog.bulk_margin_pct)}
                      label={`Margin of ${g.name}`}
                      value={g.margin_pct !== null ? trimMargin(g.margin_pct) : ""}
                      onSave={(v) => groupMargin.mutate({ id: g.category_id!, margin: v })}
                      className={`w-16 text-right ${
                        g.margin_pct !== null ? "!bg-[#f1e7d6] border-line font-medium" : ""
                      }`}
                    />
                    <span className="text-xs text-muted" aria-hidden>
                      %
                    </span>
                  </li>
                ))}
              </ul>
            </>
          )}
        </div>
      )}
    </div>
  );
}
