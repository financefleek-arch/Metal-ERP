import { useEffect, useMemo, useState } from "react";
import { useNavigate, useParams } from "react-router-dom";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { api, ApiError } from "../../lib/api";
import { downloadFile } from "../../lib/download";
import { computePreview, inr } from "../../lib/previewTotal";
import { computeMeasure, kg } from "../../lib/weighment";
import { PRIMARY_UOMS, isWeightUom, normalizeUom } from "../../lib/units.generated";
import { uomDisplay } from "../../lib/uom";
import { normalizePhone, phoneError } from "../../lib/reference";
import { lastSeenLabel } from "../../lib/format";
import { PaymentDialog } from "../../components/PaymentDialog";
import { WhatsappLog } from "../../components/WhatsappStatus";
import { SimilarParties } from "../../components/SimilarParties";
import { isDup409 } from "../../lib/types";
import type {
  FinalizeResult,
  Invoice,
  InvoiceLineIn,
  ItemListItem,
  Party,
  PartyDuplicate409,
  PartyLedgerEntry,
  PartyListItem,
  PartyMatchRef,
  PaymentCreate,
  PaymentOut,
  ResolveResult,
  WeighmentSlipIn,
} from "../../lib/types";

type DiscMode = "amt" | "pct";

/** an editor row — string-typed so partial input never NaNs the totals */
interface Row {
  key: string;
  item_id: string | null;
  group_id: string | null;
  description: string;
  hsn_code: string;
  quantity: string;
  uom: string;
  unit_rate: string;
  discount: string;
  discMode: DiscMode;
  /** 1-based weighment segment this line belongs to */
  segmentNo: number;
  /** snapshots from the picked item — for guards + ghost text, not sent */
  _priceMin: string | null;
  _priceMax: string | null;
  _lastRate: string | null;
  _lastSoldAt: string | null;
}

/** desktop line-table column track — shared by the header and every row so
 *  they never drift. Item gets a wide, flexible column; the rest are tight
 *  fixed widths. */
const LINE_GRID =
  "grid-cols-[22px_minmax(180px,1.7fr)_76px_60px_58px_80px_112px_84px_24px]";

/** one segment's physical contents as a short human string:
 *  "128.500 kg", "3 pcs", "96.250 kg · 12 pcs", or "—" when empty. */
function segMeasureText(weightKg: number, count: number): string {
  const parts: string[] = [];
  if (weightKg > 0) parts.push(kg(weightKg));
  if (count > 0) parts.push(`${count} pcs`);
  return parts.length ? parts.join(" · ") : "—";
}

let _rk = 0;
function blankRow(segmentNo = 1): Row {
  return {
    key: `r${++_rk}`,
    item_id: null,
    group_id: null,
    description: "",
    hsn_code: "",
    quantity: "",
    uom: "",
    unit_rate: "",
    discount: "",
    // % is the default discount mode for a fresh line
    discMode: "pct",
    segmentNo,
    _priceMin: null,
    _priceMax: null,
    _lastRate: null,
    _lastSoldAt: null,
  };
}

function rowsFromInvoice(inv: Invoice): Row[] {
  if (!inv.lines.length) return [blankRow()];
  return inv.lines.map((l) => ({
    key: `r${++_rk}`,
    item_id: l.item_id,
    group_id: null,
    description: l.description,
    hsn_code: l.hsn_code ?? "",
    quantity: trimQty(l.quantity),
    uom: normalizeUom(l.uom),
    unit_rate: String(l.unit_rate ?? ""),
    // discount_pct is a persisted UI hint: if the operator originally typed
    // a % it round-trips as that same %, not the computed ₹ figure.
    discount:
      l.discount_pct && Number(l.discount_pct)
        ? String(l.discount_pct)
        : l.discount && Number(l.discount)
          ? String(l.discount)
          : "",
    discMode: l.discount_pct && Number(l.discount_pct) ? "pct" : ("amt" as DiscMode),
    segmentNo: l.segment_no ?? 1,
    _priceMin: null,
    _priceMax: null,
    _lastRate: null,
    _lastSoldAt: null,
  }));
}

/** round half-away-from-zero to 2dp — mirrors the paise rounding in tax.py */
function round2(n: number): number {
  return Math.sign(n) * Math.round(Math.abs(n) * 100) / 100;
}

/** the backend returns quantity at fixed 3dp ("1.000") — trim trailing
 *  zeros on load so a reloaded line reads the same as a freshly-typed one
 *  ("1", not "1.000"), while still allowing real fractional qty ("2.5"). */
function trimQty(v: string | number | null | undefined): string {
  const s = String(v ?? "").trim();
  if (!s || !/^-?\d+(\.\d+)?$/.test(s)) return s;
  return s.includes(".") ? s.replace(/\.?0+$/, "") : s;
}

/** the absolute ₹ discount for a row, resolving a % into an amount */
function rowDiscountAmount(r: Row): number {
  const d = parseFloat(r.discount.replace(/,/g, ""));
  if (!isFinite(d) || d <= 0) return 0;
  if (r.discMode === "amt") return round2(d);
  const qty = parseFloat(r.quantity.replace(/,/g, ""));
  const rate = parseFloat(r.unit_rate.replace(/,/g, ""));
  if (!isFinite(qty) || !isFinite(rate)) return 0;
  const pct = Math.min(d, 100);
  return round2((qty * rate * pct) / 100);
}

/** a per-line warning (amber) or blocker (red) surfaced under the line */
interface LineProblem {
  field: "item" | "qty" | "unit" | "rate" | "disc" | "hsn";
  msg: string;
  block: boolean;
}

/** All the "now" guards, in one place. Pure — takes a Row, returns problems. */
function lineProblems(r: Row): LineProblem[] {
  const out: LineProblem[] = [];
  const qty = parseFloat(r.quantity.replace(/,/g, ""));
  const rate = parseFloat(r.unit_rate.replace(/,/g, ""));
  const hasQty = isFinite(qty) && qty > 0;
  const hasRate = isFinite(rate) && rate > 0;
  const gross = hasQty && hasRate ? qty * rate : 0;

  // item
  if (r.description.trim() && !r.item_id) {
    out.push({ field: "item", block: false, msg: "not in catalogue — a new item will be created" });
  }

  // qty
  if (!hasQty) out.push({ field: "qty", block: true, msg: "needs a quantity" });

  // unit
  if (hasQty && !r.uom.trim())
    out.push({ field: "unit", block: true, msg: "needs a unit" });

  // rate
  if (!hasRate) {
    out.push({ field: "rate", block: true, msg: "needs a rate" });
  } else {
    const lo = r._priceMin != null ? Number(r._priceMin) : null;
    const hi = r._priceMax != null ? Number(r._priceMax) : null;
    if ((lo != null && rate < lo) || (hi != null && rate > hi)) {
      const band =
        lo != null && hi != null ? `₹${lo}–${hi}` : lo != null ? `≥ ₹${lo}` : `≤ ₹${hi}`;
      out.push({ field: "rate", block: false, msg: `rate outside usual ${band}` });
    }
  }

  // discount
  if (r.discount.trim()) {
    const d = parseFloat(r.discount.replace(/,/g, ""));
    if (r.discMode === "pct" && isFinite(d) && d > 100)
      out.push({ field: "disc", block: true, msg: "discount % can't exceed 100" });
    const amt = rowDiscountAmount(r);
    if (gross > 0 && amt >= gross)
      out.push({ field: "disc", block: true, msg: "discount is not less than the line total" });
  }

  // hsn
  const hsn = r.hsn_code.trim();
  if (hsn && !/^\d{4}(\d{2}(\d{2})?)?$/.test(hsn))
    out.push({ field: "hsn", block: false, msg: "HSN should be 4, 6 or 8 digits" });

  return out;
}

function toLineIn(r: Row): InvoiceLineIn {
  const pct = parseFloat(r.discount.replace(/,/g, ""));
  return {
    item_id: r.item_id,
    group_id: r.group_id,
    description: r.description.trim(),
    hsn_code: r.hsn_code.trim() || null,
    quantity: r.quantity.trim() || "0",
    uom: r.uom.trim() || null,
    unit_rate: r.unit_rate.trim() || "0",
    // % is resolved to an absolute amount for billing — the backend line's
    // `discount` (₹) is the only value tax computation reads
    discount: String(rowDiscountAmount(r) || 0),
    // …but the original % is persisted alongside it purely so the row
    // round-trips as a % on reload instead of a converted ₹ figure
    discount_pct: r.discMode === "pct" && isFinite(pct) && pct > 0 ? String(pct) : null,
    size_pos: null,
    segment_no: r.segmentNo || 1,
  };
}

/** the absolute ₹ for the invoice-level discount, resolving a % into an amount */
function invoiceDiscountAmount(
  raw: string,
  mode: DiscMode,
  subtotal: string,
): number {
  const d = parseFloat(raw.replace(/,/g, ""));
  if (!isFinite(d) || d <= 0) return 0;
  if (mode === "amt") return round2(d);
  const sub = parseFloat(String(subtotal).replace(/,/g, ""));
  if (!isFinite(sub)) return 0;
  return round2((sub * Math.min(d, 100)) / 100);
}

