// AUTO-GENERATED from api/app/domain/units.json by web/scripts/gen-units.mjs — do not edit.
// Run `node scripts/gen-units.mjs` from web/ after changing api/app/domain/units.json.

export interface Unit {
  value: string;
  label: string;
  kind: "weight" | "count";
  kgFactor: number | null;
  pieceMultiplier: number | null;
  isMrp: boolean;
  primary: boolean;
  aliases: string[];
}

export const UNITS: Unit[] = [
  {
    "value": "nos",
    "label": "Pcs",
    "kind": "count",
    "kgFactor": null,
    "pieceMultiplier": 1,
    "isMrp": true,
    "primary": true,
    "aliases": [
      "no",
      "pc",
      "pcs",
      "piece",
      "pieces",
      "each",
      "unit",
      "units",
      "nug"
    ]
  },
  {
    "value": "kg",
    "label": "Kg (weight)",
    "kind": "weight",
    "kgFactor": 1,
    "pieceMultiplier": null,
    "isMrp": false,
    "primary": true,
    "aliases": [
      "kgs",
      "kilo",
      "kilos",
      "kilogram",
      "kilograms"
    ]
  },
  {
    "value": "doz",
    "label": "Dozen (12)",
    "kind": "count",
    "kgFactor": null,
    "pieceMultiplier": 12,
    "isMrp": true,
    "primary": true,
    "aliases": [
      "dz",
      "dozen",
      "dozens"
    ]
  },
  {
    "value": "gross",
    "label": "Gross (144)",
    "kind": "count",
    "kgFactor": null,
    "pieceMultiplier": 144,
    "isMrp": true,
    "primary": true,
    "aliases": [
      "grs",
      "gro",
      "grss"
    ]
  },
  {
    "value": "g",
    "label": "Gram",
    "kind": "weight",
    "kgFactor": 0.001,
    "pieceMultiplier": null,
    "isMrp": false,
    "primary": false,
    "aliases": [
      "gm",
      "gms",
      "gram",
      "grams"
    ]
  },
  {
    "value": "quintal",
    "label": "Quintal (100 kg)",
    "kind": "weight",
    "kgFactor": 100,
    "pieceMultiplier": null,
    "isMrp": false,
    "primary": false,
    "aliases": [
      "qtl",
      "quintals"
    ]
  },
  {
    "value": "mt",
    "label": "Metric tonne",
    "kind": "weight",
    "kgFactor": 1000,
    "pieceMultiplier": null,
    "isMrp": false,
    "primary": false,
    "aliases": [
      "ton",
      "tonne",
      "tonnes",
      "tons"
    ]
  },
  {
    "value": "set",
    "label": "Set",
    "kind": "count",
    "kgFactor": null,
    "pieceMultiplier": 1,
    "isMrp": true,
    "primary": false,
    "aliases": [
      "sets"
    ]
  },
  {
    "value": "pair",
    "label": "Pair",
    "kind": "count",
    "kgFactor": null,
    "pieceMultiplier": 2,
    "isMrp": true,
    "primary": false,
    "aliases": [
      "pairs",
      "pr"
    ]
  },
  {
    "value": "bundle",
    "label": "Bundle",
    "kind": "count",
    "kgFactor": null,
    "pieceMultiplier": 1,
    "isMrp": false,
    "primary": false,
    "aliases": [
      "bdl",
      "bundles",
      "bunch"
    ]
  },
  {
    "value": "coil",
    "label": "Coil",
    "kind": "count",
    "kgFactor": null,
    "pieceMultiplier": 1,
    "isMrp": false,
    "primary": false,
    "aliases": [
      "coils"
    ]
  },
  {
    "value": "sheet",
    "label": "Sheet",
    "kind": "count",
    "kgFactor": null,
    "pieceMultiplier": 1,
    "isMrp": false,
    "primary": false,
    "aliases": [
      "sheets",
      "sht"
    ]
  },
  {
    "value": "length",
    "label": "Length",
    "kind": "count",
    "kgFactor": null,
    "pieceMultiplier": 1,
    "isMrp": false,
    "primary": false,
    "aliases": [
      "lengths",
      "len"
    ]
  },
  {
    "value": "ft",
    "label": "Foot",
    "kind": "count",
    "kgFactor": null,
    "pieceMultiplier": 1,
    "isMrp": false,
    "primary": false,
    "aliases": [
      "feet",
      "foot"
    ]
  },
  {
    "value": "m",
    "label": "Metre",
    "kind": "count",
    "kgFactor": null,
    "pieceMultiplier": 1,
    "isMrp": false,
    "primary": false,
    "aliases": [
      "mtr",
      "mtrs",
      "meter",
      "metre",
      "meters",
      "metres"
    ]
  },
  {
    "value": "sqft",
    "label": "Square foot",
    "kind": "count",
    "kgFactor": null,
    "pieceMultiplier": 1,
    "isMrp": false,
    "primary": false,
    "aliases": [
      "sq ft",
      "sft"
    ]
  },
  {
    "value": "sqm",
    "label": "Square metre",
    "kind": "count",
    "kgFactor": null,
    "pieceMultiplier": 1,
    "isMrp": false,
    "primary": false,
    "aliases": [
      "sq m",
      "sqmtr"
    ]
  },
  {
    "value": "ltr",
    "label": "Litre",
    "kind": "count",
    "kgFactor": null,
    "pieceMultiplier": 1,
    "isMrp": true,
    "primary": false,
    "aliases": [
      "l",
      "lt",
      "lit",
      "litre",
      "litres",
      "liter",
      "liters"
    ]
  },
  {
    "value": "box",
    "label": "Box",
    "kind": "count",
    "kgFactor": null,
    "pieceMultiplier": 1,
    "isMrp": true,
    "primary": false,
    "aliases": [
      "boxes",
      "bx"
    ]
  },
  {
    "value": "pkt",
    "label": "Packet",
    "kind": "count",
    "kgFactor": null,
    "pieceMultiplier": 1,
    "isMrp": true,
    "primary": false,
    "aliases": [
      "packet",
      "packets",
      "pkts"
    ]
  },
  {
    "value": "btl",
    "label": "Bottle",
    "kind": "count",
    "kgFactor": null,
    "pieceMultiplier": 1,
    "isMrp": true,
    "primary": false,
    "aliases": [
      "bottle",
      "bottles",
      "btls"
    ]
  }
];

