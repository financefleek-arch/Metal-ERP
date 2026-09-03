import { useEffect, useRef, useState } from "react";
import { useMutation, useQueryClient } from "@tanstack/react-query";
import { api, ApiError } from "../lib/api";
import { useVocab } from "../lib/reference";
import type { Item, ItemType } from "../lib/types";
import { HsnPicker } from "./HsnPicker";

type Fields = { [k: string]: string };

const TEXT_FIELDS = [
  "name",
  "grade",
  "size_text",
  "thickness_mm",
  "width_mm",
  "length_mm",
  "secondary_uom",
  "conversion_factor",
  "weight_per_uom",
  "purchase_uom",
  "default_rate",
  "mrp",
  "default_discount_pct",
  "price_min",
  "price_max",
  "notes",
] as const;

function toFields(it: Item): Fields {
  const f: Fields = {
    name: it.name,
    item_type: it.item_type,
    category: it.category ?? "",
    uom: it.uom ?? "",
    hsn_code: it.hsn_code ?? "",
    metal: it.metal ?? "",
    shape: it.shape ?? "",
    finish: it.finish ?? "",
  };
  for (const k of TEXT_FIELDS) {
    const v = (it as unknown as Record<string, unknown>)[k];
    f[k] = v == null ? "" : String(v);
  }
  return f;
}

function toBody(f: Fields) {
  const g = (k: string) => f[k] ?? "";
  const num = (k: string) => (g(k).trim() === "" ? null : g(k).trim());
  const str = (k: string) => g(k) || null;
  return {
    name: g("name").trim(),
    item_type: g("item_type") as ItemType,
    category: str("category"),
    uom: str("uom"),
    hsn_code: str("hsn_code"),
    metal: str("metal"),
    shape: str("shape"),
    grade: str("grade"),
    size_text: str("size_text"),
    thickness_mm: num("thickness_mm"),
    width_mm: num("width_mm"),
    length_mm: num("length_mm"),
    finish: str("finish"),
    secondary_uom: str("secondary_uom"),
    conversion_factor: num("conversion_factor"),
    weight_per_uom: num("weight_per_uom"),
    purchase_uom: str("purchase_uom"),
    default_rate: num("default_rate"),
    mrp: num("mrp"),
    default_discount_pct: num("default_discount_pct"),
    price_min: num("price_min"),
    price_max: num("price_max"),
    notes: str("notes"),
  };
}

type SaveState = "clean" | "dirty" | "saving" | "saved" | "error";