export function InvoiceEditorPage() {
  const nav = useNavigate();
  const qc = useQueryClient();
  const { id } = useParams();
  const isNew = !id;

  const detail = useQuery({
    queryKey: ["invoice", id],
    queryFn: () => api<Invoice>(`/invoices/${id}`),
    enabled: !isNew,
  });

  const [partyId, setPartyId] = useState("");
  const [partyLabel, setPartyLabel] = useState("");
  // the picked party's opening-balance state, so the "Bill to" block can show
  // it editable (new client) or as a read-only prior-balance line (locked).
  const [partyOpeningLocked, setPartyOpeningLocked] = useState(false);
  const [date, setDate] = useState(() => new Date().toISOString().slice(0, 10));
  const [notes, setNotes] = useState("");
  const [invoiceDiscount, setInvoiceDiscount] = useState("");
  // % is the default for a fresh invoice; a reloaded ₹ value shows as ₹
  const [invDiscMode, setInvDiscMode] = useState<DiscMode>("pct");
  const [rows, setRows] = useState<Row[]>([blankRow()]);
  /** key of the one mobile line whose editor is expanded (one at a time) */
  const [openKey, setOpenKey] = useState<string | null>(null);
  const [slips, setSlips] = useState<WeighmentSlipIn[]>([]);
  /** segment number a newly added line gets — bumped by "Next segment" */
  const [curSeg, setCurSeg] = useState(1);
  /** the segment whose scale weight the operator is being asked to record */
  const [closingSeg, setClosingSeg] = useState<number | null>(null);
  const [err, setErr] = useState<string | null>(null);
  const [savedNote, setSavedNote] = useState<string | null>(null);
  const [dirty, setDirty] = useState(false);
  const [payingOpen, setPayingOpen] = useState(false);
  const [waOpen, setWaOpen] = useState(false);
  /** cash-and-carry: record a payment right after finalize, no dialog needed
   *  for "full"; "partial" reveals a plain amount field. */
  const [finalizePayMode, setFinalizePayMode] = useState<"none" | "full" | "partial">("none");
  const [finalizePayAmount, setFinalizePayAmount] = useState("");

  const inv = detail.data;
  const finalized = inv?.status === "final";
  const cancelled = inv?.status === "cancelled";
  const readOnly = finalized || cancelled;

  // hydrate from a loaded draft/invoice
  useEffect(() => {
    if (!inv) return;
    setPartyId(inv.party_id ?? "");
    setPartyLabel(inv.party?.legal_name ?? "");
    setDate(inv.date);
    setNotes(inv.notes ?? "");
    setInvoiceDiscount(inv.invoice_discount && Number(inv.invoice_discount) ? inv.invoice_discount : "");
    // reloaded invoice discount is a stored ₹ amount
    setInvDiscMode("amt");
    const loaded = rowsFromInvoice(inv);
    setRows(loaded);
    setOpenKey(null);
    setSlips(
      (inv.measure?.segments ?? [])
        .filter((s) => s.recorded_kg != null)
        .map((s) => ({ seg: s.seg, recorded_kg: String(s.recorded_kg) })),
    );
    setCurSeg(loaded.reduce((m, r) => Math.max(m, r.segmentNo || 1), 1));
    setDirty(false);
  }, [inv]);

  // whenever a party is selected (from a loaded invoice or the picker), fetch
  // its full record so the "Bill to" block knows locked vs. editable.
  const partyRec = useQuery({
    queryKey: ["party", partyId],
    queryFn: () => api<Party>(`/parties/${partyId}`),
    enabled: !!partyId && !readOnly,
  });
  useEffect(() => {
    if (partyRec.data) setPartyOpeningLocked(partyRec.data.opening_balance_locked);
  }, [partyRec.data]);


  // subtotal-only pass so a % invoice discount has a base to resolve against
  const subtotalOnly = useMemo(
    () =>
      computePreview({
        lines: rows
          .filter((r) => r.description.trim())
          .map((r) => ({
            quantity: r.quantity,
            unitRate: r.unit_rate,
            discount: rowDiscountAmount(r),
          })),
      }).subtotal,
    [rows],
  );

  const invDiscAmt = useMemo(
    () => invoiceDiscountAmount(invoiceDiscount, invDiscMode, subtotalOnly),
    [invoiceDiscount, invDiscMode, subtotalOnly],
  );

  const preview = useMemo(
    () =>
      computePreview({
        lines: rows
          .filter((r) => r.description.trim())
          .map((r) => ({
            quantity: r.quantity,
            unitRate: r.unit_rate,
            discount: rowDiscountAmount(r),
          })),
        invoiceDiscount: invDiscAmt,
      }),
    [rows, invDiscAmt],
  );

  // live preview of the finalize-time payment (used by the totals rail) —
  // clamped to the grand total so a typo in "partial" never previews an
  // impossible overpayment against the invoice being drafted.
  const finalizePayPreview = useMemo(() => {
    const grand = Number(preview.grandTotal);
    const amount =
      finalizePayMode === "full"
        ? grand
        : finalizePayMode === "partial"
          ? Math.min(parseFloat(finalizePayAmount.replace(/,/g, "")) || 0, grand)
          : 0;
    return { amount, balanceAfter: Math.max(0, grand - amount) };
  }, [finalizePayMode, finalizePayAmount, preview.grandTotal]);

  const filledRows = rows.filter((r) => r.description.trim());

  // derived weight / count / segments — mirrors the backend measure
  const measure = useMemo(
    () =>
      computeMeasure(
        filledRows.map((r) => ({
          quantity: r.quantity,
          uom: r.uom,
          segmentNo: r.segmentNo || 1,
        })),
        slips,
      ),
    [filledRows, slips],
  );
  // the open segment = the highest segment number carried by a filled line
  const openSeg = filledRows.reduce((m, r) => Math.max(m, r.segmentNo || 1), 1);
  const openSegMeasure = measure.segments.find((s) => s.seg === openSeg);
  const openSegWeight = openSegMeasure?.weightKg ?? 0;
  const openSegCount = openSegMeasure?.count ?? 0;
  const localBlockers: string[] = [];
  if (!partyId) localBlockers.push("select a party");
  if (!filledRows.length) localBlockers.push("add at least one line with an item");
  filledRows.forEach((r) => {
    const n = rows.indexOf(r) + 1;
    const problems = lineProblems(r);
    problems.filter((p) => p.block).forEach((p) => localBlockers.push(`line ${n}: ${p.msg}`));
  });
  // every CLOSED segment (< openSeg) that actually has weighed lines must
  // carry a real recorded scale weight — a blank/zero slip on a real weighed
  // segment is the fat-finger case this guard exists to catch
  measure.segments
    .filter((s) => s.seg < openSeg && s.weightKg > 0)
    .forEach((s) => {
      const recorded = parseFloat(
        slips.find((sl) => sl.seg === s.seg)?.recorded_kg ?? "",
      );
      if (!isFinite(recorded) || recorded <= 0) {
        localBlockers.push(`weighment ${s.seg}: needs a recorded scale weight`);
      }
    });

  const save = useMutation({
    mutationFn: async (): Promise<Invoice> => {
      // renumber segments to a gap-free 1..N over the filled lines, and keep
      // only the slips whose segment still exists
      const filled = rows.filter((r) => r.description.trim());
      const segSeen: number[] = [];
      filled.forEach((r) => {
        if (!segSeen.includes(r.segmentNo || 1)) segSeen.push(r.segmentNo || 1);
      });
      segSeen.sort((a, b) => a - b);
      const remap = new Map(segSeen.map((s, i) => [s, i + 1]));
      const body = {
        party_id: partyId,
        date,
        notes: notes.trim() || null,
        invoice_discount: String(invDiscAmt || 0),
        lines: filled.map((r) => ({
          ...toLineIn(r),
          segment_no: remap.get(r.segmentNo || 1) ?? 1,
        })),
        weighment_slips: slips
          .filter((s) => remap.has(s.seg))
          .map((s) => ({ seg: remap.get(s.seg)!, recorded_kg: s.recorded_kg })),
      };
      if (isNew) return api<Invoice>("/invoices", { method: "POST", body });
      return api<Invoice>(`/invoices/${id}`, { method: "PUT", body });
    },
    onSuccess: (saved) => {
      qc.invalidateQueries({ queryKey: ["invoices"] });
      setDirty(false);
      setSavedNote(
        saved.party_id
          ? "Draft saved."
          : "Draft saved — add a party before you can finalize.",
      );
      if (isNew) nav(`/invoices/${saved.id}`, { replace: true });
      else qc.setQueryData(["invoice", id], saved);
    },
    onError: (e) => setErr(e instanceof ApiError ? e.message : "Save failed"),
  });

  const finalize = useMutation({
    mutationFn: async () => {
      const saved = await save.mutateAsync();
      const result = await api<FinalizeResult>(`/invoices/${saved.id}/finalize`, {
        method: "POST",
      });
      const grand = Number(result.totals.grand_total);
      const payAmount =
        finalizePayMode === "full"
          ? result.totals.grand_total
          : finalizePayMode === "partial"
            ? finalizePayAmount.trim()
            : "";
      const payAmountNum = parseFloat(payAmount.replace(/,/g, ""));

      if (finalizePayMode !== "none" && saved.party_id && payAmountNum > 0) {
        // never let a typo in the partial-amount field allocate more than
        // the invoice actually totals
        const clamped = Math.min(payAmountNum, grand);
        const body: PaymentCreate = {
          party_id: saved.party_id,
          // today, not the invoice's (possibly backdated) date — this
          // records cash received right now, at the counter
          date: new Date().toISOString().slice(0, 10),
          amount: String(clamped),
          mode: "cash",
          ref_no: null,
          notes: null,
          ledger_name: null,
          allocations: [{ invoice_id: saved.id, type: "against_invoice", amount: String(clamped) }],
        };
        await api<PaymentOut>("/payments", { method: "POST", body });
        qc.invalidateQueries({ queryKey: ["collections"] });
        qc.invalidateQueries({ queryKey: ["party-ledger", saved.party_id] });
        // finalize() already rendered the PDF with zero payment recorded —
        // the payment above happens strictly after that render, so without
        // this the very first PDF a "pay at finalize" invoice ever gets is
        // permanently stale (no Amount Received / Balance Due lines) until
        // someone happens to click "Re-render PDF" by hand.
        if (result.pdf_status !== "failed") {
          await api(`/invoices/${saved.id}/rerender`, { method: "POST" }).catch(() => {
            // best-effort: a failed re-render here shouldn't fail the whole
            // finalize+pay action — "Re-render PDF" is still available
          });
        }
      }
      return result;
    },
    onSuccess: () => {
      qc.invalidateQueries({ queryKey: ["invoices"] });
      qc.invalidateQueries({ queryKey: ["invoice", id] });
      detail.refetch();
      if (finalizePayMode === "full") setSavedNote("Finalized — payment recorded in full.");
      else if (finalizePayMode === "partial" && parseFloat(finalizePayAmount) > 0)
        setSavedNote("Finalized — partial payment recorded.");
      setFinalizePayMode("none");
      setFinalizePayAmount("");
    },
    onError: (e) => {
      if (e instanceof ApiError) {
        // 422 detail is a list of blocker strings joined by "; " in api.ts
        setErr(e.message);
      } else setErr("Finalize failed");
    },
  });

  // any fresh edit dismisses the last save confirmation
  useEffect(() => {
    if (dirty) setSavedNote(null);
  }, [dirty]);

  function patchRow(key: string, patch: Partial<Row>) {
    setRows((rs) => rs.map((r) => (r.key === key ? { ...r, ...patch } : r)));
    setDirty(true);
  }
  function addRow() {
    const r = blankRow(curSeg);
    setRows((rs) => [...rs, r]);
    setOpenKey(r.key); // the fresh line is the one you're filling
    setDirty(true);
  }
  function removeRow(key: string) {
    setRows((rs) => {
      const next = rs.filter((r) => r.key !== key);
      return next.length ? next : [blankRow()];
    });
    setOpenKey((k) => (k === key ? null : k));
    setDirty(true);
  }

  /** open the "record scale weight" sheet for the current open segment */
  function startNextSegment() {
    if (!filledRows.length) return;
    setClosingSeg(openSeg);
  }
  /** commit the recorded weight; new lines from here on join segment seg+1 */
  function confirmSegment(recordedKg: string) {
    const seg = closingSeg;
    if (seg == null) return;
    setSlips((ss) => [
      ...ss.filter((s) => s.seg !== seg),
      { seg, recorded_kg: recordedKg || "0" },
    ]);
    // trailing empty rows follow into the new segment
    setRows((rs) => {
      const lastFilledIdx = rs.map((r) => !!r.description.trim()).lastIndexOf(true);
      return rs.map((r, i) => (i > lastFilledIdx ? { ...r, segmentNo: seg + 1 } : r));
    });
    setCurSeg(seg + 1);
    setClosingSeg(null);
    setDirty(true);
  }
  /** overwrite a closed segment's recorded scale weight from its slip divider */
  function editSlip(seg: number, v: string) {
    setSlips((ss) => [
      ...ss.filter((s) => s.seg !== seg),
      { seg, recorded_kg: v || "0" },
    ]);
    setDirty(true);
  }
  /** re-open the last-closed segment: drop its slip, fold its lines back down */
  function reopenLastSegment() {
    if (openSeg <= 1) return;
    const target = openSeg - 1;
    setSlips((ss) => ss.filter((s) => s.seg !== target));
    setRows((rs) =>
      rs.map((r) => ((r.segmentNo || 1) >= openSeg ? { ...r, segmentNo: target } : r)),
    );
    setCurSeg(target);
    setDirty(true);
  }

  function openPdf() {
    if (!id) return;
    // saves as the server's "<Party> <date> <total>.pdf"
    downloadFile(`/invoices/${id}/pdf`, `invoice-${id}.pdf`).catch(() =>
      setErr("PDF not ready."),
    );
  }

  const rerender = useMutation({
    mutationFn: () => api(`/invoices/${id}/rerender`, { method: "POST" }),
    onSuccess: () => detail.refetch(),
    onError: (e) => setErr(e instanceof ApiError ? e.message : "Re-render failed"),
  });

  if (!isNew && detail.isLoading) {
    return <div className="grid h-full place-items-center text-sm text-muted">Loading…</div>;
  }

  const statusLabel = finalized
    ? `Final · #${inv?.number}`
    : cancelled
      ? "Cancelled"
      : "Draft";

  return (
    <div className="mx-auto flex max-w-5xl flex-col gap-4">
      {/* header bar */}
      <div className="flex flex-wrap items-center justify-between gap-2">
        <div className="flex items-center gap-2">
          <button className="text-sm text-accent hover:underline" onClick={() => nav("/invoices")}>
            ← Sales
          </button>
          <h1 className="font-serif text-lg font-semibold">
            {isNew ? "New invoice" : `Invoice`}{" "}
            <span className={finalized ? "text-[#3f7a4f]" : cancelled ? "text-danger" : "text-warn"}>
              · {statusLabel}
            </span>
          </h1>
        </div>
        <div className="flex flex-wrap gap-2">
          {!readOnly && (
            <>
              <button
                className="btn-ghost h-9 px-4 text-sm"
                disabled={!dirty || save.isPending}
                title="Saves everything entered so far — a party can be added later"
                onClick={() => {
                  setErr(null);
                  save.mutate();
                }}
              >
                {save.isPending ? "Saving…" : "Save draft"}
              </button>
              <div className="flex items-center gap-1.5" title="Record a cash payment against this invoice right after it's finalized — for customers who pay on the spot">
                <div className="inline-flex overflow-hidden rounded-md border border-line">
                  {(["none", "full", "partial"] as const).map((m) => (
                    <button
                      key={m}
                      type="button"
                      className={`px-2 py-1.5 text-[11px] font-semibold ${
                        finalizePayMode === m ? "bg-accent text-white" : "bg-card text-muted"
                      }`}
                      onClick={() => setFinalizePayMode(m)}
                    >
                      {m === "none" ? "No payment" : m === "full" ? "Paid in full" : "Partial"}
                    </button>
                  ))}
                </div>
                {finalizePayMode === "partial" && (
                  <input
                    className="field h-9 w-24 text-right text-xs"
                    inputMode="decimal"
                    placeholder="0.00"
                    value={finalizePayAmount}
                    onChange={(e) => setFinalizePayAmount(e.target.value)}
                  />
                )}
              </div>
              <button
                className="btn-primary h-9 px-4 text-sm"
                disabled={localBlockers.length > 0 || finalize.isPending}
                onClick={() => {
                  setErr(null);
                  finalize.mutate();
                }}
              >
                {finalize.isPending
                  ? finalizePayMode !== "none"
                    ? "Finalizing & recording payment…"
                    : "Finalizing…"
                  : "Finalize"}
              </button>
            </>
          )}
          {finalized && (
            <>
              <button className="btn-ghost h-9 px-4 text-sm" onClick={openPdf}>
                Download PDF
              </button>
              <button
                className="btn-ghost h-9 px-4 text-sm"
                onClick={() => setWaOpen(true)}
                disabled={inv?.pdf_status !== "rendered"}
                title={
                  inv?.pdf_status !== "rendered"
                    ? "Re-render the PDF first"
                    : "Send this invoice PDF on WhatsApp"
                }
              >
                Send on WhatsApp
              </button>
              {inv?.pdf_status !== "rendered" && (
                <button
                  className="btn-ghost h-9 px-4 text-sm"
                  onClick={() => rerender.mutate()}
                  disabled={rerender.isPending}
                >
                  {rerender.isPending ? "Rendering…" : "Re-render PDF"}
                </button>
              )}
            </>
          )}
        </div>
      </div>

      {err && <p className="err whitespace-pre-wrap">{err}</p>}
      {savedNote && !err && (
        <p className="rounded-md bg-[#eef3ee] px-3 py-2 text-xs text-ok">{savedNote}</p>
      )}
      {finalized && inv?.pdf_status === "failed" && (
        <p className="rounded-md bg-[#f1e0e0] px-3 py-2 text-xs text-danger">
          PDF render failed on the server. Use “Re-render PDF”.
        </p>
      )}

      {finalized && inv && (
        <WhatsappLog invoiceId={inv.id} onResend={() => setWaOpen(true)} />
      )}

      <div className="grid grid-cols-1 gap-4 lg:grid-cols-[1fr_320px]">
        {/* left: header fields + lines */}
        <div className="flex flex-col gap-4">
          <div className="card grid grid-cols-1 gap-3 p-4 sm:grid-cols-2">
            <div className="sm:col-span-2">
              <label className="label">Bill to (party)</label>
              {readOnly ? (
                <div className="field flex items-center">{partyLabel || "—"}</div>
              ) : (
                <PartyPicker
                  value={partyId}
                  label={partyLabel}
                  onPick={(p) => {
                    setPartyId(p.id);
                    setPartyLabel(p.legal_name);
                    setPartyOpeningLocked(p.opening_balance_locked);
                    setDirty(true);
                  }}
                />
              )}
              {!readOnly && partyId && (
                <PartyOpeningBlock
                  partyId={partyId}
                  locked={partyOpeningLocked}
                  initialOpening={partyRec.data?.opening_balance ?? "0"}
                  initialAsOf={partyRec.data?.opening_balance_as_of ?? ""}
                  onSaved={() => {
                    qc.invalidateQueries({ queryKey: ["party", partyId] });
                    qc.invalidateQueries({ queryKey: ["party-ledger", partyId] });
                  }}
                />
              )}
            </div>
            <div>
              <label className="label">Invoice date</label>
              <input
                type="date"
                className="field"
                value={date}
                disabled={readOnly}
                onChange={(e) => {
                  setDate(e.target.value);
                  setDirty(true);
                }}
              />
            </div>
            <div>
              <label className="label">Number</label>
              <div className="field flex items-center text-muted">
                {inv?.number ?? "auto on finalize"}
              </div>
            </div>
          </div>

          {/* line table */}
          <div className="card overflow-visible">
            <div className={`hidden ${LINE_GRID} gap-2 border-b border-line bg-ground px-3 py-2 text-[10px] font-semibold uppercase text-muted md:grid`}>
              <span>#</span>
              <span>Item</span>
              <span>HSN</span>
              <span className="text-right">Qty</span>
              <span>Unit</span>
              <span className="text-right">Rate</span>
              <span className="text-right">Disc.</span>
              <span className="text-right">Amount</span>
              <span />
            </div>

            {rows.map((r, i) => {
              const fi = filledRows.indexOf(r);
              // a closed segment ends on this line's filled-row position?
              const seg =
                fi >= 0
                  ? measure.segments.find(
                      (s) => s.lineTo === fi + 1 && s.seg < openSeg,
                    )
                  : undefined;
              return (
                <div key={r.key}>
                  <LineRow
                    n={i + 1}
                    row={r}
                    readOnly={readOnly}
                    amount={preview.lines[fi]?.lineTotal}
                    expanded={openKey === r.key || (!r.description.trim() && openKey == null)}
                    onToggle={(want) => setOpenKey(want ? r.key : null)}
                    otherItemLine={
                      r.item_id
                        ? rows.findIndex((x) => x !== r && x.item_id === r.item_id)
                        : -1
                    }
                    onPatch={(p) => patchRow(r.key, p)}
                    onRemove={() => removeRow(r.key)}
                  />
                  {seg && (
                    <SlipDivider
                      seg={seg.seg}
                      lineFrom={seg.lineFrom}
                      lineTo={seg.lineTo}
                      recordedKg={
                        slips.find((s) => s.seg === seg.seg)?.recorded_kg ??
                        String(seg.weightKg)
                      }
                      lineSumKg={seg.weightKg}
                      count={seg.count}
                      readOnly={readOnly}
                      isLastClosed={seg.seg === openSeg - 1}
                      onEdit={(v) => editSlip(seg.seg, v)}
                      onReopen={reopenLastSegment}
                    />
                  )}
                </div>
              );
            })}

            {/* running weight / count bar + Next segment */}
            {!readOnly && filledRows.length > 0 && (
              <div className="border-t border-line bg-accent-soft px-3 py-2 text-xs">
                <div className="flex flex-col gap-1.5 md:flex-row md:flex-wrap md:items-center md:justify-between md:gap-2">
                  <div className="flex flex-col gap-1 md:flex-row md:flex-wrap md:gap-x-4 md:gap-y-1">
                    {openSeg > 1 && (
                      <span className="text-muted">
                        Weighment&nbsp;{openSeg} (open){" "}
                        <b className="font-mono text-ink">
                          {segMeasureText(openSegWeight, openSegCount)}
                        </b>
                      </span>
                    )}
                    <span className="text-muted">
                      Bill so far{" "}
                      <b className="font-mono text-ink">{kg(measure.totalWeightKg)}</b>
                      {" · "}
                      <b className="font-mono text-ink">{measure.totalCount} pcs</b>
                    </span>
                  </div>
                  <button
                    type="button"
                    className="w-full rounded-md border border-ok px-3 py-2 text-xs font-semibold text-ok hover:bg-[#eef3ee] md:w-auto md:py-1"
                    onClick={startNextSegment}
                  >
                    Next segment ›
                  </button>
                </div>
              </div>
            )}

            {!readOnly && (
              <div className="p-3">
                {/* mobile: a real button, not a whisper */}
                <button
                  className="flex w-full items-center justify-center gap-2 rounded-lg border-[1.5px] border-dashed border-accent bg-accent-soft py-3 text-[15px] font-semibold text-accent-dark md:hidden"
                  onClick={addRow}
                >
                  <span className="text-xl leading-none">＋</span> Add another item
                </button>
                <button
                  className="hidden text-sm text-accent hover:underline md:inline"
                  onClick={addRow}
                >
                  + Add line
                </button>
              </div>
            )}
          </div>

          <div className="card p-4">
            <label className="label">Notes on invoice</label>
            <textarea
              className="field h-auto min-h-[56px] py-2"
              placeholder="Optional note printed under the line table…"
              value={notes}
              disabled={readOnly}
              onChange={(e) => {
                setNotes(e.target.value);
                setDirty(true);
              }}
            />
          </div>
        </div>

        {/* right: totals rail */}
        <div className="card h-fit p-4">
          <h2 className="font-serif text-sm font-semibold">Totals</h2>
          <div className="mt-3 flex flex-col gap-2 text-sm">
            <Row2 label="Subtotal" value={inr(preview.subtotal)} />
            <div className="flex items-center justify-between gap-2">
              <span className="text-muted">Invoice discount</span>
              <span className="flex items-center gap-1.5">
                <span className="inline-flex overflow-hidden rounded border border-line">
                  {(["amt", "pct"] as DiscMode[]).map((m) => (
                    <button
                      key={m}
                      type="button"
                      disabled={readOnly}
                      className={`px-1.5 text-[9px] font-bold ${
                        invDiscMode === m ? "bg-accent text-white" : "bg-card text-muted"
                      }`}
                      onClick={() => {
                        setInvDiscMode(m);
                        setDirty(true);
                      }}
                    >
                      {m === "amt" ? "₹" : "%"}
                    </button>
                  ))}
                </span>
                <input
                  className="field h-8 w-20 text-right font-mono text-xs"
                  inputMode="decimal"
                  placeholder={invDiscMode === "pct" ? "0" : "0.00"}
                  value={invoiceDiscount}
                  disabled={readOnly}
                  onChange={(e) => {
                    setInvoiceDiscount(e.target.value);
                    setDirty(true);
                  }}
                />
              </span>
            </div>
            {invDiscMode === "pct" && invDiscAmt > 0 && (
              <div className="flex justify-end text-[10px] text-muted">
                = − {inr(invDiscAmt)}
              </div>
            )}
            {Number(preview.discountTotal) > 0 && (
              <Row2 label="Total discount" value={`− ${inr(preview.discountTotal)}`} muted />
            )}
            <Row2 label="Round off" value={inr(preview.roundOff)} muted />
            <div className="my-1 border-t border-line" />
            <div className="flex items-center justify-between font-serif text-base font-semibold">
              <span>Grand total</span>
              <span>{inr(preview.grandTotal)}</span>
            </div>
            <p className="min-h-[28px] text-[11px] leading-snug text-muted">
              {Number(preview.grandTotal) > 0 ? preview.amountInWords : ""}
            </p>

            {!readOnly && finalizePayMode !== "none" && (
              <div className="mt-1 rounded-md border border-dashed border-accent bg-accent-soft p-2.5">
                <div className="flex items-center justify-between text-xs">
                  <span className="text-muted">
                    Payment on finalize {finalizePayMode === "partial" && "(partial)"}
                  </span>
                  <span className="font-semibold text-accent">
                    {inr(finalizePayPreview.amount)}
                  </span>
                </div>
                <div className="mt-0.5 flex items-center justify-between text-xs">
                  <span className="text-muted">Balance after payment</span>
                  <span className="font-semibold">{inr(finalizePayPreview.balanceAfter)}</span>
                </div>
                <p className="mt-1.5 text-[10px] leading-snug text-accent-dark">
                  Only recorded when you Finalize — “Save draft” does not record this payment.
                </p>
              </div>
            )}
          </div>

          {/* weighment — always shown once there's a line */}
          {filledRows.length > 0 && (
            <div className="mt-4 rounded-md border border-[#c9ddc9] bg-[#eef3ee] p-3 text-xs">
              <div className="mb-1.5 text-[10px] font-semibold uppercase tracking-wide text-ok">
                Weighment
              </div>
              <div className="flex justify-between py-0.5">
                <span className="text-muted">Total weight</span>
                <b className="font-mono">{kg(measure.totalWeightKg)}</b>
              </div>
              <div className="flex justify-between py-0.5">
                <span className="text-muted">Piece count</span>
                <b className="font-mono">{measure.totalCount} pcs</b>
              </div>
              {measure.segments.length > 1 && (
                <div className="mt-1.5 border-t border-dashed border-[#c9ddc9] pt-1.5">
                  {measure.segments.map((s) => (
                    <div key={s.seg} className="flex justify-between py-0.5 text-[11px] text-muted">
                      <span>
                        Weighment {s.seg} · lines {s.lineFrom}–{s.lineTo}
                      </span>
                      <span className="font-mono">
                        {s.recordedKg != null
                          ? kg(s.recordedKg)
                          : segMeasureText(s.weightKg, s.count)}
                      </span>
                    </div>
                  ))}
                </div>
              )}
              {measure.segments.length <= 1 && (
                <div className="mt-1 text-[10px] text-muted">1 weighment</div>
              )}
            </div>
          )}

          {finalized && (
            <div className="mt-4 rounded-md border border-line bg-card p-3">
              <div className="label mb-2">Payment</div>
              <div className="flex items-center justify-between">
                <div>
                  <div className="text-[11px] text-muted">Paid so far</div>
                  <div className="font-serif text-base font-semibold">
                    {inv?.paid_amount ? inr(inv.paid_amount) : inr(0)}
                  </div>
                </div>
                <div className="text-right">
                  <div className="text-[11px] text-muted">Balance due</div>
                  <div
                    className={`font-serif text-base font-semibold ${
                      Number(inv?.balance_due ?? 0) > 0 ? "text-danger" : ""
                    }`}
                  >
                    {inv?.balance_due ? inr(inv.balance_due) : inr(0)}
                  </div>
                </div>
              </div>
              <button
                className="btn-ghost mt-3 h-9 w-full text-xs"
                onClick={() => setPayingOpen(true)}
              >
                + Record payment
              </button>
            </div>
          )}

          {!readOnly && (
            <div className="mt-4 rounded-md border border-line bg-ground p-3 text-xs">
              <div className="font-semibold">
                {localBlockers.length ? "Blocking finalize" : "Ready to finalize"}
              </div>
              <ul className="mt-1 space-y-0.5 text-muted">
                {localBlockers.length ? (
                  localBlockers.map((b, i) => <li key={i}>• {b}</li>)
                ) : (
                  <li>
                    {filledRows.length} line{filledRows.length === 1 ? "" : "s"} · party selected
                  </li>
                )}
              </ul>
            </div>
          )}

          <p className="mt-3 text-[11px] leading-snug text-muted">
            GST off · template v1-nongst. No CGST/SGST, IRN or HSN summary.
          </p>
        </div>
      </div>

      {closingSeg != null && (
        <CloseSegmentDialog
          seg={closingSeg}
          lineSumKg={openSegWeight}
          lineCount={openSegCount}
          onCancel={() => setClosingSeg(null)}
          onConfirm={confirmSegment}
        />
      )}

      {payingOpen && inv?.party_id && (
        <PaymentDialog
          partyId={inv.party_id}
          partyName={inv.party?.legal_name ?? partyLabel}
          focusInvoiceId={inv.id}
          onClose={() => setPayingOpen(false)}
          onSaved={() => {
            setPayingOpen(false);
            detail.refetch();
          }}
        />
      )}

      {waOpen && inv && (
        <WhatsappSendDialog
          invoiceId={inv.id}
          invoiceNumber={inv.number}
          partyId={inv.party?.id ?? null}
          partyName={inv.party?.legal_name ?? null}
          partyPhone={inv.party?.phone ?? null}
          onClose={() => setWaOpen(false)}
          onPartyPhoneSaved={() => detail.refetch()}
        />
      )}
    </div>
  );
}