const BY_KEY: Record<string, Unit> = (() => {
  const m: Record<string, Unit> = {};
  for (const u of UNITS) {
    m[u.value.toLowerCase()] = u;
    for (const a of u.aliases) if (!(a in m)) m[a] = u;
  }
  return m;
})();

function lookup(uom: string | null | undefined): Unit | undefined {
  if (!uom) return undefined;
  return BY_KEY[uom.trim().toLowerCase()];
}

/** Fold a raw unit string to its canonical value. Unknown -> lowercased passthrough. */
export function normalizeUom(uom: string | null | undefined): string {
  if (!uom || !uom.trim()) return "";
  const key = uom.trim().toLowerCase();
  return lookup(key)?.value ?? key;
}

export function isWeightUom(uom: string | null | undefined): boolean {
  return lookup(uom)?.kind === "weight";
}

export function isMrpUom(uom: string | null | undefined): boolean {
  return lookup(uom)?.isMrp === true;
}

/** Individual pieces in one unit (dozen -> 12, gross -> 144). Weight & unknown -> 1. */
export function countMultiplier(uom: string | null | undefined): number {
  return lookup(uom)?.pieceMultiplier ?? 1;
}

/** Quantity in `uom` -> kilograms, or 0 for a non-weight unit. */
export function toKg(quantity: number | string, uom: string | null | undefined): number {
  const u = lookup(uom);
  if (!u || u.kgFactor == null) return 0;
  const q = typeof quantity === "number" ? quantity : parseFloat(String(quantity || 0));
  if (!isFinite(q)) return 0;
  return Math.round(q * u.kgFactor * 1000) / 1000;
}

/** The strict billing set — invoice line + an item's primary unit. */
export const PRIMARY_UOMS: string[] = UNITS.filter((u) => u.primary).map((u) => u.value);

/** The wide list — secondary / purchase unit, and legacy data. */
export const ALL_UOMS: string[] = UNITS.map((u) => u.value);

export const UNIT_LABELS: Record<string, string> = Object.fromEntries(
  UNITS.map((u) => [u.value, u.label]),
);
