import { useState } from "react";
import { useMutation, useQueryClient } from "@tanstack/react-query";
import { api, ApiError } from "../lib/api";
import {
  MAXLEN,
  addressLineError,
  cityError,
  emailError,
  gstinError,
  legalNameError,
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

  const create = useMutation({
    mutationFn: () => {
      const hasAddr = v.addr_line1 || v.addr_city || v.addr_state_code || v.addr_pincode;
      return api<Party>("/parties", {
        method: "POST",
        body: {
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
        },
      });
    },
    onSuccess: (p) => {
      qc.invalidateQueries({ queryKey: ["parties"] });
      onCreated(p);
    },
    onError: (e) => setServerError(e instanceof ApiError ? e.message : "Create failed"),
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

  return (
    <form
      className="flex flex-col gap-4"
      onSubmit={(e) => {
        e.preventDefault();
        setServerError(null);
        if (canCreate) create.mutate();
      }}
    >
      <div className="flex items-center justify-between">
        <h2 className="font-serif text-lg font-semibold text-accent">New party</h2>
        <div className="flex gap-2">
          <button type="button" className="btn-ghost" onClick={onCancel}>
            Cancel
          </button>
          <button type="submit" className="btn-primary" disabled={!canCreate}>
            {create.isPending ? "Creating…" : "Create"}
          </button>
        </div>
      </div>

      <div className="grid grid-cols-2 gap-x-4 gap-y-3">
        <div className="col-span-2">
          <label className="label">Legal name *</label>
          <input
            className="field"
            autoFocus
            placeholder="Type the party name…"
            maxLength={MAXLEN.legalName}
            value={v.legal_name}
            onChange={(e) => setV({ ...v, legal_name: e.target.value })}
          />
          {v.legal_name.trim() && errs.legal_name && <p className="err">{errs.legal_name}</p>}
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
          <div className="grid grid-cols-3 gap-3">
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
      <p className="text-[11px] text-muted">Nothing is saved until you press Create.</p>
    </form>
  );
}