// --------------------------------------------------------------------------
// send the finalized invoice PDF on WhatsApp (invoice_ready template).
// Two independent targets: the party's own number (when it has one) and/or
// any other number typed in. Either or both.
// --------------------------------------------------------------------------

function WhatsappSendDialog({
  invoiceId,
  invoiceNumber,
  partyId,
  partyName,
  partyPhone,
  onClose,
  onPartyPhoneSaved,
}: {
  invoiceId: string;
  invoiceNumber: number | null;
  partyId: string | null;
  partyName: string | null;
  partyPhone: string | null;
  onClose: () => void;
  onPartyPhoneSaved?: () => void;
}) {
  const qc = useQueryClient();
  const [toParty, setToParty] = useState(!!partyPhone);
  const [otherPhone, setOtherPhone] = useState("");
  const [otherTouched, setOtherTouched] = useState(false);
  const [busy, setBusy] = useState(false);
  const [results, setResults] = useState<{ label: string; ok: boolean; msg: string }[]>([]);
  const [saveState, setSaveState] = useState<"idle" | "saving" | "saved">("idle");

  const sendOne = (toPhone: string) =>
    api<{ status: string; wa_message_id: string | null; error: string | null }>(
      `/invoices/${invoiceId}/whatsapp`,
      { method: "POST", body: { to_phone: toPhone } },
    );

  const otherNorm = normalizePhone(otherPhone); // string | null
  const otherValid = otherNorm !== null;
  const targets: { label: string; phone: string }[] = [];
  if (toParty && partyPhone) targets.push({ label: partyName ?? "Party", phone: partyPhone });
  if (otherValid) targets.push({ label: "Other number", phone: otherNorm });

  const canSend = targets.length > 0 && !busy;
  const allDone = results.length > 0 && results.every((r) => r.ok);

  // After a good send to a typed number, offer to keep it on the party —
  // only when it differs from what the party already has.
  const otherSentOk =
    otherValid && results.some((r) => r.label === "Other number" && r.ok);
  const offerSave =
    !!partyId &&
    otherSentOk &&
    saveState !== "saved" &&
    normalizePhone(partyPhone ?? "") !== otherNorm;

  async function saveToParty() {
    if (!partyId || !otherNorm) return;
    setSaveState("saving");
    try {
      await api(`/parties/${partyId}`, {
        method: "PATCH",
        body: { phone: otherNorm },
      });
      setSaveState("saved");
      onPartyPhoneSaved?.();
    } catch {
      setSaveState("idle");
    }
  }

  async function run() {
    setBusy(true);
    setResults([]);
    const out: { label: string; ok: boolean; msg: string }[] = [];
    for (const t of targets) {
      try {
        const r = await sendOne(t.phone);
        out.push({
          label: t.label,
          ok: true,
          msg: r.wa_message_id ? "sent" : `sent (status "${r.status}")`,
        });
      } catch (e) {
        out.push({
          label: t.label,
          ok: false,
          msg: e instanceof ApiError ? e.message : "send failed",
        });
      }
      setResults([...out]);
    }
    setBusy(false);
    if (out.some((r) => r.ok)) {
      qc.invalidateQueries({ queryKey: ["invoice-whatsapp", invoiceId] });
      qc.invalidateQueries({ queryKey: ["invoices"] });
    }
  }

  return (
    <div
      className="fixed inset-0 z-50 grid place-items-center bg-black/40 p-4"
      onClick={onClose}
    >
      <div
        className="w-full max-w-sm rounded-xl bg-card p-5 shadow-xl"
        onClick={(e) => e.stopPropagation()}
      >
        <h2 className="font-serif text-lg font-semibold">
          Send invoice{invoiceNumber ? ` #${invoiceNumber}` : ""} on WhatsApp
        </h2>
        <p className="mt-1 text-xs text-muted">
          Sends the invoice PDF using the <code>invoice_ready</code> template
          from this firm's WhatsApp number.
        </p>

        {partyPhone && (
          <label className="mt-4 flex items-center gap-2 text-sm">
            <input
              type="checkbox"
              checked={toParty}
              onChange={(e) => setToParty(e.target.checked)}
            />
            <span>
              Send to {partyName ?? "the party"}{" "}
              <span className="text-muted">({partyPhone})</span>
            </span>
          </label>
        )}

        <label className="label mt-4">
          {partyPhone ? "Also send to another number" : "Recipient WhatsApp number"}
        </label>
        <input
          className="field"
          autoFocus={!partyPhone}
          value={otherPhone}
          onChange={(e) => setOtherPhone(e.target.value)}
          onBlur={() => {
            setOtherTouched(true);
            if (otherNorm && otherNorm !== otherPhone) setOtherPhone(otherNorm);
          }}
          placeholder="98765 43210"
          inputMode="tel"
        />
        {otherTouched && otherPhone.trim() && !otherValid ? (
          <p className="err mt-1">
            Enter a 10-digit mobile, or +&lt;country code&gt;&lt;number&gt;.
          </p>
        ) : (
          <p className="mt-1 text-[11px] text-muted">
            Paste a number from Contacts — a leading +91 or spaces are fine.
            Leave blank to skip.
          </p>
        )}

        {results.length > 0 && (
          <ul className="mt-3 space-y-1 text-xs">
            {results.map((r, i) => (
              <li
                key={i}
                className={r.ok ? "text-[#3f7a4f]" : "text-danger"}
              >
                {r.label}: {r.msg}
              </li>
            ))}
          </ul>
        )}

        {offerSave && (
          <div className="mt-3 flex items-center justify-between gap-2 rounded-md bg-[#f0f6f8] px-3 py-2 text-xs">
            <span>
              Save <span className="font-medium">{otherNorm}</span> to{" "}
              {partyName ?? "this party"}?
            </span>
            <button
              className="btn-ghost h-7 shrink-0 px-3 text-xs"
              disabled={saveState === "saving"}
              onClick={saveToParty}
            >
              {saveState === "saving" ? "Saving…" : "Save"}
            </button>
          </div>
        )}
        {saveState === "saved" && (
          <p className="mt-2 text-xs text-[#3f7a4f]">
            Saved to {partyName ?? "the party"}.
          </p>
        )}

        <div className="mt-5 flex justify-end gap-2">
          <button className="btn-ghost h-9 px-4 text-sm" onClick={onClose}>
            {allDone ? "Close" : "Cancel"}
          </button>
          {!allDone && (
            <button
              className="btn-primary h-9 px-4 text-sm"
              disabled={!canSend}
              onClick={run}
            >
              {busy
                ? "Sending…"
                : targets.length > 1
                  ? `Send to ${targets.length}`
                  : "Send"}
            </button>
          )}
        </div>
      </div>
    </div>
  );
}

