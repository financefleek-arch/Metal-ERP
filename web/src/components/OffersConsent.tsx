import { useMutation, useQueryClient } from "@tanstack/react-query";
import { useState } from "react";
import { api, ApiError } from "../lib/api";
import type { Party, WaConsent } from "../lib/types";

type Subject = Pick<Party, "id" | "phone" | "wa_catalog_consent" | "wa_catalog_consent_at" | "wa_catalog_consent_source">;

const WHERE: Record<string, string> = {
  manual: "at the counter",
  order_page: "on the order page",
  whatsapp_reply: "by replying in WhatsApp",
};

function when(subject: Subject): string {
  const day = subject.wa_catalog_consent_at
    ? new Date(subject.wa_catalog_consent_at).toLocaleDateString("en-IN", { day: "numeric", month: "short" })
    : "";
  const where = subject.wa_catalog_consent_source ? WHERE[subject.wa_catalog_consent_source] : "";
  return [where, day].filter(Boolean).join(", ");
}

/**
 * A customer's answer to "may we send you new ranges and offers on WhatsApp?" and the one tap that records it.
 * `line` sits under the party on the invoice editor (one tick at the counter); `card` is the customer's page in Parties.
 * Staff may record a yes or a no; "not asked" is never set by hand. Offers go only to customers who agreed.
 */
export function OffersConsent({ party, variant }: { party: Subject; variant: "line" | "card" }) {
  const qc = useQueryClient();
  const [err, setErr] = useState<string | null>(null);
  const status: WaConsent = party.wa_catalog_consent ?? "none";

  const set = useMutation({
    mutationFn: (next: Exclude<WaConsent, "none">) =>
      api(`/parties/${party.id}/catalog-consent`, { method: "PUT", body: { status: next } }),
    onSuccess: () => {
      setErr(null);
      qc.invalidateQueries({ queryKey: ["party", party.id] });
      qc.invalidateQueries({ queryKey: ["parties"] });
      qc.invalidateQueries({ queryKey: ["offers-counts"] });
    },
    onError: (e) => setErr(e instanceof ApiError ? e.message : "That did not save. Try again."),
  });

  if (!party.phone) {
    return (
      <p className="mt-2 text-xs text-muted">
        {variant === "card" ? "Offers on WhatsApp: " : ""}Add a phone number to ask about offers.
      </p>
    );
  }

  if (variant === "line") {
    return (
      <div className="mt-2 text-sm">
        {status === "none" && (
          <label className="flex cursor-pointer items-start gap-2">
            <input
              type="checkbox"
              className="mt-1"
              checked={false}
              disabled={set.isPending}
              onChange={() => set.mutate("opted_in")}
            />
            <span>
              Customer agreed to offers on WhatsApp
              <span className="block text-xs text-muted">
                Tick only if they said yes. Offers go only to customers who agreed.
              </span>
            </span>
          </label>
        )}
        {status === "opted_in" && (
          <p className="flex flex-wrap items-center gap-x-2 text-ok">
            <span aria-hidden>✓</span>
            <span className="font-medium">Agreed to offers on WhatsApp</span>
            <span className="text-xs text-muted">{when(party)}</span>
            <button
              type="button"
              className="text-xs text-muted underline"
              disabled={set.isPending}
              title="Records that they did not agree"
              onClick={() => set.mutate("opted_out")}
            >
              Undo
            </button>
          </p>
        )}
        {status === "opted_out" && (
          <p className="flex flex-wrap items-center gap-x-2 text-muted">
            <span>Said no to offers on WhatsApp</span>
            <span className="text-xs">{when(party)}</span>
            <button
              type="button"
              className="text-xs underline"
              disabled={set.isPending}
              onClick={() => set.mutate("opted_in")}
            >
              They agreed now
            </button>
          </p>
        )}
        {err && <p className="err">{err}</p>}
      </div>
    );
  }

  const pill =
    status === "opted_in"
      ? "bg-[#e6efe8] text-ok"
      : status === "opted_out"
        ? "bg-[#f4e3df] text-danger"
        : "bg-[#f1e7d6] text-warn";
  const label = status === "opted_in" ? "Agreed" : status === "opted_out" ? "Said no" : "Not asked";
  return (
    <section className="card mb-4 p-4" aria-label="Offers on WhatsApp">
      <div className="flex items-baseline justify-between gap-3">
        <h3 className="text-sm font-semibold">Offers on WhatsApp</h3>
        <span className={`rounded-full px-2 py-0.5 text-[11px] font-semibold ${pill}`}>{label}</span>
      </div>
      <p className="mt-1 text-xs text-muted">
        {status === "none"
          ? "Not asked yet. New ranges and offers go only to customers who agreed."
          : status === "opted_in"
            ? `Agreed ${when(party)}. They get new ranges and offers on WhatsApp.`
            : `Said no ${when(party)}. They will not be sent offers.`}
      </p>
      <div className="mt-3 flex flex-wrap gap-2">
        <button
          type="button"
          className="btn-ghost h-8 px-3 text-xs"
          disabled={set.isPending || status === "opted_in"}
          onClick={() => set.mutate("opted_in")}
        >
          Customer agreed
        </button>
        <button
          type="button"
          className="btn-ghost h-8 px-3 text-xs"
          disabled={set.isPending || status === "opted_out"}
          onClick={() => set.mutate("opted_out")}
        >
          Customer said no
        </button>
      </div>
      {err && <p className="err">{err}</p>}
    </section>
  );
}
