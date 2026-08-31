import { useEffect, useState } from "react";
import { useForm } from "react-hook-form";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { api, ApiError } from "../lib/api";
import type { Tenant } from "../lib/types";
import { StateSelect } from "../components/StateSelect";
import { panError } from "../lib/reference";

type FormShape = Omit<Tenant, "id" | "gst_enabled" | "gstin" | "trade_name" | "email">;

type FieldKind = "text" | "textarea" | "state" | "pan";
const FIELDS: { name: keyof FormShape; label: string; full?: boolean; kind?: FieldKind }[] = [
  { name: "legal_name", label: "Legal / trade name", full: true },
  { name: "address", label: "Address", full: true, kind: "textarea" },
  { name: "city", label: "City" },
  { name: "pincode", label: "PIN" },
  { name: "state_code", label: "State", kind: "state" },
  { name: "phone", label: "Phone" },
  { name: "pan", label: "PAN", kind: "pan" },
  { name: "document_label", label: "Document label" },
  { name: "bank_holder", label: "Bank A/c holder" },
  { name: "bank_name", label: "Bank name" },
  { name: "bank_ac_no", label: "Bank A/c no." },
  { name: "bank_ifsc", label: "IFSC" },
  { name: "bank_branch", label: "Branch" },
  { name: "upi_id", label: "UPI ID" },
  { name: "declaration_text", label: "Declaration text", full: true, kind: "textarea" },
  { name: "jurisdiction_text", label: "Jurisdiction line", full: true },
];

export function FirmPage() {
  const qc = useQueryClient();
  const { data, isLoading } = useQuery({
    queryKey: ["tenant"],
    queryFn: () => api<Tenant>("/tenant"),
  });

  const {
    register,
    handleSubmit,
    reset,
    formState: { errors },
  } = useForm<FormShape>();
  const [saved, setSaved] = useState(false);
  const [serverError, setServerError] = useState<string | null>(null);

  useEffect(() => {
    if (data) {
      const seed: Partial<FormShape> = {};
      for (const f of FIELDS) seed[f.name] = (data[f.name] ?? "") as never;
      reset(seed as FormShape);
    }
  }, [data, reset]);

  const mut = useMutation({
    mutationFn: (body: Partial<FormShape>) =>
      api<Tenant>("/tenant", { method: "PATCH", body }),
    onSuccess: (t) => {
      qc.setQueryData(["tenant"], t);
      setSaved(true);
      setServerError(null);
      setTimeout(() => setSaved(false), 1600);
    },
    onError: (e) => setServerError(e instanceof ApiError ? e.message : "Save failed"),
  });

  const onSubmit = handleSubmit((v) => {
    // blank strings -> null so we don't store "" everywhere
    const body: Record<string, unknown> = {};
    for (const [k, val] of Object.entries(v)) body[k] = val === "" ? null : val;
    mut.mutate(body);
  });

  if (isLoading) return <p className="text-sm text-muted">Loading…</p>;

  return (
    <div className="max-w-3xl">
      <div className="mb-5 flex items-baseline justify-between">
        <h1 className="font-serif text-2xl font-semibold">Firm profile</h1>
        <div className="flex items-center gap-3">
          {saved && <span className="text-xs text-ok">Saved ✓</span>}
          {data && (
            <span className="text-xs text-muted">
              GST {data.gst_enabled ? "on" : "off"}
            </span>
          )}
        </div>
      </div>

      <form onSubmit={onSubmit} className="card p-6">
        <div className="grid grid-cols-2 gap-x-5 gap-y-4">
          {FIELDS.map((f) => (
            <div key={f.name} className={f.full ? "col-span-2" : ""}>
              <label className="label">{f.label}</label>
              {f.kind === "textarea" ? (
                <textarea
                  rows={2}
                  className="field h-auto resize-y py-2"
                  {...register(f.name)}
                />
              ) : f.kind === "state" ? (
                <StateSelect {...register(f.name)} />
              ) : f.kind === "pan" ? (
                <>
                  <input
                    className="field uppercase"
                    placeholder="AAAAA9999A"
                    {...register(f.name, {
                      setValueAs: (v: string) => (v ?? "").trim().toUpperCase(),
                      validate: (v) => panError(String(v ?? "")) ?? true,
                    })}
                  />
                  {errors[f.name] && <p className="err">{errors[f.name]?.message}</p>}
                </>
              ) : (
                <input className="field" {...register(f.name)} />
              )}
            </div>
          ))}
        </div>

        {serverError && <p className="err mt-3">{serverError}</p>}

        <div className="mt-5 flex justify-end">
          <button type="submit" className="btn-primary" disabled={mut.isPending}>
            {mut.isPending ? "Saving…" : "Save firm profile"}
          </button>
        </div>
      </form>
    </div>
  );
}