// --------------------------------------------------------------------------
// weighment — slip divider + "record scale weight" dialog
// --------------------------------------------------------------------------

function SlipDivider({
  seg,
  lineFrom,
  lineTo,
  recordedKg,
  lineSumKg,
  count,
  readOnly,
  isLastClosed,
  onEdit,
  onReopen,
}: {
  seg: number;
  lineFrom: number;
  lineTo: number;
  recordedKg: string;
  lineSumKg: number;
  count: number;
  readOnly: boolean;
  isLastClosed: boolean;
  onEdit: (v: string) => void;
  onReopen: () => void;
}) {
  const drift = Number(recordedKg) - lineSumKg;
  // same "blank/zero against real weighed lines" guard as the close-segment
  // dialog — this field can silently overwrite a valid recorded weight
  const needsWeight = lineSumKg > 0;
  const recordedNum = parseFloat(recordedKg);
  const invalid = needsWeight && (!recordedKg.trim() || !isFinite(recordedNum) || recordedNum <= 0);
  // a piece-only closed segment has no scale weight to record — show the
  // piece count instead of a 0 kg field + a false "can't be blank" error
  const pieceOnly = !needsWeight;
  return (
    <div className="flex flex-wrap items-center gap-x-3 gap-y-1 border-y border-[#c9ddc9] bg-[#eef3ee] px-3 py-1.5">
      <span className="flex items-center gap-1.5 text-[11px] font-semibold uppercase tracking-wide text-ok">
        <svg className="h-3 w-3" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2" strokeLinecap="round" strokeLinejoin="round">
          <path d="M12 3v18M5 21h14M6 7h12l3 7a4 4 0 01-8 0zM6 7L3 14a4 4 0 008 0" />
        </svg>
        Weighment {seg}
      </span>
      {pieceOnly ? (
        <span className="font-mono text-xs font-semibold text-ink">
          {count > 0 ? `${count} pcs` : "—"}
        </span>
      ) : readOnly ? (
        <span className="font-mono text-xs font-semibold text-ink">{kg(recordedKg)}</span>
      ) : (
        <span className="flex items-center gap-1">
          <input
            className={`field h-7 w-24 text-right font-mono text-xs ${invalid ? "border-danger focus:border-danger" : ""}`}
            inputMode="decimal"
            value={recordedKg}
            onChange={(e) => onEdit(e.target.value)}
          />
          <span className="text-[10px] text-muted">kg</span>
        </span>
      )}
      <span className="text-[11px] text-muted">
        lines {lineFrom}–{lineTo}
        {!pieceOnly && count > 0 && ` · ${count} pcs`}
      </span>
      {invalid ? (
        <span className="text-[10px] font-semibold text-danger">
          can't be blank/zero — this segment has weighed lines
        </span>
      ) : (
        !pieceOnly &&
        Math.abs(drift) >= 0.005 && (
          <span className="text-[10px] text-warn">
            {drift > 0 ? "+" : "−"}
            {Math.abs(drift).toFixed(2)} kg vs line sum
          </span>
        )
      )}
      {!readOnly && isLastClosed && (
        <button
          type="button"
          className="ml-auto text-[10px] text-accent hover:underline"
          onClick={onReopen}
        >
          re-open
        </button>
      )}
    </div>
  );
}

