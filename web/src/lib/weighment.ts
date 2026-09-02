/**
 * Editor-side mirror of `api/app/domain/weighment.py`.
 *
 * Derives the physical measures a metal-trade bill carries at the bottom —
 * total weight of weight-priced goods, a piece count of the rest — plus the
 * operator-drawn weighment segments. No money here; tax.py / previewTotal.ts
 * own every rupee.
 *
 * Keep the unit table and the segment grouping identical to the Python side.
 */

export interface MeasureLineInput {
  quantity: string | number;
  uom: string | null;
  segmentNo: number;
}

export interface WeighmentSlip {
  seg: number;
  recorded_kg: string;
}

export interface SegmentMeasure {
  seg: number;
  lineFrom: number; // 1-based
  lineTo: number;
  weightKg: number;
  count: number;
  recordedKg: number | null;
}

export interface Measure {
  totalWeightKg: number;
  totalCount: number;
  segmentCount: number;
  segments: SegmentMeasure[];
}

/** uom (lower, trimmed) -> multiplier to kg. Absent => a piece unit. */
const WEIGHT_UNITS: Record<string, number> = {
  kg: 1,
  kgs: 1,
  kilogram: 1,
  kilograms: 1,
  g: 0.001,
  gm: 0.001,
  gms: 0.001,
  gram: 0.001,
  grams: 0.001,
  quintal: 100,
  qtl: 100,
  ton: 1000,
  tonne: 1000,
  tonnes: 1000,
  mt: 1000,
};

export function isWeightUom(uom: string | null | undefined): boolean {
  return (uom ?? "").trim().toLowerCase() in WEIGHT_UNITS;
}

function num(v: string | number | null | undefined): number {
  if (v === null || v === undefined || v === "") return 0;
  const n = typeof v === "number" ? v : parseFloat(String(v).replace(/,/g, ""));
  return isFinite(n) ? n : 0;
}

/** round half away from zero to 3dp — matches the Python quantize */
function round3(n: number): number {
  return Math.sign(n || 1) * Math.round(Math.abs(n) * 1000) / 1000;
}

export function computeMeasure(
  lines: MeasureLineInput[],
  slips: WeighmentSlip[] = [],
): Measure {
  if (!lines.length) {
    return { totalWeightKg: 0, totalCount: 0, segmentCount: 1, segments: [] };
  }

  const recorded = new Map<number, number>();
  for (const s of slips) {
    const seg = Number(s.seg);
    const kg = num(s.recorded_kg);
    if (Number.isFinite(seg)) recorded.set(seg, kg);
  }

  let totalW = 0;
  let totalC = 0;
  const buckets = new Map<
    number,
    { from: number; to: number; w: number; c: number }
  >();

  lines.forEach((ln, idx) => {
    const i = idx + 1;
    const seg = ln.segmentNo || 1;
    let b = buckets.get(seg);
    if (!b) {
      b = { from: i, to: i, w: 0, c: 0 };
      buckets.set(seg, b);
    }
    b.to = i;
    const factor = WEIGHT_UNITS[(ln.uom ?? "").trim().toLowerCase()];
    if (factor !== undefined) {
      const kg = round3(num(ln.quantity) * factor);
      b.w += kg;
      totalW += kg;
    } else {
      const n = Math.round(num(ln.quantity));
      b.c += n;
      totalC += n;
    }
  });

  const segments: SegmentMeasure[] = [...buckets.entries()]
    .sort((a, b) => a[0] - b[0])
    .map(([seg, b]) => ({
      seg,
      lineFrom: b.from,
      lineTo: b.to,
      weightKg: round3(b.w),
      count: b.c,
      recordedKg: recorded.has(seg) ? recorded.get(seg)! : null,
    }));

  return {
    totalWeightKg: round3(totalW),
    totalCount: totalC,
    segmentCount: segments.length,
    segments,
  };
}

/** "1,234.500 kg" — Indian grouping, 3dp, for display. */
export function kg(value: string | number): string {
  const n = typeof value === "number" ? value : parseFloat(String(value || "0"));
  if (!isFinite(n)) return "0.000 kg";
  const [whole, frac = "000"] = Math.abs(n).toFixed(3).split(".");
  let out = whole;
  if (whole.length > 3) {
    const head = whole.slice(0, -3);
    const tail = whole.slice(-3);
    out = head.replace(/\B(?=(\d{2})+(?!\d))/g, ",") + "," + tail;
  }
  return `${n < 0 ? "−" : ""}${out}.${frac} kg`;
}
