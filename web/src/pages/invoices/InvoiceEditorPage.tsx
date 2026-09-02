import { useEffect, useMemo, useState } from "react";
import { useNavigate, useParams } from "react-router-dom";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { api, ApiError, getToken } from "../../lib/api";
import { computePreview, inr } from "../../lib/previewTotal";
import { useVocab } from "../../lib/reference";
import type {
  Invoice,
  InvoiceLineIn,
  ItemListItem,
  PartyListItem,
  ResolveCandidate,
  ResolveResult,
} from "../../lib/types";

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
    unit_rate: "",
    discount: "",
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
    unit_rate: String(l.unit_rate ?? ""),
    discount: l.discount && Number(l.discount) ? String(l.discount) : "",
  }));
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
    discount: r.discount.trim() || "0",
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

  const uoms = useVocab("uoms");

  const preview = useMemo(
    () =>
      computePreview({
        lines: rows
          .filter((r) => r.description.trim())
          .map((r) => ({ quantity: r.quantity, unitRate: r.unit_rate, discount: r.discount })),
        invoiceDiscount,
      }),
    [rows, invoiceDiscount],
  );

  const filledRows = rows.filter((r) => r.description.trim());
  const badRows = filledRows.filter(
    (r) => !(Number(r.quantity) > 0) || !(Number(r.unit_rate) > 0),
  );
  const localBlockers: string[] = [];
  if (!partyId) localBlockers.push("select a party");
  if (!filledRows.length) localBlockers.push("add at least one line with an item");
  badRows.forEach((r) => {
    const n = rows.indexOf(r) + 1;
    localBlockers.push(`line ${n}: needs quantity and rate`);
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
            <div className="hidden grid-cols-[24px_1fr_84px_70px_64px_88px_78px_88px_28px] gap-2 border-b border-line bg-ground px-3 py-2 text-[10px] font-semibold uppercase text-muted md:grid">
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
                uoms={uoms.data ?? []}
                amount={preview.lines[filledRows.indexOf(r)]?.lineTotal}
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
// line row — item type-ahead through POST /api/items/resolve
//   Runs the typed text through the tenant synonym map + alias table + the
//   confidence ladder, so "balti" finds the "Bucket" item. `exact` hits
//   auto-adopt on Enter/blur; `alias`/`fuzzy` need a click. On SQLite (no
//   pg_trgm) a non-exact/alias query returns no candidates — the "use as
//   new item" row still lets the line through (created at finalize).
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
  uoms,
  amount,
  onPatch,
  onRemove,
}: {
  n: number;
  row: Row;
  readOnly: boolean;
  uoms: string[];
  amount: string | undefined;
  onPatch: (p: Partial<Row>) => void;
  onRemove: () => void;
}) {
  const [open, setOpen] = useState(false);
  const [typed, setTyped] = useState(row.description);

  useEffect(() => setTyped(row.description), [row.description]);

  const debounced = useDebounced(typed.trim(), 200);

  const search = useQuery({
    queryKey: ["item-resolve", debounced, row.hsn_code.trim()],
    queryFn: () => {
      const p = new URLSearchParams({ description: debounced });
      if (row.hsn_code.trim()) p.set("hsn", row.hsn_code.trim());
      return api<ResolveResult>(`/items/resolve?${p.toString()}`, { method: "POST" });
    },
    enabled: open && debounced.length >= 1 && !row.item_id,
  });

  const method = search.data?.method ?? null;
  const candidates = search.data?.candidates ?? [];

  function pick(it: ItemListItem) {
    onPatch({
      item_id: it.id,
      description: it.name,
      hsn_code: it.hsn_code ?? row.hsn_code,
      uom: it.uom ?? row.uom,
      unit_rate:
        it.last_rate ?? it.default_rate ?? row.unit_rate ?? "",
    });
    setTyped(it.name);
    setOpen(false);
  }

  function createNew() {
    // keep the typed text as a free-form line; item row is created at finalize
    onPatch({ item_id: null, description: typed.trim() });
    setOpen(false);
  }

  return (
    <div className="grid grid-cols-2 gap-2 border-b border-[#f3eee4] px-3 py-3 md:grid-cols-[24px_1fr_84px_70px_64px_88px_78px_88px_28px] md:items-center md:py-2">
      <span className="text-xs text-muted md:text-center">{n}</span>

      <div className="relative col-span-2 md:col-span-1">
        <input
          className="field h-9 text-sm"
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
            // silent auto-adopt on an unambiguous exact match; anything
            // weaker waits for a click.
            if (method === "exact" && candidates[0] && !row.item_id) pick(candidates[0]);
            setTimeout(() => setOpen(false), 160);
          }}
          onKeyDown={(e) => {
            if (e.key === "Enter") {
              // Enter adopts the top candidate only when it's an exact hit.
              if (method === "exact" && candidates[0]) {
                e.preventDefault();
                pick(candidates[0]);
              }
            } else if (e.key === "Escape") {
              setOpen(false);
            }
          }}
        />
        {open && !row.item_id && typed.trim() && (
          <div className="absolute z-20 mt-1 max-h-60 w-[min(380px,90vw)] overflow-y-auto rounded-md border border-line bg-card shadow-lg">
            {candidates.map((c: ResolveCandidate) => {
              const badge = method ? METHOD_BADGE[method] : undefined;
              return (
                <button
                  key={c.id}
                  className="flex w-full items-center justify-between border-b border-[#f3eee4] px-3 py-2 text-left text-sm hover:bg-accent-soft"
                  onMouseDown={() => pick(c)}
                >
                  <span>
                    {c.name}{" "}
                    <span
                      className={`ml-1 rounded px-1 py-0.5 text-[9px] ${
                        c.item_type === "bulk"
                          ? "bg-[#e3eef2] text-accent"
                          : "bg-[#f1e7d6] text-warn"
                      }`}
                    >
                      {c.item_type === "bulk" ? "⚖ BULK" : "📦 MRP"}
                    </span>
                    {badge && (
                      <span className={`ml-1 rounded px-1 py-0.5 text-[9px] ${badge.cls}`}>
                        {badge.label}
                        {method === "fuzzy" ? ` ${c.score.toFixed(2)}` : ""}
                      </span>
                    )}
                  </span>
                  <span className="text-[11px] text-muted">
                    {c.last_rate ? `₹${c.last_rate}` : ""} · {c.times_billed}×
                  </span>
                </button>
              );
            })}
            {search.data?.weak && !candidates.length && (
              <div className="px-3 py-2 text-[11px] text-muted">No strong match.</div>
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

      <input
        className="field h-9 text-xs"
        placeholder="HSN"
        value={row.hsn_code}
        disabled={readOnly}
        onChange={(e) => onPatch({ hsn_code: e.target.value })}
      />
      <input
        className="field h-9 text-right text-sm"
        inputMode="decimal"
        placeholder="0"
        value={row.quantity}
        disabled={readOnly}
        onChange={(e) => onPatch({ quantity: e.target.value })}
      />
      <select
        className="field h-9 px-1 text-xs"
        value={row.uom}
        disabled={readOnly}
        onChange={(e) => onPatch({ uom: e.target.value })}
      >
        <option value="">—</option>
        {uoms.map((u) => (
          <option key={u}>{u}</option>
        ))}
      </select>
      <input
        className="field h-9 text-right text-sm"
        inputMode="decimal"
        placeholder="0"
        value={row.unit_rate}
        disabled={readOnly}
        onChange={(e) => onPatch({ unit_rate: e.target.value })}
      />
      <input
        className="field h-9 text-right text-xs"
        inputMode="decimal"
        placeholder="0"
        value={row.discount}
        disabled={readOnly}
        onChange={(e) => onPatch({ discount: e.target.value })}
      />
      <span className="text-right font-mono text-sm">{amount ? inr(amount) : "—"}</span>
      {!readOnly ? (
        <button
          className="text-danger md:text-center"
          title="Remove line"
          onClick={onRemove}
        >
          ×
        </button>
      ) : (
        <span />
      )}
    </div>
  );
}
