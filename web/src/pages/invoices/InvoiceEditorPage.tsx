import { useEffect, useMemo, useState } from "react";
import { useNavigate, useParams } from "react-router-dom";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { api, ApiError } from "../../lib/api";
import { downloadFile } from "../../lib/download";
import { computePreview, inr } from "../../lib/previewTotal";
import { computeMeasure, kg } from "../../lib/weighment";
import { PaymentDialog } from "../../components/PaymentDialog";
import type {
  FinalizeResult,
  Invoice,
  InvoiceLineIn,
  ItemListItem,
  PartyListItem,
  PaymentCreate,
  PaymentOut,
  RateMode,
  ResolveResult,
  WeighmentSlipIn,
} from "../../lib/types";

type DiscMode = "amt" | "pct";
/** piece/kg choice for a free-typed line that will become a new item */
type NewMode = RateMode | "";

/** an editor row — string-typed so partial input never NaNs the totals */
interface Row {
  key: string;
  item_id: string | null;
  group_id: string | null;
  description: string;
  hsn_code: string;
  quantity: string;
  uom: string;
  /** the picked item's alternate sell unit, if any — narrows the unit picker */
  secondaryUom: string;
  /** the picked item's rate_mode, drives the ₹/<unit> label + new-item default */
  rateMode: RateMode | null;
  unit_rate: string;
  discount: string;
  discMode: DiscMode;
  /** 1-based weighment segment this line belongs to */
  segmentNo: number;
  /** for a line with no item match yet: sold per piece or per kg */
  newMode: NewMode;
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
    secondaryUom: "",
    rateMode: null,
    unit_rate: "",
    discount: "",
    // % is the default discount mode for a fresh line
    discMode: "pct",
    segmentNo,
    newMode: "",
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
    quantity: String(l.quantity ?? ""),
    uom: l.uom ?? "",
    secondaryUom: "",
    rateMode: null,
    unit_rate: String(l.unit_rate ?? ""),
    // stored lines always carry an absolute discount — reloads as ₹
    discount: l.discount && Number(l.discount) ? String(l.discount) : "",
    discMode: "amt" as DiscMode,
    segmentNo: l.segment_no ?? 1,
    newMode: "" as NewMode,
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
    if (!r.newMode)
      out.push({ field: "item", block: false, msg: "choose per piece / per kg for the new item" });
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
  return {
    item_id: r.item_id,
    group_id: r.group_id,
    description: r.description.trim(),
    hsn_code: r.hsn_code.trim() || null,
    quantity: r.quantity.trim() || "0",
    uom: r.uom.trim() || null,
    unit_rate: r.unit_rate.trim() || "0",
    // % is resolved to an absolute amount here — the backend line stores ₹ only
    discount: String(rowDiscountAmount(r) || 0),
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
  const openSegWeight = measure.segments.find((s) => s.seg === openSeg)?.weightKg ?? 0;
  const localBlockers: string[] = [];
  if (!partyId) localBlockers.push("select a party");
  if (!filledRows.length) localBlockers.push("add at least one line with an item");
  filledRows.forEach((r) => {
    const n = rows.indexOf(r) + 1;
    const problems = lineProblems(r);
    problems.filter((p) => p.block).forEach((p) => localBlockers.push(`line ${n}: ${p.msg}`));
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
                    setDirty(true);
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
              <div className="flex flex-wrap items-center justify-between gap-2 border-t border-line bg-accent-soft px-3 py-2 text-xs">
                <div className="flex flex-wrap gap-x-4 gap-y-1">
                  {openSeg > 1 && (
                    <span className="text-muted">
                      Seg&nbsp;{openSeg}{" "}
                      <b className="font-mono text-ink">{kg(openSegWeight)}</b>
                    </span>
                  )}
                  <span className="text-muted">
                    Bill{" "}
                    <b className="font-mono text-ink">{kg(measure.totalWeightKg)}</b>
                    {" · "}
                    <b className="font-mono text-ink">{measure.totalCount} pcs</b>
                  </span>
                </div>
                <button
                  type="button"
                  className="rounded-md border border-ok px-3 py-1 text-xs font-semibold text-ok hover:bg-[#eef3ee]"
                  onClick={startNextSegment}
                >
                  Next segment ›
                </button>
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
                        {kg(
                          s.recordedKg != null ? s.recordedKg : s.weightKg,
                        )}
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
  return (
    <div className="flex flex-wrap items-center gap-x-3 gap-y-1 border-y border-[#c9ddc9] bg-[#eef3ee] px-3 py-1.5">
      <span className="flex items-center gap-1.5 text-[11px] font-semibold uppercase tracking-wide text-ok">
        <svg className="h-3 w-3" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2" strokeLinecap="round" strokeLinejoin="round">
          <path d="M12 3v18M5 21h14M6 7h12l3 7a4 4 0 01-8 0zM6 7L3 14a4 4 0 008 0" />
        </svg>
        Weighment {seg}
      </span>
      {readOnly ? (
        <span className="font-mono text-xs font-semibold text-ink">{kg(recordedKg)}</span>
      ) : (
        <span className="flex items-center gap-1">
          <input
            className="field h-7 w-24 text-right font-mono text-xs"
            inputMode="decimal"
            value={recordedKg}
            onChange={(e) => onEdit(e.target.value)}
          />
          <span className="text-[10px] text-muted">kg</span>
        </span>
      )}
      <span className="text-[11px] text-muted">
        lines {lineFrom}–{lineTo}
        {count > 0 && ` · ${count} pcs`}
      </span>
      {Math.abs(drift) >= 0.005 && (
        <span className="text-[10px] text-warn">
          {drift > 0 ? "+" : "−"}
          {Math.abs(drift).toFixed(2)} kg vs line sum
        </span>
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
  onCancel,
  onConfirm,
}: {
  seg: number;
  lineSumKg: number;
  onCancel: () => void;
  onConfirm: (recordedKg: string) => void;
}) {
  const [val, setVal] = useState(lineSumKg ? String(lineSumKg) : "");
  const drift = (parseFloat(val) || 0) - lineSumKg;
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
        <p className="mt-1 text-xs text-muted">
          Sum of line weights in this segment:{" "}
          <b className="text-ink">{kg(lineSumKg)}</b>
        </p>
        <label className="label mt-3 block">Weight shown on the platform scale</label>
        <input
          className="field text-right font-mono"
          inputMode="decimal"
          autoFocus
          value={val}
          placeholder="0.000"
          onChange={(e) => setVal(e.target.value)}
          onKeyDown={(e) => {
            if (e.key === "Enter") onConfirm(val);
          }}
        />
        {Math.abs(drift) >= 0.005 && (
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
            className="h-9 flex-1 rounded-md bg-ok px-4 text-sm font-semibold text-white"
            onClick={() => onConfirm(val)}
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
  const results = useQuery({
    queryKey: ["party-search", q],
    queryFn: () => api<PartyListItem[]>(`/parties?q=${encodeURIComponent(q)}&role=customer`),
    enabled: open && q.trim().length >= 1,
  });

  function handlePick(p: PartyListItem) {
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
              {p.default_state_code && (
                <span className="ml-2 text-[11px] text-muted">{p.default_state_code}</span>
              )}
            </button>
          ))}
          {q.trim() && (
            <button
              className="block w-full bg-[#f0f6f8] px-3 py-2 text-left text-sm text-accent"
              onMouseDown={() => setCreating(true)}
            >
              + Create “{q.trim()}” as a new party
            </button>
          )}
        </div>
      )}
      {open && q.trim() && !results.isFetching && (results.data?.length ?? 0) === 0 && (
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
  const [err, setErr] = useState<string | null>(null);
  const create = useMutation({
    mutationFn: () =>
      api<PartyListItem>("/parties", {
        method: "POST",
        body: {
          legal_name: name.trim(),
          phone: phone.trim() || null,
          role: "customer",
        },
      }),
    onSuccess: onCreated,
    onError: (e) => setErr(e instanceof ApiError ? e.message : "Could not create party"),
  });

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
          onChange={(e) => setName(e.target.value)}
          onKeyDown={(e) => {
            if (e.key === "Enter" && name.trim()) create.mutate();
          }}
        />
        <label className="label mt-3 block">Phone (optional)</label>
        <input
          className="field"
          inputMode="tel"
          value={phone}
          onChange={(e) => setPhone(e.target.value)}
        />
        <div className="mt-4 flex gap-2">
          <button className="btn-ghost h-9 flex-1 px-4 text-sm" onClick={onCancel}>
            Cancel
          </button>
          <button
            className="btn-primary h-9 flex-1 px-4 text-sm"
            disabled={!name.trim() || create.isPending}
            onClick={() => create.mutate()}
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
  /** mobile: which "+" panels the user has opened (discount / hsn / unit) */
  const [reveal, setReveal] = useState<Set<"disc" | "hsn" | "unit">>(new Set());
  const show = (k: "disc" | "hsn" | "unit") => setReveal((s) => new Set(s).add(k));
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
    onPatch({
      item_id: it.id,
      description: it.name,
      hsn_code: it.hsn_code ?? row.hsn_code,
      uom: it.uom ?? it.secondary_uom ?? row.uom,
      secondaryUom: it.secondary_uom ?? "",
      rateMode: it.rate_mode ?? null,
      unit_rate: it.last_rate ?? it.default_rate ?? row.unit_rate ?? "",
      newMode: "",
      _priceMin: it.price_min,
      _priceMax: it.price_max,
      _lastRate: it.last_rate,
      _lastSoldAt: it.last_sold_at ?? null,
    });
    setTyped(it.name);
    setOpen(false);
  }

  function createNew() {
    onPatch({
      item_id: null,
      description: typed.trim(),
      rateMode: null,
      secondaryUom: "",
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

  // the unit the line is priced in — for the "Rate ₹/<unit>" label
  const unitLabel = row.uom.trim() || (row.rateMode === "kg" ? "kg" : "nos");
  // Always a dropdown, defaulted to the item's unit but overridable: the
  // item's own unit(s) first, then the two rate-mode defaults, deduped.
  const unitChoices = Array.from(
    new Set(
      [row.uom, row.secondaryUom, "nos", "kg"]
        .map((u) => u.trim().toLowerCase())
        .filter(Boolean),
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
    workingBits.push(`${row.quantity} ${unitLabel || ""}`.trim() + ` × ₹${row.unit_rate}`);
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

  // is this a two-unit item? then offer a "change unit" reveal; else unit is fixed
  const twoUnit = unitChoices.length > 1;
  const showDisc = reveal.has("disc") || (row.discount.trim() && discAmt > 0);
  const showHsn = reveal.has("hsn") || row.hsn_code.trim().length > 0;
  const showUnit = reveal.has("unit");
  const anyExtra = showDisc || showHsn || showUnit;

  // % → ₹ explainer under the discount field
  const discExplain =
    row.discMode === "pct" && discAmt > 0
      ? `${row.discount.trim()}% = ₹${discAmt} — stored as ₹${discAmt}`
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
          Not in the catalogue — finalising creates “{row.description.trim()}” as a new item.{" "}
          <span className="ml-1 inline-flex overflow-hidden rounded border border-[#e2cfa0] align-middle">
            {(["piece", "kg"] as const).map((m) => (
              <button
                key={m}
                type="button"
                className={`px-1.5 py-0.5 text-[10px] font-bold ${
                  row.newMode === m ? "bg-warn text-white" : "bg-transparent"
                }`}
                onClick={() =>
                  onPatch({
                    newMode: m,
                    uom: m === "kg" ? "kg" : "nos",
                    rateMode: m,
                  })
                }
              >
                per {m}
              </button>
            ))}
          </span>
        </div>
      )}

      {/* Qty × Rate — the two you always need */}
      <div className="mt-2.5 grid grid-cols-[92px_16px_1fr] items-end gap-2">
        <div>
          <label className="fl-m">
            Qty
            {!twoUnit && <span className="unit-badge">{unitLabel}</span>}
          </label>
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
        <div className="pb-2.5 text-center text-base text-muted">×</div>
        <div>
          <label className="fl-m">Rate ₹/{unitLabel}</label>
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
      {showUnit && twoUnit && (
        <div className="mt-2.5 rounded-md bg-ground p-2.5">
          <label className="fl-m">Unit</label>
          <select
            className={`field h-10 px-2 text-sm ${fieldClass("unit")}`}
            value={row.uom.trim().toLowerCase() || unitLabel}
            disabled={readOnly}
            onChange={(e) => onPatch({ uom: e.target.value })}
          >
            {unitChoices.map((u) => (
              <option key={u}>{u}</option>
            ))}
          </select>
          <p className="mt-1 text-[11px] text-muted">this item is sold more than one way</p>
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
          {twoUnit && !showUnit && (
            <button type="button" onClick={() => show("unit")}>
              change unit
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
          value={row.uom.trim().toLowerCase() || unitLabel}
          disabled={readOnly}
          title={row.rateMode === "kg" ? "weight item" : "piece item"}
          onChange={(e) => onPatch({ uom: e.target.value })}
        >
          {unitChoices.map((u) => (
            <option key={u}>{u}</option>
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
