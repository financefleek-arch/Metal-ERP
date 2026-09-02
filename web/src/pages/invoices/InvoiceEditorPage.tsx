import { useEffect, useMemo, useState } from "react";
import { useNavigate, useParams } from "react-router-dom";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { api, ApiError, getToken } from "../../lib/api";
import { computePreview, inr } from "../../lib/previewTotal";
import type {
  Invoice,
  InvoiceLineIn,
  ItemListItem,
  PartyListItem,
  RateMode,
  ResolveResult,
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
  /** for a line with no item match yet: sold per piece or per kg */
  newMode: NewMode;
  /** snapshots from the picked item — for guards + ghost text, not sent */
  _priceMin: string | null;
  _priceMax: string | null;
  _lastRate: string | null;
  _lastSoldAt: string | null;
}

let _rk = 0;
function blankRow(): Row {
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
    discMode: "amt",
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
    // stored lines always carry an absolute discount
    discount: l.discount && Number(l.discount) ? String(l.discount) : "",
    discMode: "amt" as DiscMode,
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
  };
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
  const [rows, setRows] = useState<Row[]>([blankRow()]);
  const [err, setErr] = useState<string | null>(null);
  const [dirty, setDirty] = useState(false);

  const inv = detail.data;
  const finalized = inv?.status === "final";
  const cancelled = inv?.status === "cancelled";
  const readOnly = finalized || cancelled;

  // hydrate from a loaded draft/invoice
  useEffect(() => {
    if (!inv) return;
    setPartyId(inv.party_id);
    setPartyLabel(inv.party?.legal_name ?? "");
    setDate(inv.date);
    setNotes(inv.notes ?? "");
    setInvoiceDiscount(inv.invoice_discount && Number(inv.invoice_discount) ? inv.invoice_discount : "");
    setRows(rowsFromInvoice(inv));
    setDirty(false);
  }, [inv]);


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
        invoiceDiscount,
      }),
    [rows, invoiceDiscount],
  );

  const filledRows = rows.filter((r) => r.description.trim());
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
      const body = {
        party_id: partyId,
        date,
        notes: notes.trim() || null,
        invoice_discount: invoiceDiscount.trim() || "0",
        lines: rows.filter((r) => r.description.trim()).map(toLineIn),
      };
      if (isNew) return api<Invoice>("/invoices", { method: "POST", body });
      return api<Invoice>(`/invoices/${id}`, { method: "PUT", body });
    },
    onSuccess: (saved) => {
      qc.invalidateQueries({ queryKey: ["invoices"] });
      setDirty(false);
      if (isNew) nav(`/invoices/${saved.id}`, { replace: true });
      else qc.setQueryData(["invoice", id], saved);
    },
    onError: (e) => setErr(e instanceof ApiError ? e.message : "Save failed"),
  });

  const finalize = useMutation({
    mutationFn: async () => {
      const saved = await save.mutateAsync();
      return api<{ number: number; pdf_status: string }>(`/invoices/${saved.id}/finalize`, {
        method: "POST",
      });
    },
    onSuccess: () => {
      qc.invalidateQueries({ queryKey: ["invoices"] });
      qc.invalidateQueries({ queryKey: ["invoice", id] });
      detail.refetch();
    },
    onError: (e) => {
      if (e instanceof ApiError) {
        // 422 detail is a list of blocker strings joined by "; " in api.ts
        setErr(e.message);
      } else setErr("Finalize failed");
    },
  });

  function patchRow(key: string, patch: Partial<Row>) {
    setRows((rs) => rs.map((r) => (r.key === key ? { ...r, ...patch } : r)));
    setDirty(true);
  }
  function addRow() {
    setRows((rs) => [...rs, blankRow()]);
    setDirty(true);
  }
  function removeRow(key: string) {
    setRows((rs) => {
      const next = rs.filter((r) => r.key !== key);
      return next.length ? next : [blankRow()];
    });
    setDirty(true);
  }

  function openPdf() {
    if (!id) return;
    const t = getToken();
    fetch(`/api/invoices/${id}/pdf`, { headers: t ? { Authorization: `Bearer ${t}` } : {} })
      .then((r) => {
        if (!r.ok) throw new Error();
        return r.blob();
      })
      .then((b) => window.open(URL.createObjectURL(b), "_blank"))
      .catch(() => setErr("PDF not ready."));
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
                onClick={() => {
                  setErr(null);
                  save.mutate();
                }}
              >
                {save.isPending ? "Saving…" : "Save draft"}
              </button>
              <button
                className="btn-primary h-9 px-4 text-sm"
                disabled={localBlockers.length > 0 || finalize.isPending}
                onClick={() => {
                  setErr(null);
                  finalize.mutate();
                }}
              >
                {finalize.isPending ? "Finalizing…" : "Finalize"}
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
            <div className="hidden grid-cols-[24px_1fr_84px_66px_54px_84px_92px_92px_28px] gap-2 border-b border-line bg-ground px-3 py-2 text-[10px] font-semibold uppercase text-muted md:grid">
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

            {rows.map((r, i) => (
              <LineRow
                key={r.key}
                n={i + 1}
                row={r}
                readOnly={readOnly}
                amount={preview.lines[filledRows.indexOf(r)]?.lineTotal}
                otherItemLine={
                  r.item_id
                    ? rows.findIndex((x) => x !== r && x.item_id === r.item_id)
                    : -1
                }
                onPatch={(p) => patchRow(r.key, p)}
                onRemove={() => removeRow(r.key)}
              />
            ))}

            {!readOnly && (
              <div className="px-3 py-2">
                <button className="text-sm text-accent hover:underline" onClick={addRow}>
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
              <input
                className="field h-8 w-28 text-right font-mono text-xs"
                inputMode="decimal"
                placeholder="0.00"
                value={invoiceDiscount}
                disabled={readOnly}
                onChange={(e) => {
                  setInvoiceDiscount(e.target.value);
                  setDirty(true);
                }}
              />
            </div>
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
  const results = useQuery({
    queryKey: ["party-search", q],
    queryFn: () => api<PartyListItem[]>(`/parties?q=${encodeURIComponent(q)}&role=customer`),
    enabled: open && q.trim().length >= 1,
  });

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
              onMouseDown={() => {
                onPick(p);
                setOpen(false);
                setQ("");
              }}
            >
              <span className="font-medium">{p.legal_name}</span>
              {p.default_state_code && (
                <span className="ml-2 text-[11px] text-muted">{p.default_state_code}</span>
              )}
            </button>
          ))}
        </div>
      )}
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
  otherItemLine,
  onPatch,
  onRemove,
}: {
  n: number;
  row: Row;
  readOnly: boolean;
  amount: string | undefined;
  /** 0-based index of another line already using this item, or -1 */
  otherItemLine: number;
  onPatch: (p: Partial<Row>) => void;
  onRemove: () => void;
}) {
  const [open, setOpen] = useState(false);
  const [typed, setTyped] = useState(row.description);

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
        className={`field h-9 text-sm ${fieldClass("item")}`}
        placeholder="type an item name…"
        value={typed}
        disabled={readOnly}
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
        <span className="pointer-events-none absolute right-2 top-1/2 -translate-y-1/2 text-[10px] text-[#3f7a4f]">
          ✓ matched
        </span>
      )}
    </div>
  );

  const discSeg = (
    <span className="inline-flex overflow-hidden rounded border border-line">
      {(["amt", "pct"] as DiscMode[]).map((m) => (
        <button
          key={m}
          type="button"
          disabled={readOnly}
          className={`px-1.5 text-[9px] font-bold ${
            row.discMode === m ? "bg-accent text-white" : "bg-card text-muted"
          }`}
          onClick={() => onPatch({ discMode: m })}
        >
          {m === "amt" ? "₹" : "%"}
        </button>
      ))}
    </span>
  );

  // ---- MOBILE card ----
  const mobile = (
    <div className="md:hidden">
      <div className="flex items-center justify-between px-3 pt-2 text-xs text-muted">
        <span>{n}</span>
        {filled &&
          (blocked ? (
            <span className="font-semibold text-danger">fix highlighted fields</span>
          ) : (
            <span className="font-semibold text-[#3f7a4f]">✓ ready</span>
          ))}
      </div>

      <div className="px-3 pt-1">{itemInput}</div>

      {row.description.trim() && !row.item_id && (
        <div className="mx-3 mt-2 rounded-md border border-[#ecdcb8] bg-[#fbf3e2] px-2.5 py-1.5 text-[11px] text-warn">
          Not in the catalogue — finalising creates “{row.description.trim()}” as a new item.{" "}
          <span className="ml-1 inline-flex overflow-hidden rounded border border-[#e2cfa0] align-middle">
            {(["piece", "kg"] as const).map((m) => (
              <button
                key={m}
                type="button"
                className={`px-1.5 py-0.5 text-[10px] font-bold ${
                  row.newMode === m ? "bg-warn text-white" : "bg-transparent"
                }`}
                onClick={() => onPatch({ newMode: m, uom: row.uom || (m === "kg" ? "kg" : "nos") })}
              >
                per {m}
              </button>
            ))}
          </span>
        </div>
      )}

      <div className="grid grid-cols-2 gap-3 p-3">
        <div>
          <label className="fl-m">Qty</label>
          <input
            className={`field h-10 text-right ${fieldClass("qty")}`}
            inputMode="decimal"
            placeholder="0"
            value={row.quantity}
            disabled={readOnly}
            onChange={(e) => onPatch({ quantity: e.target.value })}
          />
        </div>

        <div>
          <label className="fl-m">Unit</label>
          <select
            className={`field h-10 px-1 text-sm ${fieldClass("unit")}`}
            value={row.uom.trim().toLowerCase() || unitLabel}
            disabled={readOnly}
            onChange={(e) => onPatch({ uom: e.target.value })}
          >
            {unitChoices.map((u) => (
              <option key={u}>{u}</option>
            ))}
          </select>
        </div>

        <div>
          <label className="fl-m">Rate ₹/{unitLabel}</label>
          <input
            className={`field h-10 text-right ${fieldClass("rate")}`}
            inputMode="decimal"
            placeholder="0.00"
            value={row.unit_rate}
            disabled={readOnly}
            onChange={(e) => onPatch({ unit_rate: e.target.value })}
          />
          {rateGhost && <p className="mt-0.5 text-[10px] text-muted">{rateGhost}</p>}
        </div>

        <div>
          <label className="fl-m">Discount {discSeg}</label>
          <input
            className={`field h-10 text-right ${fieldClass("disc")}`}
            inputMode="decimal"
            placeholder="0"
            value={row.discount}
            disabled={readOnly}
            onChange={(e) => onPatch({ discount: e.target.value })}
          />
        </div>

        <div className="col-span-2">
          <label className="fl-m">HSN <span className="font-normal normal-case tracking-normal">optional</span></label>
          <input
            className={`field h-10 ${fieldClass("hsn")}`}
            placeholder="4 / 6 / 8 digits"
            value={row.hsn_code}
            disabled={readOnly}
            onChange={(e) => onPatch({ hsn_code: e.target.value })}
          />
        </div>
      </div>

      {!readOnly && problems.length > 0 && (
        <div className="mx-3 mb-2 rounded-md bg-[#fbf6ee] px-2.5 py-1.5 text-[11px]">
          {problems.map((p, i) => (
            <div key={i} className={p.block ? "text-danger" : "text-warn"}>
              {p.block ? "•" : "⚠"} {p.msg}
            </div>
          ))}
        </div>
      )}

      <div className="flex items-center justify-between border-t border-line px-3 py-2">
        <span className="font-serif text-base font-semibold">
          {amount ? inr(amount) : "—"}
          {working && (
            <span className="block font-sans text-[11px] font-normal text-muted">{working}</span>
          )}
        </span>
        {!readOnly && (
          <button
            className="h-9 w-9 rounded-md border border-line text-danger"
            title="Remove line"
            onClick={onRemove}
          >
            ×
          </button>
        )}
      </div>
    </div>
  );

  // ---- DESKTOP row ----
  const desktop = (
    <div className="hidden md:block">
      <div className="grid grid-cols-[24px_1fr_84px_66px_54px_84px_92px_92px_28px] items-center gap-2 px-3 py-2 text-sm">
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
        <span className="flex items-center justify-end gap-1">
          {discSeg}
          <input
            className={`field h-9 w-14 text-right text-xs ${fieldClass("disc")}`}
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

  return (
    <div className="border-b border-[#f3eee4]">
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