export function ItemForm({
  item,
  onChanged,
  onDeleted,
}: {
  item: Item;
  onChanged: () => void;
  onDeleted: () => void;
}) {
  const qc = useQueryClient();
  const [v, setV] = useState<Fields>(() => toFields(item));
  const [saveState, setSaveState] = useState<SaveState>("clean");
  const [err, setErr] = useState<string | null>(null);
  const [menuOpen, setMenuOpen] = useState(false);
  const timer = useRef<number | undefined>(undefined);
  const loadedId = useRef(item.id);

  const categories = useVocab("categories");
  const uoms = useVocab("uoms");
  const metals = useVocab("metals");
  const shapes = useVocab("shapes");
  const finishes = useVocab("finishes");

  useEffect(() => {
    if (loadedId.current !== item.id) {
      loadedId.current = item.id;
      setV(toFields(item));
      setSaveState("clean");
      setErr(null);
      setMenuOpen(false);
    }
  }, [item]);

  const save = useMutation({
    mutationFn: (body: ReturnType<typeof toBody>) =>
      api<Item>(`/items/${item.id}`, { method: "PATCH", body }),
    onSuccess: () => {
      setSaveState("saved");
      onChanged();
      qc.invalidateQueries({ queryKey: ["items"] });
      qc.invalidateQueries({ queryKey: ["item-tree"] });
      qc.invalidateQueries({ queryKey: ["item-tree-leaves"] });
    },
    onError: (e) => {
      setSaveState("error");
      setErr(e instanceof ApiError ? e.message : "Save failed");
    },
  });

  const act = useMutation({
    mutationFn: async (kind: "confirm" | "delete") => {
      if (kind === "delete") await api<void>(`/items/${item.id}`, { method: "DELETE" });
      else await api<Item>(`/items/${item.id}/confirm`, { method: "POST" });
    },
    onSuccess: (_d, kind) => {
      qc.invalidateQueries({ queryKey: ["items"] });
      qc.invalidateQueries({ queryKey: ["item-tree"] });
      qc.invalidateQueries({ queryKey: ["item-tree-leaves"] });
      if (kind === "delete") onDeleted();
      else onChanged();
    },
    onError: (e) => setErr(e instanceof ApiError ? e.message : "Action failed"),
  });

  function patch(next: Record<string, string>) {
    const merged: Fields = { ...v, ...next };
    setV(merged);
    setErr(null);
    if (!(merged.name ?? "").trim()) {
      setSaveState("dirty");
      setErr("Name is required");
      window.clearTimeout(timer.current);
      return;
    }
    const pmin = parseFloat(merged.price_min ?? "");
    const pmax = parseFloat(merged.price_max ?? "");
    if (isFinite(pmin) && isFinite(pmax) && pmin > pmax) {
      setSaveState("dirty");
      setErr("Optimum min must be ≤ max");
      window.clearTimeout(timer.current);
      return;
    }
    setSaveState("dirty");
    window.clearTimeout(timer.current);
    timer.current = window.setTimeout(() => {
      setSaveState("saving");
      save.mutate(toBody(merged));
    }, 600);
  }

  useEffect(() => () => window.clearTimeout(timer.current), []);

  const referenced = item.document_count > 0 || item.times_billed > 0;
  const isMrp = v.item_type === "mrp";
  const saveHint = {
    clean: "No unsaved changes",
    dirty: "Editing…",
    saving: "Saving…",
    saved: "Saved",
    error: err ?? "Save failed",
  }[saveState];

  return (
    <div className="flex flex-col gap-4">
      <div className="flex items-start justify-between gap-3">
        <div>
          <h2 className="font-serif text-lg font-semibold">{item.name}</h2>
          <p className="mt-0.5 text-[11px] text-muted">
            {item.source === "auto_from_purchase"
              ? "◆ from inward bill"
              : item.source === "auto_from_invoice"
                ? "from an invoice"
                : "added manually"}
            {` · billed ${item.times_billed}×`}
            {item.last_purchase_rate != null &&
              ` · last purchased ₹${item.last_purchase_rate}`}
            {" · "}
            {item.status === "confirmed" ? (
              <span className="text-ok">✓ confirmed</span>
            ) : item.status === "archived" ? (
              <span className="text-danger">archived</span>
            ) : (
              <span className="text-warn">unconfirmed</span>
            )}
          </p>
        </div>
        <div className="relative">
          <button
            className="rounded-md border border-line bg-card px-2 py-1 text-sm text-muted hover:text-ink"
            onClick={() => setMenuOpen((o) => !o)}
            aria-label="More actions"
          >
            ⋯
          </button>
          {menuOpen && (
            <div className="absolute right-0 z-10 mt-1 min-w-[180px] max-w-[calc(100vw-2rem)] overflow-hidden rounded-lg border border-line bg-card shadow-xl">
              {item.status !== "confirmed" && (
                <button
                  className="block w-full px-3 py-2 text-left text-xs hover:bg-ground"
                  onClick={() => {
                    setMenuOpen(false);
                    act.mutate("confirm");
                  }}
                >
                  Confirm
                </button>
              )}
              <button
                className="block w-full px-3 py-2 text-left text-xs text-danger hover:bg-ground disabled:text-muted disabled:hover:bg-transparent"
                disabled={referenced}
                title={referenced ? "On documents — archive instead" : undefined}
                onClick={() => {
                  setMenuOpen(false);
                  if (confirm(`Delete "${item.name}"? This can't be undone.`)) act.mutate("delete");
                }}
              >
                Delete{referenced ? " · blocked" : ""}
              </button>
            </div>
          )}
        </div>
      </div>

      {/* Identity */}
      <Section title="Identity">
        <div className="sm:col-span-2 lg:col-span-3">
          <Label>Name *</Label>
          <input className="field" value={v.name} onChange={(e) => patch({ name: e.target.value })} />
          <p className="mt-1 font-mono text-[10px] text-muted">
            normalized → {item.name_normalized}
          </p>
        </div>
        <Field label="Metal">
          <Select value={v.metal} onChange={(x) => patch({ metal: x })} options={metals.data} />
        </Field>
        <Field label="Shape">
          <Select value={v.shape} onChange={(x) => patch({ shape: x })} options={shapes.data} />
        </Field>
        <Field label="Grade">
          <input className="field" value={v.grade} onChange={(e) => patch({ grade: e.target.value })} />
        </Field>
        <Field label="Size (as written)">
          <input
            className="field"
            value={v.size_text}
            onChange={(e) => patch({ size_text: e.target.value })}
          />
        </Field>
        <Field label="Thickness (mm)">
          <input
            className="field font-mono"
            inputMode="decimal"
            value={v.thickness_mm}
            onChange={(e) => patch({ thickness_mm: e.target.value })}
          />
        </Field>
        <Field label="Finish">
          <Select value={v.finish} onChange={(x) => patch({ finish: x })} options={finishes.data} />
        </Field>
        <Field label="Width (mm)">
          <input
            className="field font-mono"
            inputMode="decimal"
            value={v.width_mm}
            onChange={(e) => patch({ width_mm: e.target.value })}
          />
        </Field>
        <Field label="Length (mm)">
          <input
            className="field font-mono"
            inputMode="decimal"
            value={v.length_mm}
            onChange={(e) => patch({ length_mm: e.target.value })}
          />
        </Field>
      </Section>

      {/* Classification */}
      <Section title="Classification">
        <Field label="Type">
          <select
            className="field"
            value={v.item_type}
            onChange={(e) => patch({ item_type: e.target.value })}
          >
            <option value="bulk">⚖ BULK</option>
            <option value="mrp">📦 MRP</option>
          </select>
        </Field>
        <Field label="Category">
          <Select
            value={v.category}
            onChange={(x) => patch({ category: x })}
            options={categories.data}
          />
        </Field>
        <Field label="HSN">
          <HsnPicker
            value={v.hsn_code}
            onChange={(code) => patch({ hsn_code: code })}
          />
        </Field>
      </Section>

      {/* Units & conversion */}
      <Section title="Units & conversion" note="stored now; invoice-editor wiring is a later slice">
        <Field label="Bill in (UOM)">
          <Select value={v.uom} onChange={(x) => patch({ uom: x })} options={uoms.data} />
        </Field>
        <Field label="Also counted in">
          <Select
            value={v.secondary_uom}
            onChange={(x) => patch({ secondary_uom: x })}
            options={uoms.data}
          />
        </Field>
        <Field label="Per secondary → primary">
          <input
            className="field font-mono"
            inputMode="decimal"
            placeholder="e.g. 6.2"
            value={v.conversion_factor}
            onChange={(e) => patch({ conversion_factor: e.target.value })}
          />
        </Field>
        <Field label="Weight per secondary (kg)">
          <input
            className="field font-mono"
            inputMode="decimal"
            value={v.weight_per_uom}
            onChange={(e) => patch({ weight_per_uom: e.target.value })}
          />
        </Field>
        <Field label="Purchase UOM">
          <Select
            value={v.purchase_uom}
            onChange={(x) => patch({ purchase_uom: x })}
            options={uoms.data}
          />
        </Field>
      </Section>

      {/* Price */}
      <Section title="Price">
        <Field label="Default rate">
          <input
            className="field font-mono"
            inputMode="decimal"
            value={v.default_rate}
            onChange={(e) => patch({ default_rate: e.target.value })}
          />
        </Field>
        <Field label="Optimum min">
          <input
            className="field font-mono"
            inputMode="decimal"
            value={v.price_min}
            onChange={(e) => patch({ price_min: e.target.value })}
          />
        </Field>
        <Field label="Optimum max">
          <input
            className="field font-mono"
            inputMode="decimal"
            value={v.price_max}
            onChange={(e) => patch({ price_max: e.target.value })}
          />
        </Field>
        {isMrp && (
          <>
            <Field label="MRP">
              <input
                className="field font-mono"
                inputMode="decimal"
                value={v.mrp}
                onChange={(e) => patch({ mrp: e.target.value })}
              />
            </Field>
            <Field label="Default discount %">
              <input
                className="field font-mono"
                inputMode="decimal"
                value={v.default_discount_pct}
                onChange={(e) => patch({ default_discount_pct: e.target.value })}
              />
            </Field>
          </>
        )}
        <div className="flex flex-wrap gap-x-6 gap-y-1 text-[11px] text-muted sm:col-span-2 lg:col-span-3">
          {item.gst_rate != null && (
            <span>
              GST <strong>{item.gst_rate}%</strong>
              {item.hsn_code ? ` · from HSN ${item.hsn_code}` : ""}
              <span className="text-faint"> · not on M1 sales invoices</span>
            </span>
          )}
          {item.rate_in_band === false && (
            <span className="text-warn">⚠ default rate is outside the optimum band</span>
          )}
          {item.last_rate != null && <span>last sold ₹{item.last_rate}</span>}
        </div>
      </Section>

      <Section title="Notes">
        <div className="sm:col-span-2 lg:col-span-3">
          <textarea
            className="field h-16 resize-y py-2"
            value={v.notes}
            onChange={(e) => patch({ notes: e.target.value })}
          />
        </div>
      </Section>

      <div className="sticky bottom-0 -mx-4 flex items-center gap-2 border-t border-line bg-card/95 px-4 py-2 text-[11px] text-muted backdrop-blur md:static md:mx-0 md:border-0 md:bg-transparent md:px-0 md:py-0 md:backdrop-blur-none">
        <span
          className={`inline-block h-1.5 w-1.5 rounded-full ${
            saveState === "saved"
              ? "bg-ok"
              : saveState === "error"
                ? "bg-danger"
                : saveState === "saving" || saveState === "dirty"
                  ? "bg-warn"
                  : "bg-line"
          }`}
        />
        {saveHint}
      </div>
    </div>
  );
}

