import { useEffect, useRef, useState } from "react";
import { useMutation, useQueryClient } from "@tanstack/react-query";
import { api, ApiError } from "../lib/api";
import { gstinError, panError } from "../lib/reference";
import { missingLabel } from "../lib/format";
import type { Party, PartyRole } from "../lib/types";
import { StateSelect } from "./StateSelect";

interface Fields {
  legal_name: string;
  role: PartyRole;
  phone: string;
  email: string;
  pan: string;
  gstin: string;
  default_state_code: string;
  addr_line1: string;
  addr_city: string;
  addr_state_code: string;
  addr_pincode: string;
}

function toFields(p: Party): Fields {
  const a = p.addresses?.[0];
  return {
    legal_name: p.legal_name ?? "",
    role: p.role ?? "customer",
    phone: p.phone ?? "",
    email: p.email ?? "",
    pan: p.pan ?? "",
    gstin: p.gstin ?? "",
    default_state_code: p.default_state_code ?? "",
    addr_line1: a?.line1 ?? "",
    addr_city: a?.city ?? "",
    addr_state_code: a?.state_code ?? "",
    addr_pincode: a?.pincode ?? "",
  };
}

function toBody(v: Fields) {
  const hasAddr = v.addr_line1 || v.addr_city || v.addr_state_code || v.addr_pincode;
  return {
    legal_name: v.legal_name.trim(),
    role: v.role,
    phone: v.phone || null,
    email: v.email || null,
    pan: v.pan.trim().toUpperCase() || null,
    gstin: v.gstin.trim().toUpperCase() || null,
    default_state_code: v.default_state_code || null,
    addresses: hasAddr
      ? [
          {
            type: "both" as const,
            line1: v.addr_line1 || null,
            line2: null,
            line3: null,
            city: v.addr_city || null,
            state_code: v.addr_state_code || null,
            pincode: v.addr_pincode || null,
            is_default: true,
          },
        ]
      : [],
  };
}

type SaveState = "clean" | "dirty" | "saving" | "saved" | "error";

/**
 * Inline detail-pane editor for an existing party. No Save button — every
 * edit debounce-autosaves via PATCH. Client-side PAN/GSTIN hints block the
 * autosave until fixed (the server would 422 anyway).
 */