function CloseSegmentDialog({
  seg,
  lineSumKg,
  lineCount,
  onCancel,
  onConfirm,
}: {
  seg: number;
  lineSumKg: number;
  lineCount: number;
  onCancel: () => void;
  onConfirm: (recordedKg: string) => void;
}) {
  const [val, setVal] = useState(lineSumKg ? String(lineSumKg) : "");
  const [touched, setTouched] = useState(false);
  const drift = (parseFloat(val) || 0) - lineSumKg;
  // this segment has real kg-bearing lines (weightKg only accumulates from
  // kg-uom lines) — a blank/zero scale reading against them is exactly the
  // fat-finger case reported, so block it. A piece-only segment (lineSumKg
  // === 0 by construction) has nothing to weigh and is never blocked.
  const needsWeight = lineSumKg > 0;
  const parsed = parseFloat(val);
  const invalid = needsWeight && (!val.trim() || !isFinite(parsed) || parsed <= 0);
  return (
    <div
      className="fixed inset-0 z-50 grid place-items-center bg-black/30 p-4"
      onClick={onCancel}
    >
      <div
        className="w-full max-w-sm rounded-lg border border-line bg-card p-4 shadow-xl"
        onClick={(e) => e.stopPropagation()}
      >
        <h3 className="font-serif text-sm font-semibold">Close weighment {seg}</h3>
        {needsWeight ? (
          <p className="mt-1 text-xs text-muted">
            Sum of line weights in this segment:{" "}
            <b className="text-ink">{kg(lineSumKg)}</b>
          </p>
        ) : (
          <p className="mt-1 text-xs text-muted">
            In this weighment:{" "}
            <b className="text-ink">{lineCount > 0 ? `${lineCount} pcs` : "no lines"}</b>{" "}
            — no weighed lines, scale weight optional.
          </p>
        )}
        <label className="label mt-3 block">
          {needsWeight
            ? "Weight shown on the platform scale"
            : "Weight shown on the platform scale (optional)"}
        </label>
        <input
          className={`field text-right font-mono ${invalid && touched ? "border-danger focus:border-danger" : ""}`}
          inputMode="decimal"
          autoFocus
          value={val}
          placeholder="0.000"
          onChange={(e) => setVal(e.target.value)}
          onBlur={() => setTouched(true)}
          onKeyDown={(e) => {
            if (e.key === "Enter" && !invalid) onConfirm(val);
          }}
        />
        {invalid && touched && (
          <p className="mt-1 text-[11px] text-danger">
            enter the scale weight — it can't be blank or zero for a weighed segment
          </p>
        )}
        {!invalid && Math.abs(drift) >= 0.005 && (
          <p className="mt-1 text-[11px] text-warn">
            {drift > 0 ? "+" : "−"}
            {Math.abs(drift).toFixed(2)} kg vs line sum — recorded as-is on the slip
          </p>
        )}
        <div className="mt-4 flex gap-2">
          <button
            className="btn-ghost h-9 flex-1 px-4 text-sm"
            onClick={onCancel}
          >
            Cancel
          </button>
          <button
            className="h-9 flex-1 rounded-md bg-ok px-4 text-sm font-semibold text-white disabled:cursor-not-allowed disabled:opacity-50"
            disabled={invalid}
            onClick={() => {
              setTouched(true);
              if (!invalid) onConfirm(val);
            }}
          >
            Close &amp; start seg {seg + 1}
          </button>
        </div>
      </div>
    </div>
  );
}

function Row2({ label, value, muted }: { label: string; value: string; muted?: boolean }) {
  return (
    <div className="flex items-center justify-between">
      <span className="text-muted">{label}</span>
      <span className={muted ? "text-muted" : ""}>{value}</span>
    </div>
  );
}

// --------------------------------------------------------------------------
// opening / prior balance block under "Bill to"
//   - new client (unlocked): an editable ₹ field + as-of date, PATCHed onto
//     the party on blur. A failed save shows inline and never blocks the
//     invoice.
//   - party with history (locked): a read-only "outstanding before this
//     invoice" line, from the cached party ledger.
// --------------------------------------------------------------------------

