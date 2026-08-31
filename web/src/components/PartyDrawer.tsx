import { useForm } from "react-hook-form";
import { useMutation } from "@tanstack/react-query";
import { api, ApiError } from "../lib/api";
import type { Party, PartyRole } from "../lib/types";
import { useState } from "react";

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

function toFields(p: Party | null): Fields {
  const a = p?.addresses?.[0];
  return {
    legal_name: p?.legal_name ?? "",
    role: p?.role ?? "customer",
    phone: p?.phone ?? "",
    email: p?.email ?? "",
    pan: p?.pan ?? "",
    gstin: p?.gstin ?? "",
    default_state_code: p?.default_state_code ?? "",
    addr_line1: a?.line1 ?? "",
    addr_city: a?.city ?? "",
    addr_state_code: a?.state_code ?? "",
    addr_pincode: a?.pincode ?? "",
  };
}

export function PartyDrawer({
  party,
  onClose,
  onSaved,
}: {
  party: Party | null;
  onClose: () => void;
  onSaved: () => void;
}) {
  const isNew = party === null;
  const {
    register,
    handleSubmit,
    formState: { errors },
  } = useForm<Fields>({ defaultValues: toFields(party) });
  const [serverError, setServerError] = useState<string | null>(null);

  const mut = useMutation({
    mutationFn: (v: Fields) => {
      const hasAddr = v.addr_line1 || v.addr_city || v.addr_state_code || v.addr_pincode;
      const body = {
        legal_name: v.legal_name.trim(),
        role: v.role,
        phone: v.phone || null,
        email: v.email || null,
        pan: v.pan || null,
        gstin: v.gstin || null,
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
      return isNew
        ? api<Party>("/parties", { method: "POST", body })
        : api<Party>(`/parties/${party!.id}`, { method: "PATCH", body });
    },
    onSuccess: onSaved,
    onError: (e) => setServerError(e instanceof ApiError ? e.message : "Save failed"),
  });

  const onSubmit = handleSubmit((v) => {
    setServerError(null);
    mut.mutate(v);
  });

  return (
    <div className="fixed inset-0 z-40 flex justify-end bg-ink/30" onClick={onClose}>
      <div
        className="h-full w-full max-w-md overflow-y-auto bg-card p-6 shadow-2xl"
        onClick={(e) => e.stopPropagation()}
      >
        <div className="mb-4 flex items-center justify-between">
          <h2 className="font-serif text-lg font-semibold">
            {isNew ? "New party" : party!.legal_name}
          </h2>
          <button onClick={onClose} className="text-muted hover:text-ink">
            ✕
          </button>
        </div>

        <form onSubmit={onSubmit} className="space-y-4">
          <div>
            <label className="label">Legal name *</label>
            <input
              className="field"
              autoFocus
              {...register("legal_name", { required: "Required" })}
            />
            {errors.legal_name && <p className="err">{errors.legal_name.message}</p>}
          </div>

          <div className="grid grid-cols-2 gap-3">
            <div>
              <label className="label">Role</label>
              <select className="field" {...register("role")}>
                <option value="customer">Customer</option>
                <option value="supplier">Supplier</option>
                <option value="both">Both</option>
              </select>
            </div>
            <div>
              <label className="label">Phone</label>
              <input className="field" {...register("phone")} />
            </div>
            <div>
              <label className="label">Email</label>
              <input className="field" type="email" {...register("email")} />
            </div>
            <div>
              <label className="label">PAN</label>
              <input className="field uppercase" {...register("pan")} />
            </div>
            <div>
              <label className="label">GSTIN</label>
              <input className="field uppercase" {...register("gstin")} />
            </div>
            <div>
              <label className="label">Default state code</label>
              <input className="field" {...register("default_state_code")} />
            </div>
          </div>

          <div className="border-t border-line pt-4">
            <p className="label">Address</p>
            <div className="space-y-3">
              <input
                className="field"
                placeholder="Address line"
                {...register("addr_line1")}
              />
              <div className="grid grid-cols-3 gap-3">
                <input className="field" placeholder="City" {...register("addr_city")} />
                <input
                  className="field"
                  placeholder="State code"
                  {...register("addr_state_code")}
                />
                <input className="field" placeholder="PIN" {...register("addr_pincode")} />
              </div>
            </div>
          </div>

          {serverError && <p className="err">{serverError}</p>}

          <div className="flex justify-end gap-2 pt-2">
            <button type="button" className="btn-ghost" onClick={onClose}>
              Cancel
            </button>
            <button type="submit" className="btn-primary" disabled={mut.isPending}>
              {mut.isPending ? "Saving…" : isNew ? "Create party" : "Save changes"}
            </button>
          </div>
        </form>
      </div>
    </div>
  );
}
