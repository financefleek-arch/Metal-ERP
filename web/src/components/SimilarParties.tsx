import { useQuery } from "@tanstack/react-query";
import { api } from "../lib/api";
import { lastSeenLabel } from "../lib/format";
import type { PartyMatchRef, PartyResolveResult } from "../lib/types";

/**
 * "Did you mean one of these?" panel for a party create form. Debounced-
 * queries POST /api/parties/resolve as the operator types the name (and
 * fills GSTIN / phone) and lists existing matches so a duplicate is caught
 * before the POST, not after.
 *
 * The parent owns the debounce (pass an already-debounced `name`) and the
 * "create anyway" decision — this component only shows matches and calls
 * `onUse` when the operator picks one.
 */
export function SimilarParties({
  name,
  gstin,
  phone,
  onUse,
  minLen = 3,
}: {
  name: string;
  gstin?: string;
  phone?: string;
  onUse: (p: PartyMatchRef) => void;
  minLen?: number;
}) {
  const q = name.trim();
  const enabled = q.length >= minLen;
  const params = new URLSearchParams({ name: q });
  if (gstin?.trim()) params.set("gstin", gstin.trim());
  if (phone?.trim()) params.set("phone", phone.trim());

  const res = useQuery({
    queryKey: ["party-resolve", q, gstin ?? "", phone ?? ""],
    queryFn: () =>
      api<PartyResolveResult>(`/parties/resolve?${params.toString()}`, { method: "POST" }),
    enabled,
  });

  if (!enabled || res.isLoading) return null;
  const hits = res.data?.candidates ?? [];
  if (hits.length === 0) return null;

  const hard = res.data?.method === "gstin" || res.data?.method === "phone";
  const exact = res.data?.method === "exact";

  return (
    <div className="rounded-md border border-line bg-[#f1e7d6] p-3">
      <p className="text-[13px] font-medium text-warn">
        {hard
          ? `⚠ A party with this ${
              res.data?.method === "gstin" ? "GSTIN" : "phone number"
            } already exists`
          : exact
            ? "⚠ This name is already on your list"
            : `⚠ ${hits.length} similar part${hits.length === 1 ? "y" : "ies"} already exist${
                hits.length === 1 ? "s" : ""
              }`}
      </p>
      <ul className="mt-2 divide-y divide-line/70">
        {hits.map((p) => (
          <li key={p.id} className="flex items-center justify-between gap-3 py-1.5">
            <div className="min-w-0">
              <p className="truncate text-sm font-medium text-ink">{p.legal_name}</p>
              <p className="truncate text-[11px] text-muted">
                {[p.city, p.gstin, lastSeenLabel(p.last_txn_at)].filter(Boolean).join(" · ")}
              </p>
            </div>
            <button
              type="button"
              className="btn-ghost h-7 shrink-0 px-2 text-xs"
              onClick={() => onUse(p)}
            >
              Use this
            </button>
          </li>
        ))}
      </ul>
    </div>
  );
}
