import { useEffect, useState } from "react";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { api } from "../../lib/api";
import type { LedgerConfig } from "../../lib/inward";

const FIELDS: { key: keyof LedgerConfig; label: string }[] = [
  { key: "creditors_group", label: "Creditors group" },
  { key: "purchase_ledger", label: "Purchase ledger" },
  { key: "cgst_ledger", label: "CGST ledger" },
  { key: "sgst_ledger", label: "SGST ledger" },
  { key: "igst_ledger", label: "IGST ledger" },
  { key: "round_off_ledger", label: "Round-off ledger" },
];

export function InwardSettingsPage() {
  const qc = useQueryClient();
  const cfg = useQuery({
    queryKey: ["inward-ledgers"],
    queryFn: () => api<LedgerConfig>("/inward-bills/settings/ledgers"),
  });
  const [form, setForm] = useState<LedgerConfig | null>(null);
  useEffect(() => {
    if (cfg.data) setForm(cfg.data);
  }, [cfg.data]);

  const save = useMutation({
    mutationFn: (body: LedgerConfig) =>
      api<LedgerConfig>("/inward-bills/settings/ledgers", { method: "PUT", body }),
    onSuccess: (d) => {
      setForm(d);
      void qc.invalidateQueries({ queryKey: ["inward-ledgers"] });
    },
  });

  if (!form) return <div className="p-6 text-sm text-muted">Loading…</div>;

  return (
    <div className="mx-auto max-w-xl">
      <h1 className="mb-1 font-serif text-xl font-semibold">Inward · Ledger settings</h1>
      <p className="mb-4 text-sm text-muted">
        These names must match the shop's Tally chart of accounts, or the import throws a{" "}
        <code className="rounded bg-line px-1">&lt;LINEERROR&gt;</code>.
      </p>
      <div className="card grid grid-cols-2 gap-4 p-4">
        {FIELDS.map((f) => (
          <div key={f.key}>
            <label className="label">{f.label}</label>
            <input
              className="field"
              value={form[f.key]}
              onChange={(e) => setForm({ ...form, [f.key]: e.target.value })}
            />
          </div>
        ))}
        <div className="col-span-2">
          <label className="label">XML encoding</label>
          <div className="inline-flex overflow-hidden rounded-full border border-line text-xs">
            {(["UTF-16", "UTF-8"] as const).map((enc) => (
              <button
                key={enc}
                className={`px-3 py-1.5 ${
                  form.xml_encoding === enc ? "bg-ink text-ground" : "text-muted"
                }`}
                onClick={() => setForm({ ...form, xml_encoding: enc })}
              >
                {enc}
              </button>
            ))}
          </div>
          <span className="ml-3 text-[11px] text-muted">
            confirm against the shop's Tally version on the first real import
          </span>
        </div>
        <div className="col-span-2">
          <button
            className="btn-primary"
            onClick={() => save.mutate(form)}
            disabled={save.isPending}
          >
            {save.isPending ? "Saving…" : "Save settings"}
          </button>
          {save.isSuccess && (
            <span className="ml-3 text-xs text-ok">Saved.</span>
          )}
        </div>
      </div>
    </div>
  );
}