function Section({
  title,
  note,
  children,
}: {
  title: string;
  note?: string;
  children: React.ReactNode;
}) {
  return (
    <div className="border-t border-line pt-3">
      <p className="mb-2 text-[10px] uppercase tracking-[0.06em] text-muted">
        {title}
        {note && <span className="ml-2 normal-case tracking-normal text-faint">· {note}</span>}
      </p>
      <div className="grid grid-cols-1 gap-x-3 gap-y-2.5 sm:grid-cols-2 lg:grid-cols-3">
        {children}
      </div>
    </div>
  );
}

function Field({ label, children }: { label: string; children: React.ReactNode }) {
  return (
    <div>
      <Label>{label}</Label>
      {children}
    </div>
  );
}

function Label({ children }: { children: React.ReactNode }) {
  return (
    <label className="mb-1 block text-[9px] uppercase tracking-[0.05em] text-muted">{children}</label>
  );
}

function Select({
  value,
  onChange,
  options,
}: {
  value: string;
  onChange: (v: string) => void;
  options: string[] | undefined;
}) {
  return (
    <select className="field" value={value} onChange={(e) => onChange(e.target.value)}>
      <option value="">—</option>
      {(options ?? []).map((o) => (
        <option key={o} value={o}>
          {o}
        </option>
      ))}
      {value && !(options ?? []).includes(value) && <option value={value}>{value}</option>}
    </select>
  );
}
