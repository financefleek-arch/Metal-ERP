// Item availability: a status per item, never a quantity (Tally keeps the stock).
import type { Availability, TallyState } from "./types";

export const AVAILABILITY: { value: Availability; label: string; hint: string }[] = [
  { value: "in_stock", label: "In stock", hint: "You have it. Offered in customer catalogs." },
  { value: "expected", label: "Expected", hint: "On order or on its way. Optional in catalogs." },
  { value: "out_of_stock", label: "Out of stock", hint: "Not available now. Kept out of catalogs." },
  { value: "discontinued", label: "Discontinued", hint: "Not selling it any more." },
];

export function availabilityLabel(a: Availability): string {
  return AVAILABILITY.find((x) => x.value === a)?.label ?? a;
}

/** Tailwind classes for the small status pill shown when an item is not plainly in stock. */
/** Short words for where an item stands with Tally; null = nothing worth showing. */
export function tallyBadge(s: TallyState): { label: string; tone: string; title: string } | null {
  switch (s) {
    case "price_due":
      return { label: "Tally price old", tone: "bg-[#f1e7d6] text-warn", title: "Its price changed since it was sent to Tally. Send it again to update." };
    case "price_differs":
      return { label: "Tally price differs", tone: "bg-[#f1e7d6] text-warn", title: "Tally shows a different price than the one sent. Send it again." };
    case "error":
      return { label: "Tally error", tone: "bg-[#f4dcd8] text-danger", title: "The last send to Tally failed." };
    case "queued":
      return { label: "Sending", tone: "bg-accent-soft text-accent", title: "Waiting to be sent to Tally." };
    case "synced":
      return { label: "In Tally", tone: "bg-[#e6efe8] text-ok", title: "In Tally at your current price." };
    default:
      return null;
  }
}

export function availabilityTone(a: Availability): string {
  switch (a) {
    case "out_of_stock":
      return "bg-[#f1e7d6] text-warn";
    case "expected":
      return "bg-accent-soft text-accent";
    case "discontinued":
      return "bg-[#efe9df] text-muted";
    default:
      return "bg-[#e6efe8] text-ok";
  }
}
