import { useEffect, useMemo, useState } from "react";
import { useQueryClient } from "@tanstack/react-query";
import { api, ApiError, getToken } from "../../lib/api";
import { useDebounced } from "../../lib/useDebounced";
import {
  COLUMN_CHOICES,
  type BulkTarget,
  type CustomerCatalog,
  type CustomerCatalogOptions,
  type CustomerCatalogRequest,
  type ItemFilter,
  type OutputJob,
} from "../../lib/catalog";

type Scope = "selected" | "filtered" | "all";

const authHeaders = (): Record<string, string> => {
  const t = getToken();
  return t ? { Authorization: `Bearer ${t}` } : {};
};

async function failure(res: Response): Promise<ApiError> {
  const data = (await res.json().catch(() => undefined)) as { detail?: unknown } | undefined;
  const d = data?.detail;
  const message =
    typeof d === "string"
      ? d
      : Array.isArray(d)
        ? d.map((x: { msg?: string }) => x?.msg).filter(Boolean).join("; ")
        : res.statusText;
  return new ApiError(res.status, message || "Could not make the catalog.", d);
}

const postJson = (path: string, body: unknown) =>
  fetch(`/api${path}`, {
    method: "POST",
    headers: { "Content-Type": "application/json", ...authHeaders() },
    body: JSON.stringify(body),
  });

const SYNC_LIMIT = 100; // mirrors the server: larger catalogs are built in the background

const DEFAULTS: CustomerCatalogOptions = {
  columns: 3,
  group_by: "group",
  show_code: true,
  price_basis: "pack",
  contents: true,
  title: "",
};