function PartyOpeningBlock({
  partyId,
  locked,
  initialOpening,
  initialAsOf,
  onSaved,
}: {
  partyId: string;
  locked: boolean;
  initialOpening: string;
  initialAsOf: string;
  onSaved: () => void;
}) {
  const [amt, setAmt] = useState(initialOpening);
  const [asOf, setAsOf] = useState(initialAsOf);
  const [err, setErr] = useState<string | null>(null);
  const [saved, setSaved] = useState(false);
  // reset when a different party is picked
  useEffect(() => {
    setAmt(initialOpening);
    setAsOf(initialAsOf);
    setErr(null);
    setSaved(false);
  }, [partyId, initialOpening, initialAsOf]);

  const ledger = useQuery({
    queryKey: ["party-ledger", partyId],
    queryFn: () => api<PartyLedgerEntry[]>(`/parties/${partyId}/ledger`),
    enabled: locked,
  });

  const save = useMutation({
    mutationFn: (body: { opening_balance: string; opening_balance_as_of: string | null }) =>
      api<Party>(`/parties/${partyId}`, { method: "PATCH", body }),
    onSuccess: () => {
      setErr(null);
      setSaved(true);
      onSaved();
    },
    onError: (e) => setErr(e instanceof ApiError ? e.message : "Could not save opening balance"),
  });

  function commit() {
    const norm = amt.trim() === "" ? "0" : amt.trim();
    if (norm === (initialOpening || "0") && (asOf || "") === (initialAsOf || "")) return;
    save.mutate({ opening_balance: norm, opening_balance_as_of: asOf || null });
  }

  if (locked) {
    const entries = ledger.data ?? [];
    const bal = Number(entries[0]?.running_balance ?? "0");
    const opening = entries.find((e) => e.kind === "opening");
    const openingAmt = opening ? Number(opening.debit) - Number(opening.credit) : 0;
    const invCount = entries.filter((e) => e.kind === "invoice").length;
    if (ledger.isLoading) return null;
    return (
      <p className="mt-2 rounded-md bg-ground px-3 py-2 text-xs text-muted">
        {bal === 0 ? (
          <>Outstanding before this invoice: <b className="text-ink">{inr(0)}</b> · all settled</>
        ) : (
          <>
            Outstanding before this invoice:{" "}
            <b className={bal > 0 ? "text-danger" : "text-ok"}>{inr(Math.abs(bal))}</b>
            {bal < 0 && " credit"}
            {openingAmt !== 0 && ` · incl. ${inr(Math.abs(openingAmt))} opening`}
            {invCount > 0 && ` · ${invCount} invoice${invCount === 1 ? "" : "s"}`}
          </>
        )}
      </p>
    );
  }

  return (
    <div className="mt-2 rounded-md border border-line bg-ground/60 p-3">
      <label className="label mb-1">Opening balance (before this bill)</label>
      <div className="flex flex-wrap items-start gap-2">
        <div className="relative w-40">
          <span className="pointer-events-none absolute left-3 top-1/2 -translate-y-1/2 text-sm text-muted">
            ₹
          </span>
          <input
            className="field pl-7"
            inputMode="decimal"
            placeholder="0.00"
            value={amt}
            onChange={(e) => {
              setAmt(e.target.value);
              setSaved(false);
            }}
            onBlur={commit}
          />
        </div>
        <input
          type="date"
          className="field w-44"
          value={asOf}
          onChange={(e) => {
            setAsOf(e.target.value);
            setSaved(false);
          }}
          onBlur={commit}
        />
      </div>
      {err ? (
        <p className="err">{err}</p>
      ) : saved ? (
        <p className="mt-1 text-[11px] text-ok">Saved to the party</p>
      ) : (
        <p className="mt-1 text-[11px] text-muted">
          ↳ new client · what they owed you before you started billing here. Leave 0 if none.
        </p>
      )}
    </div>
  );
}

// --------------------------------------------------------------------------
// party picker — async search of /api/parties
// --------------------------------------------------------------------------

function PartyPicker({
  value,
  label,
  onPick,
}: {
  value: string;
  label: string;
  onPick: (p: PartyListItem) => void;
}) {
  const [q, setQ] = useState("");
  const [open, setOpen] = useState(false);
  const [creating, setCreating] = useState(false);
  const debQ = useDebounced(q.trim(), 250);
  // No role filter: a firm already on file as a *supplier* must surface here
  // so the operator picks it instead of making a customer-role duplicate.
  const results = useQuery({
    queryKey: ["party-search", debQ],
    queryFn: () => api<PartyListItem[]>(`/parties?q=${encodeURIComponent(debQ)}`),
    enabled: open && debQ.length >= 2,
  });

  function handlePick(p: PartyListItem) {
    // A supplier-only row picked as a customer is widened to "both" so the
    // next lookup finds it either way (fire-and-forget).
    if (p.role === "supplier") {
      api(`/parties/${p.id}`, { method: "PATCH", body: { role: "both" } }).catch(() => {});
    }
    onPick(p);
    setOpen(false);
    setQ("");
  }

  return (
    <div className="relative">
      <input
        className="field"
        placeholder={value ? label : "Search a party…"}
        value={open ? q : value ? label : q}
        onFocus={() => setOpen(true)}
        onChange={(e) => {
          setQ(e.target.value);
          setOpen(true);
        }}
        onBlur={() => setTimeout(() => setOpen(false), 150)}
      />
      {open && (results.data?.length ?? 0) > 0 && (
        <div className="absolute z-20 mt-1 max-h-64 w-full overflow-y-auto rounded-md border border-line bg-card shadow-lg">
          {results.data!.map((p) => (
            <button
              key={p.id}
              className="block w-full border-b border-[#f3eee4] px-3 py-2 text-left text-sm hover:bg-accent-soft"
              onMouseDown={() => handlePick(p)}
            >
              <span className="font-medium">{p.legal_name}</span>
              {p.role === "supplier" && (
                <span className="ml-2 rounded-sm bg-[#f1e7d6] px-1 text-[9px] font-bold uppercase text-warn">
                  supplier
                </span>
              )}
              <span className="ml-2 text-[11px] text-muted">
                {[p.default_state_code, lastSeenLabel(p.last_txn_at)].filter(Boolean).join(" · ")}
              </span>
            </button>
          ))}
          {q.trim() && (
            <button
              className="block w-full px-3 py-2 text-left text-[13px] text-muted hover:bg-accent-soft"
              onMouseDown={() => setCreating(true)}
            >
              None of these — add “{q.trim()}” as new
            </button>
          )}
        </div>
      )}
      {open && debQ.length >= 2 && !results.isFetching && (results.data?.length ?? 0) === 0 && (
        <div className="absolute z-20 mt-1 w-full overflow-hidden rounded-md border border-line bg-card shadow-lg">
          <div className="px-3 py-2 text-[11px] text-muted">No matching party.</div>
          <button
            className="block w-full bg-[#f0f6f8] px-3 py-2 text-left text-sm text-accent"
            onMouseDown={() => setCreating(true)}
          >
            + Create “{q.trim()}” as a new party
          </button>
        </div>
      )}
      {creating && (
        <QuickCreatePartyDialog
          initialName={q.trim()}
          onCancel={() => setCreating(false)}
          onCreated={(p) => {
            setCreating(false);
            handlePick(p);
          }}
        />
      )}
    </div>
  );
}

function QuickCreatePartyDialog({
  initialName,
  onCancel,
  onCreated,
}: {
  initialName: string;
  onCancel: () => void;
  onCreated: (p: PartyListItem) => void;
}) {
  const [name, setName] = useState(initialName);
  const [phone, setPhone] = useState("");
  const [opening, setOpening] = useState("");
  const [openingAsOf, setOpeningAsOf] = useState("");
  const [err, setErr] = useState<string | null>(null);
  const [dupWarn, setDupWarn] = useState<PartyDuplicate409 | null>(null);

  const debName = useDebounced(name.trim(), 300);
  const debPhone = useDebounced(phone.trim(), 400);

  function pickExisting(p: PartyMatchRef) {
    api<Party>(`/parties/${p.id}`).then(onCreated);
  }

  const create = useMutation<PartyListItem, unknown, boolean>({
    mutationFn: (force) =>
      api<PartyListItem>(`/parties${force ? "?force=true" : ""}`, {
        method: "POST",
        body: {
          legal_name: name.trim(),
          phone: phone.trim() ? (normalizePhone(phone) ?? phone.trim()) : null,
          role: "customer",
          opening_balance: opening.trim() === "" ? "0" : opening.trim(),
          opening_balance_as_of: openingAsOf || null,
        },
      }),
    onSuccess: onCreated,
    onError: (e) => {
      if (e instanceof ApiError && e.status === 409 && isDup409(e.detail)) {
        setDupWarn(e.detail);
        setErr(null);
      } else {
        setDupWarn(null);
        setErr(e instanceof ApiError ? e.message : "Could not create party");
      }
    },
  });

  const blockedByDup = dupWarn?.code === "party_maybe_exists";

  return (
    <div
      className="fixed inset-0 z-50 grid place-items-center bg-black/30 p-4"
      onClick={onCancel}
    >
      <div
        className="w-full max-w-sm rounded-lg border border-line bg-card p-4 shadow-xl"
        onClick={(e) => e.stopPropagation()}
      >
        <h3 className="font-serif text-sm font-semibold">New party</h3>
        {err && <p className="err mt-2">{err}</p>}
        <label className="label mt-3 block">Legal name</label>
        <input
          className="field"
          autoFocus
          value={name}
          onChange={(e) => {
            setDupWarn(null);
            setName(e.target.value);
          }}
          onKeyDown={(e) => {
            if (e.key === "Enter" && name.trim() && !blockedByDup) create.mutate(false);
          }}
        />
        {!dupWarn && (
          <div className="mt-2">
            <SimilarParties name={debName} phone={debPhone} onUse={pickExisting} />
          </div>
        )}
        <label className="label mt-3 block">Phone (optional)</label>
        <input
          className="field"
          inputMode="tel"
          value={phone}
          onChange={(e) => setPhone(e.target.value)}
          onBlur={(e) => {
            const norm = normalizePhone(e.target.value);
            if (norm && norm !== e.target.value) setPhone(norm);
          }}
        />
        {phone.trim() && phoneError(phone) && (
          <p className="err mt-1">{phoneError(phone)}</p>
        )}
        <label className="label mt-3 block">Opening balance (optional)</label>
        <div className="flex gap-2">
          <div className="relative flex-1">
            <span className="pointer-events-none absolute left-3 top-1/2 -translate-y-1/2 text-sm text-muted">
              ₹
            </span>
            <input
              className="field pl-7"
              inputMode="decimal"
              placeholder="0.00"
              value={opening}
              onChange={(e) => setOpening(e.target.value)}
            />
          </div>
          <input
            type="date"
            className="field flex-1"
            value={openingAsOf}
            onChange={(e) => setOpeningAsOf(e.target.value)}
          />
        </div>
        <p className="mt-1 text-[11px] text-muted">
          What they owed you before you started billing here.
        </p>

        {dupWarn && (
          <div className="mt-3 rounded-md border border-line bg-[#f1e7d6] p-3">
            <p className="text-[13px] font-medium text-warn">⚠ {dupWarn.message}</p>
            <ul className="mt-2 divide-y divide-line/70">
              {(dupWarn.match ? [dupWarn.match] : dupWarn.candidates).map((p) => (
                <li key={p.id} className="flex items-center justify-between gap-3 py-1.5">
                  <div className="min-w-0">
                    <p className="truncate text-sm font-medium text-ink">{p.legal_name}</p>
                    <p className="truncate text-[11px] text-muted">
                      {[p.city, p.gstin].filter(Boolean).join(" · ") || "—"}
                    </p>
                  </div>
                  <button
                    type="button"
                    className="btn-ghost h-7 shrink-0 px-2 text-xs"
                    onClick={() => pickExisting(p)}
                  >
                    Use this
                  </button>
                </li>
              ))}
            </ul>
            {dupWarn.code === "party_maybe_exists" && (
              <button
                type="button"
                className="mt-2 text-xs font-medium text-accent underline"
                onClick={() => {
                  setDupWarn(null);
                  create.mutate(true);
                }}
              >
                None of these — create “{name.trim()}” as new
              </button>
            )}
          </div>
        )}

        <div className="mt-4 flex gap-2">
          <button className="btn-ghost h-9 flex-1 px-4 text-sm" onClick={onCancel}>
            Cancel
          </button>
          <button
            className="btn-primary h-9 flex-1 px-4 text-sm"
            disabled={!name.trim() || create.isPending || blockedByDup}
            onClick={() => create.mutate(false)}
          >
            {create.isPending ? "Creating…" : "Create & use"}
          </button>
        </div>
      </div>
    </div>
  );
}

