import { useEffect, useState } from "react";
import { useMutation, useQueryClient } from "@tanstack/react-query";
import { api, ApiError } from "../lib/api";
import type { Tenant } from "../lib/types";

/** Firm page: the one ordering setting for the whole shop, shown on every customer catalog page. */
export function OrderSettingsCard({ tenant }: { tenant: Tenant | undefined }) {
  const qc = useQueryClient();
  const [min, setMin] = useState("");
  const [terms, setTerms] = useState("");
  const [alert, setAlert] = useState("");
  const [saved, setSaved] = useState(false);
  const [err, setErr] = useState<string | null>(null);

  useEffect(() => {
    if (!tenant) return;
    setMin(tenant.order_min_value ? String(Number(tenant.order_min_value)) : "");
    setTerms(tenant.order_terms_line ?? "");
    setAlert(tenant.order_alert_phone ?? "");
  }, [tenant]);

  const minOk = min.trim() === "" || /^\d{1,8}(\.\d{1,2})?$/.test(min.trim());
  const dirty =
    !!tenant &&
    (min.trim() !== (tenant.order_min_value ? String(Number(tenant.order_min_value)) : "") ||
      terms.trim() !== (tenant.order_terms_line ?? "") ||
      alert.trim() !== (tenant.order_alert_phone ?? ""));

  const save = useMutation({
    mutationFn: () =>
      api<Tenant>("/tenant", {
        method: "PATCH",
        body: {
          order_min_value: min.trim() === "" ? null : Number(min).toFixed(2),
          order_terms_line: terms.trim() || null,
          order_alert_phone: alert.trim() || null,
        },
      }),
    onSuccess: (t) => {
      setErr(null);
      setSaved(true);
      window.setTimeout(() => setSaved(false), 1800);
      qc.setQueryData(["tenant"], t);
      qc.invalidateQueries({ queryKey: ["tenant"] });
    },
    onError: (e) => setErr(e instanceof ApiError ? e.message : "Could not save. Try again."),
  });

  return (
    <section className="card mt-5 max-w-2xl bg-card p-4" aria-label="Customer orders">
      <h3 className="font-serif text-base font-semibold">Customer catalog page</h3>
      <p className="mt-1 text-xs text-muted">
        Shown on every catalog link you share. Prices are shown before GST, so say so in the line below if you like.
      </p>
      <div className="mt-3 flex flex-wrap items-end gap-4">
        <div>
          <label className="label" htmlFor="om-min">
            Minimum order (₹)
          </label>
          <input
            id="om-min"
            className="field w-32"
            inputMode="decimal"
            placeholder="none"
            value={min}
            onChange={(e) => setMin(e.target.value)}
          />
        </div>
        <div className="min-w-[14rem] flex-1">
          <label className="label" htmlFor="om-terms">
            Terms line
          </label>
          <input
            id="om-terms"
            className="field"
            maxLength={300}
            placeholder="e.g. Prices exclude GST. Payment as agreed."
            value={terms}
            onChange={(e) => setTerms(e.target.value)}
          />
        </div>
        <button type="button" className="btn-primary" disabled={!dirty || !minOk || save.isPending} onClick={() => save.mutate()}>
          {save.isPending ? "Saving…" : saved ? "Saved" : "Save"}
        </button>
      </div>
      <div className="mt-3 max-w-xs">
        <label className="label" htmlFor="om-alert">
          Tell me on WhatsApp when an order arrives
        </label>
        <input
          id="om-alert"
          className="field"
          type="tel"
          inputMode="tel"
          placeholder="your mobile number"
          value={alert}
          onChange={(e) => setAlert(e.target.value)}
        />
        <p className="mt-1 text-xs text-muted">Leave empty for no alert. Customers who tick the WhatsApp box get updates automatically.</p>
      </div>
      {!minOk && (
        <p className="err" role="alert">
          Enter the minimum as a number, for example 2000.
        </p>
      )}
      {err && (
        <p className="err" role="alert">
          {err}
        </p>
      )}
    </section>
  );
}