export function PartyForm({
  party,
  onChanged,
  onDeleted,
}: {
  party: Party;
  onChanged: (p: Party) => void;
  onDeleted: () => void;
}) {
  const qc = useQueryClient();
  const [v, setV] = useState<Fields>(() => toFields(party));
  const [saveState, setSaveState] = useState<SaveState>("clean");
  const [errMsg, setErrMsg] = useState<string | null>(null);
  const [menuOpen, setMenuOpen] = useState(false);
  const timer = useRef<number | undefined>(undefined);
  const loadedId = useRef(party.id);

  // Reset when a different party is selected.
  useEffect(() => {
    if (loadedId.current !== party.id) {
      loadedId.current = party.id;
      setV(toFields(party));
      setSaveState("clean");
      setErrMsg(null);
      setMenuOpen(false);
    }
  }, [party]);

  const save = useMutation({
    mutationFn: (body: ReturnType<typeof toBody>) =>
      api<Party>(`/parties/${party.id}`, { method: "PATCH", body }),
    onSuccess: (p) => {
      setSaveState("saved");
      onChanged(p);
      qc.invalidateQueries({ queryKey: ["parties"] });
    },
    onError: (e) => {
      setSaveState("error");
      setErrMsg(e instanceof ApiError ? e.message : "Save failed");
    },
  });

  const del = useMutation({
    mutationFn: () => api<void>(`/parties/${party.id}`, { method: "DELETE" }),
    onSuccess: () => {
      qc.invalidateQueries({ queryKey: ["parties"] });
      onDeleted();
    },
    onError: (e) => setErrMsg(e instanceof ApiError ? e.message : "Delete failed"),
  });

  const setStatus = useMutation({
    mutationFn: (status: "active" | "archived") =>
      api<Party>(`/parties/${party.id}`, { method: "PATCH", body: { status } }),
    onSuccess: (p) => {
      qc.invalidateQueries({ queryKey: ["parties"] });
      onChanged(p);
    },
  });

  function patch(next: Partial<Fields>) {
    const merged = { ...v, ...next };
    setV(merged);
    setErrMsg(null);

    const pe = panError(merged.pan);
    const ge = gstinError(merged.gstin);
    if (!merged.legal_name.trim() || pe || ge) {
      setSaveState("dirty");
      setErrMsg(pe ?? ge ?? "Name is required");
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

  const missing = party.completeness.missing;
  const referenced = party.document_count > 0;

  const saveHint = {
    clean: "No unsaved changes",
    dirty: "Editing…",
    saving: "Saving…",
    saved: "Saved",
    error: errMsg ?? "Save failed",
  }[saveState];

  return (
    <div className="flex flex-col gap-4">
      <div className="flex items-start justify-between gap-3">
        <div>
          <h2 className="font-serif text-lg font-semibold">{party.legal_name}</h2>
          <p className="mt-0.5 text-[11px] text-muted">
            {party.source === "inward_bill" ? (
              <span className="text-accent">◆ from inward bill</span>
            ) : party.source === "tally_import" ? (
              "from Tally import"
            ) : (
              "added manually"
            )}
            {party.document_count > 0 && ` · on ${party.document_count} document${party.document_count === 1 ? "" : "s"}`}
            {" · "}
            {party.completeness.complete ? (
              <span className="text-ok">✓ complete</span>
            ) : (
              <span className="text-warn">⚠ details pending · missing: {missingLabel(missing)}</span>
            )}
            {party.status === "archived" && <span className="text-danger"> · archived</span>}
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
            <div className="absolute right-0 z-10 mt-1 min-w-[190px] overflow-hidden rounded-lg border border-line bg-card shadow-xl">
              {party.status === "active" ? (
                <button
                  className="block w-full px-3 py-2 text-left text-xs text-danger hover:bg-ground"
                  onClick={() => {
                    setMenuOpen(false);
                    setStatus.mutate("archived");
                  }}
                >
                  Archive
                </button>
              ) : (
                <button
                  className="block w-full px-3 py-2 text-left text-xs hover:bg-ground"
                  onClick={() => {
                    setMenuOpen(false);
                    setStatus.mutate("active");
                  }}
                >
                  Unarchive
                </button>
              )}
              <button
                className="block w-full px-3 py-2 text-left text-xs text-danger hover:bg-ground disabled:text-muted disabled:hover:bg-transparent"
                disabled={referenced}
                title={referenced ? `On ${party.document_count} documents — archive instead` : undefined}
                onClick={() => {
                  setMenuOpen(false);
                  if (confirm(`Delete "${party.legal_name}"? This can't be undone.`)) del.mutate();
                }}
              >
                Delete{referenced ? ` · blocked (${party.document_count} docs)` : ""}
              </button>
            </div>
          )}
        </div>
      </div>

      <div className="grid grid-cols-2 gap-x-4 gap-y-3">
        <div className="col-span-2">
          <label className="label">Legal name *</label>
          <input
            className="field"
            value={v.legal_name}
            onChange={(e) => patch({ legal_name: e.target.value })}
          />
        </div>
        <div>
          <label className="label">Role</label>
          <select
            className="field"
            value={v.role}
            onChange={(e) => patch({ role: e.target.value as PartyRole })}
          >
            <option value="customer">Customer</option>
            <option value="supplier">Supplier</option>
            <option value="both">Both</option>
          </select>
        </div>
        <div>
          <label className="label">Phone</label>
          <input className="field" value={v.phone} onChange={(e) => patch({ phone: e.target.value })} />
        </div>
        <div>
          <label className="label">Email</label>
          <input
            className="field"
            type="email"
            value={v.email}
            onChange={(e) => patch({ email: e.target.value })}
          />
        </div>
        <div>
          <label className="label">PAN</label>
          <input
            className="field uppercase"
            placeholder="AAAAA9999A"
            value={v.pan}
            onChange={(e) => patch({ pan: e.target.value.toUpperCase() })}
          />
        </div>
        <div>
          <label className="label">GSTIN</label>
          <input
            className="field uppercase"
            placeholder="27AAAAA9999A1Z5"
            value={v.gstin}
            onChange={(e) => patch({ gstin: e.target.value.toUpperCase() })}
          />
        </div>
        <div>
          <label className="label">Default state</label>
          <StateSelect
            value={v.default_state_code}
            onChange={(e) => patch({ default_state_code: e.target.value })}
          />
        </div>
      </div>

      <div className="border-t border-line pt-4">
        <p className="label">
          Address
          {!party.completeness.complete && (
            <span className="ml-2 normal-case tracking-normal text-warn">
              · needed to clear “details pending”
            </span>
          )}
        </p>
        <div className="space-y-3">
          <input
            className="field"
            placeholder="Address line"
            value={v.addr_line1}
            onChange={(e) => patch({ addr_line1: e.target.value })}
          />
          <div className="grid grid-cols-3 gap-3">
            <input
              className="field"
              placeholder="City"
              value={v.addr_city}
              onChange={(e) => patch({ addr_city: e.target.value })}
            />
            <StateSelect
              value={v.addr_state_code}
              onChange={(e) => patch({ addr_state_code: e.target.value })}
            />
            <input
              className="field"
              placeholder="PIN"
              value={v.addr_pincode}
              onChange={(e) => patch({ addr_pincode: e.target.value })}
            />
          </div>
        </div>
      </div>

      <div className="flex items-center gap-2 text-[11px] text-muted">
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
