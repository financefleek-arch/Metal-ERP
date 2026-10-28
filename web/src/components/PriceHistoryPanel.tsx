import { useQuery } from "@tanstack/react-query";
import { api } from "../lib/api";
import { useDialogKeys } from "../lib/keyboard";
import { billLabel, isStale } from "../lib/priceHistory";
import { uomDisplay } from "../lib/uom";
import { normalizeUom } from "../lib/units.generated";
import type { PriceHistory, PricePoint } from "../lib/types";

/**
 * What this party was billed for this item, newest first — the invoice editor's version of
 * Tally's Stock Query (Alt+S). Click a row to use its rate; Esc closes.
 */
export function PriceHistoryPanel({
  partyId,
  partyName,
  itemId,
  itemName,
  onApply,
  onClose,
}: {
  partyId: string;
  partyName: string;
  itemId: string;
  itemName: string;
  onApply: (p: PricePoint) => void;
  onClose: () => void;
}) {
  useDialogKeys(onClose);
  const q = useQuery({
    queryKey: ["price-history", partyId, itemId],
    queryFn: () =>
      api<PriceHistory>(
        `/invoices/price-history?party_id=${encodeURIComponent(partyId)}&item_id=${encodeURIComponent(itemId)}&limit=10`,
      ),
  });
  const rows = q.data?.rows ?? [];

  return (
    <div
      className="fixed inset-0 z-50 flex items-end justify-center bg-black/30 p-0 sm:items-center sm:p-4"
      onMouseDown={(e) => e.target === e.currentTarget && onClose()}
    >
      <div
        role="dialog"
        aria-label="Previous sales of this item"
        className="max-h-[80vh] w-full overflow-auto rounded-t-lg bg-white p-4 shadow-xl sm:max-w-lg sm:rounded-lg"
      >
        <div className="mb-2 flex items-start justify-between gap-3">
          <div>
            <h3 className="font-serif text-base font-semibold">{itemName}</h3>
            <p className="text-xs text-muted">Billed to {partyName}</p>
          </div>
          <button className="text-muted" onClick={onClose} aria-label="Close">
            ×
          </button>
        </div>
        {q.isLoading ? (
          <p className="py-4 text-sm text-muted">Loading…</p>
        ) : q.isError ? (
          <p className="py-4 text-sm text-danger">Could not load the history.</p>
        ) : rows.length === 0 ? (
          <p className="py-4 text-sm text-muted">No earlier sale of this item to {partyName}.</p>
        ) : (
          <table className="w-full text-sm">
            <thead>
              <tr className="text-left text-[11px] uppercase text-muted">
                <th className="py-1">Date</th>
                <th>Bill</th>
                <th className="text-right">Qty</th>
                <th className="text-right">Rate</th>
                <th className="text-right">Disc</th>
              </tr>
            </thead>
            <tbody>
              {rows.map((p, i) => (
                <tr
                  key={`${p.invoice_id}-${i}`}
                  className="cursor-pointer border-t border-[#f3eee4] hover:bg-[#fbf6ee]"
                  onClick={() => onApply(p)}
                >
                  <td className="py-1.5">
                    {new Date(p.date).toLocaleDateString()}
                    {isStale(p.date) && <span className="ml-1 text-[11px] text-warn">old</span>}
                  </td>
                  <td>
                    {billLabel(p)}
                    {p.source === "tally" && (
                      <span className="ml-1 rounded bg-[#efe9dc] px-1 text-[11px]">Tally</span>
                    )}
                  </td>
                  <td className="text-right font-mono">
                    {Number(p.quantity)} {uomDisplay(normalizeUom(p.uom) || "nos")}
                  </td>
                  <td className="text-right font-mono font-semibold">₹{p.unit_rate}</td>
                  <td className="text-right font-mono text-xs">
                    {p.discount_pct && Number(p.discount_pct) ? `${Number(p.discount_pct)}%` : "—"}
                  </td>
                </tr>
              ))}
            </tbody>
          </table>
        )}
        <p className="mt-3 text-[11px] text-muted">Click a row to use its rate · Esc to close</p>
      </div>
    </div>
  );
}
