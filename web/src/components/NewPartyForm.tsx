import { useState } from "react";
import { useMutation, useQueryClient } from "@tanstack/react-query";
import { api, ApiError } from "../lib/api";
import { useDebounced } from "../lib/useDebounced";
import { SimilarParties } from "./SimilarParties";
import { isDup409, type PartyDuplicate409, type PartyMatchRef } from "../lib/types";
import {
  MAXLEN,
  addressLineError,
  cityError,
  emailError,
  gstinError,
  legalNameError,
  normalizePhone,
  panError,
  phoneError,
  pincodeError,
} from "../lib/reference";
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
  opening_balance: string;
  opening_balance_as_of: string;
  addr_line1: string;
  addr_city: string;
  addr_state_code: string;
  addr_pincode: string;
}

const EMPTY: Fields = {
  legal_name: "",
  role: "customer",
  phone: "",
  email: "",
  pan: "",
  gstin: "",
  default_state_code: "",
  opening_balance: "",
  opening_balance_as_of: "",
  addr_line1: "",
  addr_city: "",
  addr_state_code: "",
  addr_pincode: "",
};

/**
 * Detail-pane form for a not-yet-saved party. Unlike the edit form this
 * has an explicit Create — there's no row to autosave into until POST.
 */
export function NewPartyForm({
  onCreated,
  onCancel,
}: {
  onCreated: (p: Party) => void;
  onCancel: () => void;
}) {
  const qc = useQueryClient();
  const [v, setV] = useState<Fields>(EMPTY);
  const [serverError, setServerError] = useState<string | null>(null);
  // Set from a `party_maybe_exists` 409 — the operator must then either pick a
  // candidate or press "Create new anyway", which re-POSTs with ?force=true.
  const [dupWarn, setDupWarn] = useState<PartyDuplicate409 | null>(null);

  const debName = useDebounced(v.legal_name, 300);
  const debGstin = useDebounced(v.gstin, 400);
  const debPhone = useDebounced(v.phone, 400);

  const create = useMutation<Party, unknown, boolean>({
    mutationFn: (force) => {
      const hasAddr = v.addr_line1 || v.addr_city || v.addr_state_code || v.addr_pincode;
      return api<Party>(`/parties${force ? "?force=true" : ""}`, {
        method: "POST",
        body: {
          legal_name: v.legal_name.trim(),
          role: v.role,
          phone: v.phone || null,
          email: v.email || null,
          pan: v.pan.trim().toUpperCase() || null,
          gstin: v.gstin.trim().toUpperCase() || null,
          default_state_code: v.default_state_code || null,
          opening_balance: v.opening_balance.trim() === "" ? "0" : v.opening_balance.trim(),
          opening_balance_as_of: v.opening_balance_as_of || null,
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
        },
      });
    },
    onSuccess: (p) => {
      qc.invalidateQueries({ queryKey: ["parties"] });
      onCreated(p);
    },
    onError: (e) => {
      if (e instanceof ApiError && e.status === 409 && isDup409(e.detail)) {
        setDupWarn(e.detail);
        setServerError(null);
      } else {
        setDupWarn(null);
        setServerError(e instanceof ApiError ? e.message : "Create failed");
      }
    },
  });

  const errs = {
    legal_name: legalNameError(v.legal_name),
    phone: phoneError(v.phone),
    email: emailError(v.email),
    pan: panError(v.pan),
    gstin: gstinError(v.gstin),
    addr_line1: addressLineError(v.addr_line1),
    addr_city: cityError(v.addr_city),
    addr_pincode: pincodeError(v.addr_pincode),
  };
  const canCreate =
    !!v.legal_name.trim() && !Object.values(errs).some(Boolean) && !create.isPending;
  // A `party_maybe_exists` warning must be dismissed by an explicit choice.
  const blockedByDup = dupWarn?.code === "party_maybe_exists";

  function pickExisting(p: PartyMatchRef) {
    // Hydrate the picked row into a full Party via GET, then hand it up.
    api<Party>(`/parties/${p.id}`)
      .then((full) => {
        qc.invalidateQueries({ queryKey: ["parties"] });
        onCreated(full);
      })
      .catch((e) => setServerError(e instanceof ApiError ? e.message : "Could not open party"));
  }

  return (
    <form
      className="flex flex-col gap-4"
      onSubmit={(e) => {
        e.preventDefault();
        setServerError(null);
        if (canCreate && !blockedByDup) create.mutate(false);
      }}
    >
      <div className="flex flex-wrap items-center justify-between gap-2">
        <h2 className="font-serif text-lg font-semibold text-accent">New party</h2>
        <div className="flex gap-2">
          <button type="button" className="btn-ghost" onClick={onCancel}>
            Cancel
          </button>
          <button type="submit" className="btn-primary" disabled={!canCreate || blockedByDup}>
            {create.isPending ? "Creating…" : "Create"}
          </button>
        </div>
      </div>

      <div className="grid grid-cols-1 gap-x-4 gap-y-3 sm:grid-cols-2">
        <div className="sm:col-span-2">
          <label className="label">Legal name *</label>
          <input
            className="field"
            autoFocus
            placeholder="Type the party name…"
            maxLength={MAXLEN.legalName}
            value={v.legal_name}
            onChange={(e) => {
              setDupWarn(null);
              setV({ ...v, legal_name: e.target.value });
            }}
          />
          {v.legal_name.trim() && errs.legal_name && <p className="err">{errs.legal_name}</p>}
          {/* live "did you mean" — a matched candidate short-circuits create */}
          {!dupWarn && (
            <div className="mt-2">
              <SimilarParties
                name={debName}
                gstin={debGstin}
                phone={debPhone}
                onUse={pickExisting}
              />
            </div>
          )}
        </div>
        <div>
          <label className="label">Role</label>
          <select
            className="field"
            value={v.role}
            onChange={(e) => setV({ ...v, role: e.target.value as PartyRole })}
          >
            <option value="customer">Customer</option>
            <option value="supplier">Supplier</option>
            <option value="both">Both</option>
          </select>
        </div>
        <div>
          <label className="label">Phone</label>
          <input
            className="field"
            inputMode="tel"
            maxLength={MAXLEN.phone}
            value={v.phone}
            onChange={(e) => setV({ ...v, phone: e.target.value })}
            onBlur={(e) => {
              const norm = normalizePhone(e.target.value);
              if (norm && norm !== e.target.value) setV({ ...v, phone: norm });
            }}
          />
          {errs.phone && <p className="err">{errs.phone}</p>}
        </div>
        <div>
          <label className="label">Email</label>
          <input
            className="field"
            type="email"
            maxLength={MAXLEN.email}
            value={v.email}
            onChange={(e) => setV({ ...v, email: e.target.value })}
          />
          {errs.email && <p className="err">{errs.email}</p>}
        </div>
        <div>
          <label className="label">PAN</label>
          <input
            className="field uppercase"
            placeholder="AAAAA9999A"
            maxLength={MAXLEN.pan}
            value={v.pan}
            onChange={(e) => setV({ ...v, pan: e.target.value.toUpperCase() })}
          />
          {errs.pan && <p className="err">{errs.pan}</p>}
        </div>
        <div>
          <label className="label">GSTIN</label>
          <input
            className="field uppercase"
            placeholder="27AAAAA9999A1Z5"
            maxLength={MAXLEN.gstin}
            value={v.gstin}
            onChange={(e) => setV({ ...v, gstin: e.target.value.toUpperCase() })}
          />
          {errs.gstin && <p className="err">{errs.gstin}</p>}
        </div>
        <div>
          <label className="label">Default state</label>
          <StateSelect
            value={v.default_state_code}
            onChange={(e) => setV({ ...v, default_state_code: e.target.value })}
          />
        </div>
        <div>
          <label className="label">Opening balance</label>
          <div className="relative">
            <span className="pointer-events-none absolute left-3 top-1/2 -translate-y-1/2 text-sm text-muted">
              ₹
            </span>
            <input
              className="field pl-7"
              inputMode="decimal"
              placeholder="0.00"
              value={v.opening_balance}
              onChange={(e) => setV({ ...v, opening_balance: e.target.value })}
            />
          </div>
          <p className="mt-1 text-[11px] text-muted">
            What they owed you before you started billing here.
          </p>
        </div>
        <div>
          <label className="label">Opening balance as of</label>
          <input
            type="date"
            className="field"
            value={v.opening_balance_as_of}
            onChange={(e) => setV({ ...v, opening_balance_as_of: e.target.value })}
          />
        </div>
      </div>

      <div className="border-t border-line pt-4">
        <p className="label">Address</p>
        <div className="space-y-3">
          <div>
            <input
              className="field"
              placeholder="Address line"
              maxLength={MAXLEN.addressLine}
              value={v.addr_line1}
              onChange={(e) => setV({ ...v, addr_line1: e.target.value })}
            />
            {errs.addr_line1 && <p className="err">{errs.addr_line1}</p>}
          </div>
          <div className="grid grid-cols-1 gap-3 sm:grid-cols-3">
            <div>
              <input
                className="field"
                placeholder="City"
                maxLength={MAXLEN.city}
                value={v.addr_city}
                onChange={(e) => setV({ ...v, addr_city: e.target.value })}
              />
              {errs.addr_city && <p className="err">{errs.addr_city}</p>}
            </div>
            <StateSelect
              value={v.addr_state_code}
              onChange={(e) => setV({ ...v, addr_state_code: e.target.value })}
            />
            <div>
              <input
                className="field"
                placeholder="PIN"
                inputMode="numeric"
                maxLength={MAXLEN.pincode}
                value={v.addr_pincode}
                onChange={(e) => setV({ ...v, addr_pincode: e.target.value })}
              />
              {errs.addr_pincode && <p className="err">{errs.addr_pincode}</p>}
            </div>
          </div>
        </div>
      </div>

      {serverError && <p className="err">{serverError}</p>}

      {dupWarn && (
        <div className="rounded-md border border-line bg-[#f1e7d6] p-3">
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
              None of these — create “{v.legal_name.trim()}” as new
            </button>
          )}
        </div>
      )}

      <p className="text-[11px] text-muted">Nothing is saved until you press Create.</p>
    </form>
  );
}
