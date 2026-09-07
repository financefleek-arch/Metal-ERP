// Generates web/src/lib/units.generated.ts from shared/units.json.
//
// Runs in web's prebuild (see package.json). The generated file is
// COMMITTED so tsc works offline; a pytest (api/tests/test_units.py)
// fails if it ever drifts from shared/units.json.
//
// The fleek-stack SPA build runs with the Metal-ERP repo ROOT as its
// Docker context (fleek-infra/metalerp/web.Dockerfile does
// `COPY shared/ /shared/`), so units.json is reachable at /shared/ there.
// The two candidate paths cover: a normal repo checkout ("../../shared")
// and that Docker layout ("/shared", i.e. "../.." from web/scripts).
//
//   node scripts/gen-units.mjs

import { existsSync, readFileSync, writeFileSync } from "node:fs";
import { fileURLToPath } from "node:url";
import { dirname, resolve } from "node:path";

const here = dirname(fileURLToPath(import.meta.url));
const outPath = resolve(here, "../src/lib/units.generated.ts");

const candidates = [
  resolve(here, "../../shared/units.json"), // repo checkout, and the Docker /shared/ layout
  resolve(here, "../shared/units.json"), // a copy dropped into web/, if ever needed
];
const srcPath = candidates.find(existsSync);

if (!srcPath) {
  console.error(
    `gen-units: shared/units.json not found (looked in: ${candidates.join(", ")})`,
  );
  process.exit(1);
}

const raw = JSON.parse(readFileSync(srcPath, "utf-8"));

/** @type {Array<Record<string, unknown>>} */
const units = raw.units;

const header = `// AUTO-GENERATED from shared/units.json by web/scripts/gen-units.mjs — do not edit.
// Run \`node scripts/gen-units.mjs\` from web/ after changing shared/units.json.
`;

const body = `
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

export const UNITS: Unit[] = ${JSON.stringify(
  units.map((u) => ({
    value: u.value,
    label: u.label,
    kind: u.kind,
    kgFactor: u.kg_factor,
    pieceMultiplier: u.piece_multiplier,
    isMrp: u.is_mrp,
    primary: u.primary,
    aliases: u.aliases ?? [],
  })),
  null,
  2,
)};

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

/** Quantity in \`uom\` -> kilograms, or 0 for a non-weight unit. */
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
`;

writeFileSync(outPath, header + body, "utf-8");
console.log(`wrote ${outPath} (${units.length} units)`);
