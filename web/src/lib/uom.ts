// Human-facing rendering of a unit-of-measure string.
//
// Storage + normalization keep the canonical value from units.json
// (`nos` for pieces). Shop staff and their customers read "pcs", so every
// place a unit is *shown* runs it through here. This is display-only — the
// value written to item.uom / invoice_line.uom is still the canonical one.

import { normalizeUom } from "./units.generated";

const DISPLAY_OVERRIDE: Record<string, string> = {
  nos: "pcs",
};

/** Canonical unit value -> what a person should see. Unknown / other units
 *  pass through unchanged (already short, human-readable strings). */
export function uomDisplay(uom: string | null | undefined): string {
  const v = normalizeUom(uom);
  return DISPLAY_OVERRIDE[v] ?? v;
}
