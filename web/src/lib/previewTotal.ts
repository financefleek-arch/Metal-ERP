/**
 * Editor-side mirror of `api/app/domain/tax.py`.
 *
 * Only what the totals rail needs — subtotal, discounts, taxable, round-off,
 * grand total, amount-in-words. The authoritative numbers on finalize always
 * come from the Python side; this is for instant feedback while typing.
 *
 * Kept in lockstep with the backend by the shared vector table at
 * `api/tests/vectors/tax_vectors.json` (a copy lives in `./taxVectors.ts` for
 * a future Vitest run — the project has no JS test runner yet).
 *
 * All arithmetic in integer paise to avoid float drift; commercial rounding
 * (round half away from zero) at each boundary.
 */

export interface PreviewLineInput {
  quantity: string | number;
  unitRate: string | number;
  discount?: string | number;
}

export interface PreviewInput {
  lines: PreviewLineInput[];
  invoiceDiscount?: string | number;
}

export interface PreviewLine {
  lineTotal: string; // "1234.50"
}

export interface PreviewTotals {
  subtotal: string;
  discountTotal: string;
  taxableTotal: string;
  roundOff: string;
  grandTotal: string;
  amountInWords: string;
  lines: PreviewLine[];
}

/** parse a user string / number to integer paise, commercial-rounded. */
function toPaise(v: string | number | undefined): number {
  if (v === undefined || v === null || v === "") return 0;
  const n = typeof v === "number" ? v : parseFloat(String(v).replace(/,/g, ""));
  if (!isFinite(n)) return 0;
  return Math.round(Math.abs(n) * 100) * Math.sign(n === 0 ? 1 : n);
}

/** integer-paise -> "1234.50" */
function fmt(paise: number): string {
  const neg = paise < 0;
  const abs = Math.abs(paise);
  const whole = Math.floor(abs / 100);
  const frac = abs % 100;
  return (neg ? "-" : "") + `${whole}.${String(frac).padStart(2, "0")}`;
}

export function computePreview(input: PreviewInput): PreviewTotals {
  const lines: PreviewLine[] = [];
  let subtotalP = 0;
  let lineDiscP = 0;

  for (const ln of input.lines) {
    const qty = typeof ln.quantity === "number" ? ln.quantity : parseFloat(String(ln.quantity || "0").replace(/,/g, ""));
    const rateP = toPaise(ln.unitRate);
    const discP = Math.abs(toPaise(ln.discount));
    // round(qty * rate) in paise
    const grossP = Math.round((isFinite(qty) ? qty : 0) * rateP);
    let totalP = grossP - discP;
    if (totalP < 0) totalP = 0;
    subtotalP += totalP;
    lineDiscP += discP;
    lines.push({ lineTotal: fmt(totalP) });
  }

  const invDiscP = Math.abs(toPaise(input.invoiceDiscount));
  let taxableP = subtotalP - invDiscP;
  if (taxableP < 0) taxableP = 0;

  // round to whole rupee, half away from zero
  const grandP = Math.round(taxableP / 100) * 100;
  const roundOffP = grandP - taxableP;

  return {
    subtotal: fmt(subtotalP),
    discountTotal: fmt(lineDiscP + invDiscP),
    taxableTotal: fmt(taxableP),
    roundOff: fmt(roundOffP),
    grandTotal: fmt(grandP),
    amountInWords: amountInWords(grandP / 100),
    lines,
  };
}

// --------------------------------------------------------------------------
// amount in words — Indian numbering, matches tax.py::amount_in_words
// --------------------------------------------------------------------------

const ONES = [
  "", "One", "Two", "Three", "Four", "Five", "Six", "Seven", "Eight", "Nine",
  "Ten", "Eleven", "Twelve", "Thirteen", "Fourteen", "Fifteen", "Sixteen",
  "Seventeen", "Eighteen", "Nineteen",
];
const TENS = ["", "", "Twenty", "Thirty", "Forty", "Fifty", "Sixty", "Seventy", "Eighty", "Ninety"];

function two(n: number): string {
  return n < 20 ? ONES[n] : TENS[Math.floor(n / 10)] + (n % 10 ? " " + ONES[n % 10] : "");
}

function three(n: number): string {
  const hundred = Math.floor(n / 100);
  const rest = n % 100;
  const parts: string[] = [];
  if (hundred) parts.push(`${ONES[hundred]} Hundred`);
  if (rest) parts.push(two(rest));
  return parts.join(" ");
}

export function amountInWords(amount: number): string {
  const cents = Math.round(Math.abs(amount) * 100);
  if (amount < 0) return "Minus " + amountInWords(Math.abs(amount));

  let rupees = Math.floor(cents / 100);
  const paise = cents % 100;

  let words: string;
  if (rupees === 0) {
    words = "Zero";
  } else {
    const crore = Math.floor(rupees / 10000000);
    rupees %= 10000000;
    const lakh = Math.floor(rupees / 100000);
    rupees %= 100000;
    const thousand = Math.floor(rupees / 1000);
    const below = rupees % 1000;
    const chunks: string[] = [];
    if (crore) chunks.push(`${two(crore)} Crore`);
    if (lakh) chunks.push(`${two(lakh)} Lakh`);
    if (thousand) chunks.push(`${two(thousand)} Thousand`);
    if (below) chunks.push(three(below));
    words = chunks.join(" ");
  }

  return paise
    ? `INR ${words} and ${two(paise)} Paise Only`
    : `INR ${words} Only`;
}

/** "₹ 1,23,456.78" — Indian grouping, for display. */
export function inr(value: string | number): string {
  const n = typeof value === "number" ? value : parseFloat(String(value || "0"));
  if (!isFinite(n)) return "₹ 0.00";
  const neg = n < 0;
  const abs = Math.abs(n);
  const [whole, frac = "00"] = abs.toFixed(2).split(".");
  let out = whole;
  if (whole.length > 3) {
    const head = whole.slice(0, -3);
    const tail = whole.slice(-3);
    out = head.replace(/\B(?=(\d{2})+(?!\d))/g, ",") + "," + tail;
  }
  return `${neg ? "−" : ""}₹ ${out}.${frac}`;
}