/** Make a customer-facing catalog PDF: your prices, your layout, never the supplier's cost. */
export function CustomerCatalogDialog({
  catalogId,
  defaultTitle,
  selection,
  filtered,
  includedTotal,
  onClose,
}: {
  catalogId: string;
  defaultTitle: string;
  selection: { count: number; target: BulkTarget } | null;
  filtered: { count: number; filter: ItemFilter } | null;
  includedTotal: number;
  onClose: () => void;
}) {
  const qc = useQueryClient();
  const [scope, setScope] = useState<Scope>(selection ? "selected" : "all");
  const [opts, setOpts] = useState<CustomerCatalogOptions>(DEFAULTS);
  const [phase, setPhase] = useState<"idle" | "working" | "done">("idle");
  const [job, setJob] = useState<OutputJob | null>(null);
  const [made, setMade] = useState<CustomerCatalog | null>(null);
  const [fileUrl, setFileUrl] = useState<string | null>(null);
  const [err, setErr] = useState<string | null>(null);
  const [previewUrl, setPreviewUrl] = useState<string | null>(null);
  const [previewErr, setPreviewErr] = useState<string | null>(null);

  const itemCount =
    scope === "selected" ? (selection?.count ?? 0) : scope === "filtered" ? (filtered?.count ?? 0) : includedTotal;
  const grouped = opts.group_by === "group";

  const request: CustomerCatalogRequest = useMemo(() => {
    const { title, ...rest } = opts;
    const base: CustomerCatalogRequest = {
      ...rest,
      contents: grouped && opts.contents,
      ...(title.trim() ? { title: title.trim() } : {}),
    };
    if (scope === "selected" && selection) {
      return "ids" in selection.target
        ? { ...base, ids: selection.target.ids }
        : { ...base, filter: selection.target.filter };
    }
    if (scope === "filtered" && filtered) return { ...base, filter: filtered.filter };
    return { ...base, all_included: true };
  }, [opts, scope, selection, filtered, grouped]);

  // --- preview of the first product page (rendered by the server, so it is exact) ---
  const previewKey = useDebounced(JSON.stringify(request), 450);
  useEffect(() => {
    let cancelled = false;
    let url: string | null = null;
    (async () => {
      try {
        const res = await postJson(
          `/supplier-catalogs/${catalogId}/customer-catalogs/preview`,
          JSON.parse(previewKey),
        );
        if (!res.ok) throw await failure(res);
        const blob = await res.blob();
        if (cancelled) return;
        url = URL.createObjectURL(blob);
        setPreviewUrl(url);
        setPreviewErr(null);
      } catch (e) {
        if (!cancelled) {
          setPreviewUrl(null);
          setPreviewErr(e instanceof ApiError ? e.message : "No preview.");
        }
      }
    })();
    return () => {
      cancelled = true;
      if (url) URL.revokeObjectURL(url);
    };
  }, [previewKey, catalogId]);

  async function finish(version: CustomerCatalog) {
    const res = await fetch(
      `/api/supplier-catalogs/${catalogId}/customer-catalogs/${version.id}/file`,
      { headers: authHeaders() },
    );
    if (!res.ok) throw await failure(res);
    setFileUrl(URL.createObjectURL(await res.blob()));
    setMade(version);
    setPhase("done");
    qc.invalidateQueries({ queryKey: ["customer-catalogs", catalogId] });
  }

  // --- poll a background build, then fetch the new version ---
  const jobId = job?.id;
  const jobStatus = job?.status;
  useEffect(() => {
    if (!jobId || jobStatus === "done" || jobStatus === "error") return;
    const h = window.setInterval(async () => {
      try {
        const j = await api<OutputJob>(`/supplier-catalogs/${catalogId}/outputs/${jobId}`);
        setJob(j);
        if (j.status === "done" && j.result_id) {
          const list = await api<CustomerCatalog[]>(
            `/supplier-catalogs/${catalogId}/customer-catalogs`,
          );
          const version = list.find((v) => v.id === j.result_id);
          if (version) await finish(version);
        } else if (j.status === "error") {
          setErr(j.error ?? "The catalog could not be built.");
          setPhase("idle");
        }
      } catch (e) {
        setErr(e instanceof ApiError ? e.message : "Lost contact with the server. Try again.");
        setPhase("idle");
      }
    }, 1500);
    return () => window.clearInterval(h);
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [jobId, jobStatus, catalogId]);

  useEffect(() => {
    return () => {
      if (fileUrl) URL.revokeObjectURL(fileUrl);
    };
  }, [fileUrl]);

  useEffect(() => {
    const onKey = (e: KeyboardEvent) => {
      if (e.key === "Escape") onClose();
    };
    window.addEventListener("keydown", onKey);
    return () => window.removeEventListener("keydown", onKey);
  }, [onClose]);

  function patch(p: Partial<CustomerCatalogOptions>) {
    setOpts((o) => ({ ...o, ...p }));
    setPhase("idle");
    setFileUrl(null);
    setMade(null);
    setJob(null);
    setErr(null);
  }

  async function create() {
    setErr(null);
    setPhase("working");
    setFileUrl(null);
    setMade(null);
    setJob(null);
    try {
      const res = await postJson(`/supplier-catalogs/${catalogId}/customer-catalogs`, request);
      if (res.status === 202) setJob((await res.json()) as OutputJob);
      else if (!res.ok) throw await failure(res);
      else await finish((await res.json()) as CustomerCatalog);
    } catch (e) {
      setErr(e instanceof ApiError ? e.message : "Could not make the catalog. Try again.");
      setPhase("idle");
    }
  }

  const canCreate = itemCount > 0 && phase !== "working";

  return (
    <div
      className="fixed inset-0 z-50 grid place-items-center overflow-y-auto bg-black/40 p-4"
      onMouseDown={(e) => {
        if (e.target === e.currentTarget) onClose();
      }}
    >
      <div
        role="dialog"
        aria-modal="true"
        aria-labelledby="cc-title"
        className="card w-full max-w-4xl bg-card p-5 shadow-xl"
      >
        <div className="flex items-start justify-between gap-3">
          <div>
            <h2 id="cc-title" className="font-serif text-xl font-semibold">
              Create customer catalog
            </h2>
            <p className="mt-1 text-sm text-muted">
              A PDF with your prices and item codes. The supplier&apos;s prices are never shown.
            </p>
          </div>
          <button type="button" className="btn-ghost h-9 px-3" onClick={onClose}>
            Close
          </button>
        </div>

        <div className="mt-4 grid gap-5 md:grid-cols-[1fr_17rem]">
          <div className="space-y-4">
            <fieldset>
              <legend className="label">Items</legend>
              <div className="space-y-1.5 text-sm">
                {selection && (
                  <label className="flex items-center gap-2">
                    <input
                      type="radio"
                      name="cc-scope"
                      checked={scope === "selected"}
                      onChange={() => {
                        setScope("selected");
                        setPhase("idle");
                      }}
                    />
                    Selected items ({selection.count})
                  </label>
                )}
                {filtered && (
                  <label className="flex items-center gap-2">
                    <input
                      type="radio"
                      name="cc-scope"
                      checked={scope === "filtered"}
                      onChange={() => {
                        setScope("filtered");
                        setPhase("idle");
                      }}
                    />
                    Items matching the current filter ({filtered.count})
                  </label>
                )}
                <label className="flex items-center gap-2">
                  <input
                    type="radio"
                    name="cc-scope"
                    checked={scope === "all"}
                    onChange={() => {
                      setScope("all");
                      setPhase("idle");
                    }}
                  />
                  All included items ({includedTotal})
                </label>
              </div>
            </fieldset>

            <div>
              <label className="label" htmlFor="cc-title-input">
                Title on the cover
              </label>
              <input
                id="cc-title-input"
                className="field"
                placeholder={defaultTitle}
                maxLength={200}
                value={opts.title}
                onChange={(e) => patch({ title: e.target.value })}
              />
            </div>

            <fieldset>
              <legend className="label">Layout</legend>
              <div className="grid gap-2 sm:grid-cols-3">
                {COLUMN_CHOICES.map((c) => (
                  <label
                    key={c.value}
                    className={`cursor-pointer rounded-md border p-2.5 text-sm ${
                      opts.columns === c.value ? "border-accent bg-accent-soft" : "border-line"
                    }`}
                  >
                    <input
                      type="radio"
                      name="cc-cols"
                      className="sr-only"
                      checked={opts.columns === c.value}
                      onChange={() => patch({ columns: c.value })}
                    />
                    <span className="block font-medium">{c.name}</span>
                    <span className="block text-xs text-muted">{c.detail}</span>
                  </label>
                ))}
              </div>
            </fieldset>

            <fieldset>
              <legend className="label">Arrange</legend>
              <div className="flex flex-wrap gap-x-6 gap-y-1.5 text-sm">
                <label className="flex items-center gap-1.5">
                  <input
                    type="radio"
                    name="cc-group"
                    checked={grouped}
                    onChange={() => patch({ group_by: "group" })}
                  />
                  By group (product type)
                </label>
                <label className="flex items-center gap-1.5">
                  <input
                    type="radio"
                    name="cc-group"
                    checked={!grouped}
                    onChange={() => patch({ group_by: "none" })}
                  />
                  One list, no groups
                </label>
                <label className={`flex items-center gap-1.5 ${grouped ? "" : "opacity-50"}`}>
                  <input
                    type="checkbox"
                    disabled={!grouped}
                    checked={grouped && opts.contents}
                    onChange={(e) => patch({ contents: e.target.checked })}
                  />
                  Contents page
                </label>
              </div>
            </fieldset>

            <fieldset>
              <legend className="label">Show</legend>
              <div className="flex flex-wrap gap-x-6 gap-y-1.5 text-sm">
                <label className="flex items-center gap-1.5">
                  <input
                    type="checkbox"
                    checked={opts.show_code}
                    onChange={(e) => patch({ show_code: e.target.checked })}
                  />
                  Item code
                </label>
                <label className="flex items-center gap-1.5">
                  <input
                    type="radio"
                    name="cc-basis"
                    checked={opts.price_basis === "pack"}
                    onChange={() => patch({ price_basis: "pack" })}
                  />
                  Price per pack
                </label>
                <label className="flex items-center gap-1.5">
                  <input
                    type="radio"
                    name="cc-basis"
                    checked={opts.price_basis === "piece"}
                    onChange={() => patch({ price_basis: "piece" })}
                  />
                  Price per piece
                </label>
              </div>
            </fieldset>
          </div>

          <div>
            <p className="label">Preview (first page of items)</p>
            <div className="grid min-h-[12rem] place-items-center rounded-md border border-line bg-ground p-2">
              {previewUrl ? (
                <img
                  src={previewUrl}
                  alt="Preview of the first page of items"
                  className="max-h-[26rem] w-full object-contain shadow-sm"
                />
              ) : (
                <p className="p-4 text-center text-xs text-muted">{previewErr ?? "Loading preview…"}</p>
              )}
            </div>
          </div>
        </div>

        <div className="mt-5 border-t border-line pt-4">
          <p className="text-sm" aria-live="polite">
            <span className="font-medium tabular-nums">{itemCount.toLocaleString("en-IN")}</span>{" "}
            item{itemCount === 1 ? "" : "s"}
            {itemCount > SYNC_LIMIT && (
              <span className="text-muted"> · a larger catalog is built in the background</span>
            )}
          </p>

          {err && (
            <p className="err" role="alert">
              {err}
            </p>
          )}
          {phase === "working" && (
            <p className="mt-2 text-sm text-muted" role="status">
              {job
                ? `Building the catalog… ${job.progress.toLocaleString("en-IN")} of ${job.total.toLocaleString("en-IN")} items`
                : "Making your catalog…"}
            </p>
          )}

          {phase === "done" && made && fileUrl && (
            <div className="mt-2 rounded-md border border-line bg-ground p-3 text-sm" role="status">
              <p className="font-medium text-ok">
                Version {made.version} is ready: {made.item_count} items on {made.page_count} pages.
              </p>
              <div className="mt-2 flex flex-wrap gap-2">
                <a className="btn-primary" href={fileUrl} target="_blank" rel="noopener noreferrer">
                  Open PDF
                </a>
                <a className="btn-ghost" href={fileUrl} download={`catalog-v${made.version}.pdf`}>
                  Download
                </a>
              </div>
            </div>
          )}

          <div className="mt-3 flex justify-end gap-2">
            <button type="button" className="btn-ghost" onClick={onClose}>
              Close
            </button>
            <button type="button" className="btn-primary" disabled={!canCreate} onClick={create}>
              {phase === "working" ? "Working…" : phase === "done" ? "Make a new version" : "Create catalog"}
            </button>
          </div>
        </div>
      </div>
    </div>
  );
}