// --------------------------------------------------------------------------
// line row — item type-ahead
//   The visible list is /api/items?q= (substring on name / alias / grade /
//   size / HSN) — what you actually want while typing "buck". Alongside it
//   POST /api/items/resolve runs the tenant synonym map + alias table + the
//   confidence ladder purely to (a) silently adopt an unambiguous `exact`
//   hit on Enter/blur and (b) tag the matching row with a badge. resolve is
//   a precision matcher, never the browse list.
// --------------------------------------------------------------------------

const METHOD_BADGE: Record<string, { label: string; cls: string }> = {
  exact: { label: "exact", cls: "bg-[#dff0e3] text-[#3f7a4f]" },
  alias: { label: "learned", cls: "bg-[#e3eef2] text-accent" },
  fuzzy: { label: "≈ close", cls: "bg-[#f1e7d6] text-warn" },
};

function useDebounced<T>(value: T, ms: number): T {
  const [v, setV] = useState(value);
  useEffect(() => {
    const h = window.setTimeout(() => setV(value), ms);
    return () => window.clearTimeout(h);
  }, [value, ms]);
  return v;
}

function LineRow({
  n,
  row,
  readOnly,
  amount,
  expanded,
  onToggle,
  otherItemLine,
  onPatch,
  onRemove,
}: {
  n: number;
  row: Row;
  readOnly: boolean;
  amount: string | undefined;
  /** mobile: is this line's editor open? (one line open at a time) */
  expanded: boolean;
  /** mobile: request open (true) / collapse (false) */
  onToggle: (want: boolean) => void;
  /** 0-based index of another line already using this item, or -1 */
  otherItemLine: number;
  onPatch: (p: Partial<Row>) => void;
  onRemove: () => void;
}) {
  const [open, setOpen] = useState(false);
  const [typed, setTyped] = useState(row.description);
  /** fields the user has left — an empty-required caption only shows after this */
  const [touched, setTouched] = useState<Set<LineProblem["field"]>>(new Set());
  const touch = (f: LineProblem["field"]) =>
    setTouched((s) => (s.has(f) ? s : new Set(s).add(f)));
  /** mobile: which "+" panels the user has opened (discount / hsn) */
  const [reveal, setReveal] = useState<Set<"disc" | "hsn">>(new Set());
  const show = (k: "disc" | "hsn") => setReveal((s) => new Set(s).add(k));
  const hideExtras = () => setReveal(new Set());

  useEffect(() => setTyped(row.description), [row.description]);

  const debounced = useDebounced(typed.trim(), 200);
  const active = open && debounced.length >= 1 && !row.item_id;

  // visible candidate list — substring browse search
  const search = useQuery({
    queryKey: ["item-typeahead", debounced],
    queryFn: () => api<ItemListItem[]>(`/items?q=${encodeURIComponent(debounced)}`),
    enabled: active,
  });
  const results = search.data ?? [];

  // parallel resolve — drives auto-pick + the badge only, never the list
  const resolve = useQuery({
    queryKey: ["item-resolve", debounced, row.hsn_code.trim()],
    queryFn: () => {
      const p = new URLSearchParams({ description: debounced });
      if (row.hsn_code.trim()) p.set("hsn", row.hsn_code.trim());
      return api<ResolveResult>(`/items/resolve?${p.toString()}`, { method: "POST" });
    },
    enabled: active,
  });
  const method = resolve.data?.method ?? null;
  const resolvedId = resolve.data?.candidates?.[0]?.id ?? null;
  const autoPick =
    method === "exact" && resolvedId
      ? results.find((r) => r.id === resolvedId) ?? null
      : null;

  function pick(it: ItemListItem) {
    // the item's own unit wins on pick (falling back to its secondary unit,
    // then whatever's already on the row). The ₹/<unit> label follows it.
    const nextUom =
      normalizeUom(it.uom) || normalizeUom(it.secondary_uom) || row.uom;
    onPatch({
      item_id: it.id,
      description: it.name,
      hsn_code: it.hsn_code ?? row.hsn_code,
      uom: nextUom,
      unit_rate: it.last_rate ?? it.default_rate ?? row.unit_rate ?? "",
      _priceMin: it.price_min,
      _priceMax: it.price_max,
      _lastRate: it.last_rate,
      _lastSoldAt: it.last_sold_at ?? null,
    });
    setTyped(it.name);
    setOpen(false);
  }

  function createNew() {
    // a free-typed item defaults to pieces; the operator changes the Unit
    // dropdown if it's kg / doz / gross, and that unit is what the item is
    // created with on finalize.
    onPatch({
      item_id: null,
      description: typed.trim(),
      uom: row.uom.trim() || "nos",
      _priceMin: null,
      _priceMax: null,
      _lastRate: null,
      _lastSoldAt: null,
    });
    setOpen(false);
  }

  const problems = lineProblems(row);
  const byField = (f: LineProblem["field"]) => problems.filter((p) => p.field === f);
  const fieldClass = (f: LineProblem["field"]) => {
    const ps = byField(f);
    if (ps.some((p) => p.block)) return "border-danger focus:border-danger";
    if (ps.length) return "border-warn focus:border-warn";
    return "";
  };
  const blocked = problems.some((p) => p.block);
  const filled = row.description.trim().length > 0;

  // the unit the line is priced in — canonical value (stored), plus its
  // human-facing spelling for labels ("nos" -> "pcs").
  const unitLabel = normalizeUom(row.uom) || "nos";
  const unitText = uomDisplay(unitLabel);
  // The strict billing set (nos / kg / doz / gross), plus the row's current
  // unit if it's a legacy value not in that set — so an old "bundle" line
  // stays selectable and never silently flips on open.
  const unitChoices = Array.from(
    new Set(
      [...PRIMARY_UOMS, normalizeUom(row.uom)].filter(Boolean),
    ),
  );

  // Materialise the effective unit onto the row once a line has content, so
  // the <select>'s shown value is the stored value (no phantom "needs a unit").
  useEffect(() => {
    if (filled && !row.uom.trim() && unitLabel) onPatch({ uom: unitLabel });
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [filled, row.uom, unitLabel]);

  const discAmt = rowDiscountAmount(row);
  const workingBits: string[] = [];
  if (Number(row.quantity) && Number(row.unit_rate)) {
    workingBits.push(`${row.quantity} ${unitText || ""}`.trim() + ` × ₹${row.unit_rate}`);
    if (discAmt > 0) workingBits.push(`− ₹${discAmt}`);
  }
  const working = workingBits.join(" ");

  const lastRateGhost =
    row._lastRate && Number(row._lastRate)
      ? `last ₹${row._lastRate}` +
        (row._lastSoldAt ? ` · ${new Date(row._lastSoldAt).toLocaleDateString()}` : "")
      : "";
  const bandGhost =
    row._priceMin || row._priceMax
      ? `band ${row._priceMin ? `₹${row._priceMin}` : ""}${
          row._priceMin && row._priceMax ? "–" : ""
        }${row._priceMax ? `₹${row._priceMax}` : ""}`
      : "";
  const rateGhost = [lastRateGhost, bandGhost].filter(Boolean).join(" · ");

  // ---- shared sub-pieces ----
  const itemInput = (
    <div className="relative">
      <input
        className={`field h-9 text-sm ${row.item_id ? "pr-16" : ""} ${fieldClass("item")}`}
        placeholder="type an item name…"
        value={typed}
        disabled={readOnly}
        title={typed}
        onChange={(e) => {
          setTyped(e.target.value);
          onPatch({ description: e.target.value, item_id: null, group_id: null });
          setOpen(true);
        }}
        onFocus={() => setOpen(true)}
        onBlur={() => {
          if (autoPick && !row.item_id) pick(autoPick);
          setTimeout(() => setOpen(false), 160);
        }}
        onKeyDown={(e) => {
          if (e.key === "Enter") {
            e.preventDefault();
            if (autoPick) pick(autoPick);
            else if (results[0]) pick(results[0]);
          } else if (e.key === "Escape") {
            setOpen(false);
          }
        }}
      />
      {open && !row.item_id && typed.trim() && (
        <div className="absolute z-20 mt-1 max-h-60 w-[min(380px,90vw)] overflow-y-auto rounded-md border border-line bg-card shadow-lg">
          {results.map((it) => {
            const badge = it.id === resolvedId && method ? METHOD_BADGE[method] : undefined;
            const size = it.size_text || it.grade;
            return (
              <button
                key={it.id}
                className="flex w-full items-center justify-between gap-2 border-b border-[#f3eee4] px-3 py-2 text-left text-sm hover:bg-accent-soft"
                onMouseDown={() => pick(it)}
              >
                <span className="min-w-0">
                  {it.name}
                  {size && <span className="ml-1 text-[11px] text-muted">{size}</span>}
                  <span
                    className={`ml-1 rounded px-1 py-0.5 text-[9px] ${
                      it.item_type === "bulk"
                        ? "bg-[#e3eef2] text-accent"
                        : "bg-[#f1e7d6] text-warn"
                    }`}
                  >
                    {it.item_type === "bulk" ? "⚖" : "📦"}
                  </span>
                  {badge && (
                    <span className={`ml-1 rounded px-1 py-0.5 text-[9px] ${badge.cls}`}>
                      {badge.label}
                    </span>
                  )}
                </span>
                <span className="whitespace-nowrap text-[11px] text-muted">
                  {it.last_rate ? `₹${it.last_rate}` : ""} · {it.times_billed}×
                </span>
              </button>
            );
          })}
          {!results.length && !search.isFetching && (
            <div className="px-3 py-2 text-[11px] text-muted">No matching item.</div>
          )}
          <button
            className="block w-full bg-[#f0f6f8] px-3 py-2 text-left text-sm text-accent"
            onMouseDown={createNew}
          >
            + Use “{typed.trim()}” as a new item
          </button>
        </div>
      )}
      {row.item_id && (
        <span className="pointer-events-none absolute right-2 top-1/2 -translate-y-1/2 whitespace-nowrap text-[10px] font-semibold text-[#3f7a4f]">
          ✓ matched
        </span>
      )}
    </div>
  );

  const discSeg = (
    <span className="inline-flex h-9 flex-none overflow-hidden rounded-md border border-line">
      {(["amt", "pct"] as DiscMode[]).map((m) => (
        <button
          key={m}
          type="button"
          disabled={readOnly}
          title={m === "amt" ? "Discount in ₹" : "Discount in %"}
          className={`w-7 border-r border-line text-xs font-bold last:border-r-0 ${
            row.discMode === m ? "bg-accent text-white" : "bg-card text-muted hover:bg-ground"
          }`}
          onClick={() => onPatch({ discMode: m })}
        >
          {m === "amt" ? "₹" : "%"}
        </button>
      ))}
    </span>
  );

  // ---- MOBILE ----
  // per-field caption, shown only after the user has left an empty required field
  const fieldMsg = (f: LineProblem["field"]) => {
    const ps = byField(f);
    const blk = ps.find((p) => p.block);
    if (blk) return touched.has(f) ? { text: blk.msg, cls: "text-danger" } : null;
    const warn = ps.find((p) => !p.block);
    return warn ? { text: warn.msg, cls: "text-warn" } : null;
  };
  const qtyMsg = fieldMsg("qty");
  const rateMsg = fieldMsg("rate");
  const discMsg = fieldMsg("disc");
  const hsnMsg = fieldMsg("hsn");
  const unitMsg = fieldMsg("unit");

  const showDisc = reveal.has("disc") || (row.discount.trim() && discAmt > 0);
  const showHsn = reveal.has("hsn") || row.hsn_code.trim().length > 0;
  const anyExtra = showDisc || showHsn;

  // % → ₹ explainer under the discount field — billing uses the ₹ figure;
  // the % itself is kept too, so this line reloads showing the same %
  const discExplain =
    row.discMode === "pct" && discAmt > 0
      ? `${row.discount.trim()}% = ₹${discAmt} off this line`
      : "";

  const collapsedRow = (
    <button
      type="button"
      className="flex w-full items-center gap-2.5 px-3 py-3 text-left"
      onClick={() => onToggle(true)}
    >
      <span className="w-3 flex-none text-xs text-muted">{n}</span>
      <span className="min-w-0 flex-1">
        <span
          className={`block truncate text-[15px] font-semibold ${
            blocked ? "text-danger" : "text-ink"
          }`}
        >
          {row.description.trim() || "New item"}
        </span>
        {blocked ? (
          <span className="text-[12px] font-semibold text-danger">
            tap to fix &mdash; {problems.find((p) => p.block)?.msg}
          </span>
        ) : (
          working && (
            <span className="block text-[12px] tabular-nums text-muted">{working}</span>
          )
        )}
      </span>
      <span className="flex-none font-serif text-base font-semibold">
        {amount ? inr(amount) : "—"}
      </span>
      <span className="flex-none text-xs text-muted">▸</span>
    </button>
  );

  const editor = (
    <div className="px-3 pb-3 pt-2">
      <div className="mb-1.5 flex items-center justify-between text-xs text-muted">
        <span>Line {n}</span>
        {!readOnly && (
          <button
            type="button"
            className="h-7 w-7 rounded-md border border-line text-danger"
            title="Remove line"
            onClick={onRemove}
          >
            🗑
          </button>
        )}
      </div>

      {itemInput}

      {row.description.trim() && !row.item_id && (
        <div className="mt-2 rounded-md border border-[#ecdcb8] bg-[#fbf3e2] px-2.5 py-1.5 text-[11px] text-warn">
          Not in the catalogue — finalising creates “{row.description.trim()}” as a
          new item, with the unit you pick below.
        </div>
      )}

      {/* Qty · Unit · Rate — the three you always need */}
      <div className="mt-2.5 grid grid-cols-[1fr_84px_1fr] items-end gap-2">
        <div>
          <label className="fl-m">Qty</label>
          <input
            className={`field h-10 text-right ${fieldClass("qty")}`}
            inputMode="decimal"
            placeholder="0"
            value={row.quantity}
            disabled={readOnly}
            onChange={(e) => onPatch({ quantity: e.target.value })}
            onBlur={() => touch("qty")}
          />
        </div>
        <div>
          <label className="fl-m">Unit</label>
          <select
            className={`field h-10 px-1.5 text-sm ${fieldClass("unit")}`}
            value={normalizeUom(row.uom) || unitLabel}
            disabled={readOnly}
            onChange={(e) => onPatch({ uom: e.target.value })}
          >
            {unitChoices.map((u) => (
              <option key={u} value={u}>
                {uomDisplay(u)}
              </option>
            ))}
          </select>
        </div>
        <div>
          <label className="fl-m">Rate ₹/{unitText}</label>
          <input
            className={`field h-10 text-right ${fieldClass("rate")}`}
            inputMode="decimal"
            placeholder="0.00"
            value={row.unit_rate}
            disabled={readOnly}
            onChange={(e) => onPatch({ unit_rate: e.target.value })}
            onBlur={() => touch("rate")}
          />
        </div>
      </div>
      {unitMsg && <p className={`mt-1 text-[11px] ${unitMsg.cls}`}>{unitMsg.text}</p>}
      {qtyMsg && <p className={`mt-1 text-[11px] ${qtyMsg.cls}`}>{qtyMsg.text}</p>}
      {(rateMsg || rateGhost) && (
        <p className={`mt-1 text-[11px] ${rateMsg ? rateMsg.cls : "text-muted"}`}>
          {rateMsg ? rateMsg.text : rateGhost}
        </p>
      )}

      {/* live line total */}
      <div
        className={`mt-2.5 font-serif text-lg font-semibold ${
          blocked && touched.size ? "text-danger" : ""
        }`}
      >
        = {amount ? inr(amount) : "—"}
        {working && (
          <span className="block font-sans text-[11px] font-normal text-muted">
            {working}
          </span>
        )}
      </div>

      {/* revealed panels */}
      {showDisc && (
        <div className="mt-2.5 rounded-md bg-ground p-2.5">
          <label className="fl-m">Discount {discSeg}</label>
          <input
            className={`field h-10 text-right ${fieldClass("disc")}`}
            inputMode="decimal"
            placeholder="0"
            value={row.discount}
            disabled={readOnly}
            onChange={(e) => onPatch({ discount: e.target.value })}
            onBlur={() => touch("disc")}
          />
          {discMsg ? (
            <p className={`mt-1 text-[11px] ${discMsg.cls}`}>{discMsg.text}</p>
          ) : (
            discExplain && <p className="mt-1 text-[11px] text-muted">{discExplain}</p>
          )}
        </div>
      )}
      {showHsn && (
        <div className="mt-2.5 rounded-md bg-ground p-2.5">
          <label className="fl-m">
            HSN{" "}
            <span className="font-normal normal-case tracking-normal">optional</span>
          </label>
          <input
            className={`field h-10 ${fieldClass("hsn")}`}
            placeholder="4 / 6 / 8 digits"
            value={row.hsn_code}
            disabled={readOnly}
            onChange={(e) => onPatch({ hsn_code: e.target.value })}
            onBlur={() => touch("hsn")}
          />
          {hsnMsg && <p className={`mt-1 text-[11px] ${hsnMsg.cls}`}>{hsnMsg.text}</p>}
        </div>
      )}

      {/* quiet "+" links */}
      {!readOnly && (
        <div className="mt-2.5 flex flex-wrap gap-x-4 gap-y-1 border-t border-dashed border-line pt-2.5 text-[13px] font-semibold text-accent">
          {!showDisc && (
            <button type="button" onClick={() => show("disc")}>
              <span className="text-[15px]">＋</span> discount
            </button>
          )}
          {!showHsn && (
            <button type="button" onClick={() => show("hsn")}>
              <span className="text-[15px]">＋</span> HSN
            </button>
          )}
          {anyExtra && (
            <button type="button" className="text-muted" onClick={hideExtras}>
              − hide extras
            </button>
          )}
        </div>
      )}

      {filled && !readOnly && (
        <div className="mt-2.5 text-right">
          <button
            type="button"
            className="text-[13px] font-semibold text-accent"
            onClick={() => onToggle(false)}
          >
            Done
          </button>
        </div>
      )}
    </div>
  );

  // on mobile, a collapsed empty line renders nothing — "+ Add another item" is it
  const mobile = (
    <div className="md:hidden">
      {expanded ? editor : filled ? collapsedRow : null}
    </div>
  );

  // ---- DESKTOP row ----
  const desktop = (
    <div className="hidden md:block">
      <div className={`grid ${LINE_GRID} items-center gap-2 px-3 py-2 text-sm`}>
        <span className={`text-center text-xs ${blocked ? "text-danger" : "text-muted"}`}>{n}</span>
        {itemInput}
        <input
          className={`field h-9 text-xs ${fieldClass("hsn")}`}
          placeholder="HSN"
          value={row.hsn_code}
          disabled={readOnly}
          onChange={(e) => onPatch({ hsn_code: e.target.value })}
        />
        <input
          className={`field h-9 text-right text-sm ${fieldClass("qty")}`}
          inputMode="decimal"
          placeholder="0"
          value={row.quantity}
          disabled={readOnly}
          onChange={(e) => onPatch({ quantity: e.target.value })}
        />
        <select
          className={`field h-9 px-1 text-xs ${fieldClass("unit")}`}
          value={normalizeUom(row.uom) || unitLabel}
          disabled={readOnly}
          title={isWeightUom(row.uom) ? "weight item" : "piece item"}
          onChange={(e) => onPatch({ uom: e.target.value })}
        >
          {unitChoices.map((u) => (
            <option key={u} value={u}>
              {uomDisplay(u)}
            </option>
          ))}
        </select>
        <input
          className={`field h-9 text-right text-sm ${fieldClass("rate")}`}
          inputMode="decimal"
          placeholder="0"
          value={row.unit_rate}
          disabled={readOnly}
          title={rateGhost || undefined}
          onChange={(e) => onPatch({ unit_rate: e.target.value })}
        />
        <span className="flex items-center justify-end gap-1.5">
          {discSeg}
          <input
            className={`field h-9 w-12 text-right text-xs ${fieldClass("disc")}`}
            inputMode="decimal"
            placeholder="0"
            value={row.discount}
            disabled={readOnly}
            onChange={(e) => onPatch({ discount: e.target.value })}
          />
        </span>
        <span className="text-right font-mono text-sm" title={working || undefined}>
          {amount ? inr(amount) : "—"}
        </span>
        {!readOnly ? (
          <button className="text-danger md:text-center" title="Remove line" onClick={onRemove}>
            ×
          </button>
        ) : (
          <span />
        )}
      </div>
      {!readOnly && problems.length > 0 && (
        <div className="border-b border-[#f3eee4] bg-[#fbf6ee] px-3 py-1.5 pl-11 text-[11px]">
          <b className="mr-1 text-ink">Line {n}:</b>
          {problems.map((p, i) => (
            <span key={i} className={p.block ? "text-danger" : "text-warn"}>
              {i > 0 && " · "}
              {p.msg}
            </span>
          ))}
        </div>
      )}
    </div>
  );

  // a collapsed empty mobile line contributes nothing — don't draw its divider
  const mobileBlank = !expanded && !filled;
  return (
    <div className={mobileBlank ? "md:border-b md:border-[#f3eee4]" : "border-b border-[#f3eee4]"}>
      {mobile}
      {desktop}
      {otherItemLine >= 0 && (
        <div className="px-3 pb-1 text-[10px] text-warn md:pl-11">
          also on line {otherItemLine + 1}
        </div>
      )}
    </div>
  );
}
