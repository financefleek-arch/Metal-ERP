import { useEffect, useState } from "react";
import { useMutation, useQueryClient } from "@tanstack/react-query";
import { api, ApiError } from "../lib/api";
import type { Tenant } from "../lib/types";
import { validCodePrefix, type GroupCreatePolicy } from "../lib/catalog";

/** Firm-page card for the Supplier Catalog defaults. Only rendered when the
 *  tenant has ext_supplier_catalog. Saves just its own two fields. */
export function CatalogSettingsCard({ tenant }: { tenant: Tenant | undefined }) {
  const qc = useQueryClient();
  const [prefix, setPrefix] = useState("");
  const [policy, setPolicy] = useState<GroupCreatePolicy>("auto");
  const [saved, setSaved] = useState(false);
  const [err, setErr] = useState<string | null>(null);

  useEffect(() => {
    if (!tenant) return;
    setPrefix(tenant.catalog_code_prefix ?? "");
    setPolicy(tenant.catalog_group_create_policy);
  }, [tenant]);

  const prefixBad = prefix.trim() !== "" && !validCodePrefix(prefix);
  const dirty =
    !!tenant &&
    (prefix.trim().toUpperCase() !== (tenant.catalog_code_prefix ?? "") ||
      policy !== tenant.catalog_group_create_policy);

  const save = useMutation({
    mutationFn: () =>
      api<Tenant>("/tenant", {
        method: "PATCH",
        body: {
          catalog_code_prefix: prefix.trim() === "" ? null : prefix.trim(),
          catalog_group_create_policy: policy,
        },
      }),
    onSuccess: (t) => {
      qc.setQueryData(["tenant"], t);
      setErr(null);
      setSaved(true);
      setTimeout(() => setSaved(false), 1600);
    },
    onError: (e) => setErr(e instanceof ApiError ? e.message : "Save failed"),
  });

  return (
    <section className="card mb-5 p-4 sm:p-6">
      <div className="flex items-baseline justify-between">
        <h2 className="font-serif text-lg font-semibold">Supplier catalogs</h2>
        {saved && <span className="text-xs text-ok">Saved ✓</span>}
      </div>
      <div className="mt-3 grid grid-cols-1 gap-x-5 gap-y-4 sm:grid-cols-2">
        <div>
          <label className="label" htmlFor="catalog-prefix">
            Item code prefix
          </label>
          <input
            id="catalog-prefix"
            className="field uppercase"
            placeholder="e.g. GL"
            maxLength={8}
            value={prefix}
            onChange={(e) => setPrefix(e.target.value)}
          />
          <p className="mt-1 text-xs text-muted">
            Codes look like {(prefix.trim() || "GL").toUpperCase()}-000001. Leave blank to
            derive one from each catalog's name.
          </p>
          {prefixBad && <p className="err">Use 2 to 8 letters or digits.</p>}
        </div>
        <div>
          <label className="label" htmlFor="catalog-group-policy">
            New groups
          </label>
          <select
            id="catalog-group-policy"
            className="field"
            value={policy}
            onChange={(e) => setPolicy(e.target.value as GroupCreatePolicy)}
          >
            <option value="auto">Create automatically</option>
            <option value="suggest_only">Suggest only, I approve each</option>
          </select>
          <p className="mt-1 text-xs text-muted">
            A group is the product type, such as Beer Mugs. You can rename or merge groups
            later.
          </p>
        </div>
      </div>
      {err && <p className="err mt-3">{err}</p>}
      <div className="mt-4 flex justify-end">
        <button
          type="button"
          className="btn-primary"
          disabled={!dirty || prefixBad || save.isPending}
          onClick={() => {
            setErr(null);
            save.mutate();
          }}
        >
          {save.isPending ? "Saving…" : "Save catalog settings"}
        </button>
      </div>
    </section>
  );
}
