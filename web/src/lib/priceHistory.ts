import { normalizeUom } from "./units.generated";
import { uomDisplay } from "./uom";
import type { PricePoint } from "./types";

/** A last price older than this still prefills, with a note — no further staleness logic. */
export const STALE_MONTHS = 6;

export function monthsSince(iso: string, now: Date = new Date()): number {
  const d = new Date(iso);
  if (Number.isNaN(d.getTime())) return 0;
  return (now.getFullYear() - d.getFullYear()) * 12 + (now.getMonth() - d.getMonth());
}

export function isStale(iso: string, now: Date = new Date()): boolean {
  return monthsSince(iso, now) >= STALE_MONTHS;
}

/** Billing number as the shop sees it: "Sales 142", or just the date when it has none (Tally import). */
export function billLabel(p: PricePoint): string {
  return p.number != null ? `${p.series} ${p.number}` : p.series;
}

/** True when the line is priced in the same unit the party was last billed in. */
export function sameUnit(p: PricePoint, rowUom: string): boolean {
  const a = normalizeUom(p.uom);
  const b = normalizeUom(rowUom);
  return !a || !b || a === b;
}

/**
 * The one-line note under a rate: where the prefilled number came from.
 * `itemLast` is the item-wide last rate, shown only as a fallback.
 */
export function priceCaption(opts: {
  partyName: string;
  partyPrice: PricePoint | undefined;
  /** the per-party lookup finished (so "undefined" really means "never sold to them") */
  checked: boolean;
  itemLast: string | null;
  rowUom: string;
}): string {
  const { partyName, partyPrice, checked, itemLast, rowUom } = opts;
  const who = partyName || "this party";
  if (partyPrice) {
    const unit = uomDisplay(normalizeUom(partyPrice.uom) || normalizeUom(rowUom) || "nos");
    let s = `last to ${who} ₹${partyPrice.unit_rate}/${unit} · ${new Date(partyPrice.date).toLocaleDateString()}`;
    if (isStale(partyPrice.date)) s += ` (${monthsSince(partyPrice.date)} months ago)`;
    if (!sameUnit(partyPrice, rowUom)) s += ` · billed per ${unit}, not filled`;
    return s;
  }
  if (checked && partyName) {
    return `no earlier sale to ${who}` + (itemLast ? ` · item last ₹${itemLast}` : "");
  }
  return itemLast ? `item last ₹${itemLast}` : "";
}
