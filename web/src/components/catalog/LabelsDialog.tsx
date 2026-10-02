import { useEffect, useMemo, useRef, useState } from "react";
import { api, ApiError, getToken } from "../../lib/api";
import { useDebounced } from "../../lib/useDebounced";
import {
  LABEL_PRESETS,
  labelPages,
  type BulkTarget,
  type ItemFilter,
  type LabelOptions,
  type LabelRequest,
  type OutputJob,
} from "../../lib/catalog";

type Scope = "selected" | "filtered" | "all";

type Result =
  | { kind: "pdf"; blob: Blob; scanWarning: boolean }
  | { kind: "job"; job: OutputJob };

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
  return new ApiError(res.status, message || "Could not make the labels.", d);
}

async function postJson(path: string, body: unknown): Promise<Response> {
  return fetch(`/api${path}`, {
    method: "POST",
    headers: { "Content-Type": "application/json", ...authHeaders() },
    body: JSON.stringify(body),
  });
}

async function requestLabels(catalogId: string, body: LabelRequest): Promise<Result> {
  const res = await postJson(`/supplier-catalogs/${catalogId}/labels`, body);
  if (res.status === 202) return { kind: "job", job: (await res.json()) as OutputJob };
  if (!res.ok) throw await failure(res);
  return {
    kind: "pdf",
    blob: await res.blob(),
    scanWarning: res.headers.get("X-Scan-Warning") === "1",
  };
}

async function fetchJobFile(catalogId: string, jobId: string): Promise<Blob> {
  const res = await fetch(`/api/supplier-catalogs/${catalogId}/outputs/${jobId}/file`, {
    headers: authHeaders(),
  });
  if (!res.ok) throw await failure(res);
  return res.blob();
}

const DEFAULTS: LabelOptions = {
  preset: "roll_50x25",
  copies: 1,
  show_name: true,
  show_code: true,
  show_price: false,
  start_at: 1,
};

/** Print barcode labels for the selected items, the current filter, or every included item. */
export function LabelsDialog({
  catalogId,
  fileSlug,
  selection,
  filtered,
  includedTotal,
  onClose,
}: {
  catalogId: string;
  fileSlug: string;
  selection: { count: number; target: BulkTarget } | null;
  filtered: { count: number; filter: ItemFilter } | null;
  includedTotal: number;
  onClose: () => void;
}) {
  const [scope, setScope] = useState<Scope>(selection ? "selected" : "all");
  const [opts, setOpts] = useState<LabelOptions>(DEFAULTS);
  const [phase, setPhase] = useState<"idle" | "working" | "done">("idle");
  const [job, setJob] = useState<OutputJob | null>(null);
  const [fileUrl, setFileUrl] = useState<string | null>(null);
  const [scanWarning, setScanWarning] = useState(false);
  const [err, setErr] = useState<string | null>(null);
  const [previewUrl, setPreviewUrl] = useState<string | null>(null);
  const [previewErr, setPreviewErr] = useState<string | null>(null);
  const firstField = useRef<HTMLInputElement>(null);

  const sheet = opts.preset === "sheet_a4_3x8";
  const itemCount =
    scope === "selected" ? (selection?.count ?? 0) : scope === "filtered" ? (filtered?.count ?? 0) : includedTotal;
  const labels = itemCount * opts.copies;
  const pages = labelPages(labels, opts.preset, opts.start_at);

  const request: LabelRequest = useMemo(() => {
    const base: LabelRequest = { ...opts, start_at: sheet ? opts.start_at : 1 };
    if (scope === "selected" && selection) {
      return "ids" in selection.target
        ? { ...base, ids: selection.target.ids }
        : { ...base, filter: selection.target.filter };
    }
    if (scope === "filtered" && filtered) return { ...base, filter: filtered.filter };
    return { ...base, all_included: true };
  }, [opts, scope, selection, filtered, sheet]);

  // --- live preview of one label (server-rendered, so it is exactly what prints) ---
  const previewKey = useDebounced(JSON.stringify(request), 350);
  useEffect(() => {
    let cancelled = false;
    let url: string | null = null;
    (async () => {
      try {
        const res = await postJson(
          `/supplier-catalogs/${catalogId}/labels/preview`,
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

  // --- poll a background job until it finishes, then fetch the file ---
  const jobId = job?.id;
  const jobStatus = job?.status;
  useEffect(() => {
    if (!jobId || jobStatus === "done" || jobStatus === "error") return;
    const h = window.setInterval(async () => {
      try {
        const j = await api<OutputJob>(`/supplier-catalogs/${catalogId}/outputs/${jobId}`);
        setJob(j);
        if (j.status === "done") {
          const blob = await fetchJobFile(catalogId, jobId);
          setFileUrl(URL.createObjectURL(blob));
          setScanWarning(j.scan_warning);
          setPhase("done");
        } else if (j.status === "error") {
          setErr(j.error ?? "The labels could not be built.");
          setPhase("idle");
        }
      } catch (e) {
        setErr(e instanceof ApiError ? e.message : "Lost contact with the server. Try again.");
        setPhase("idle");
      }
    }, 1000);
    return () => window.clearInterval(h);
  }, [jobId, jobStatus, catalogId]);

  // release the generated file when the dialog closes or is regenerated
  useEffect(() => {
    return () => {
      if (fileUrl) URL.revokeObjectURL(fileUrl);
    };
  }, [fileUrl]);

  useEffect(() => firstField.current?.focus(), []);
  useEffect(() => {
    const onKey = (e: KeyboardEvent) => {
      if (e.key === "Escape") onClose();
    };
    window.addEventListener("keydown", onKey);
    return () => window.removeEventListener("keydown", onKey);
  }, [onClose]);

  function patch(p: Partial<LabelOptions>) {
    setOpts((o) => ({ ...o, ...p }));
    setPhase("idle");
    setFileUrl(null);
    setJob(null);
    setErr(null);
  }

  async function create() {
    setErr(null);
    setPhase("working");
    setFileUrl(null);
    setJob(null);
    try {
      const r = await requestLabels(catalogId, request);
      if (r.kind === "pdf") {
        setFileUrl(URL.createObjectURL(r.blob));
        setScanWarning(r.scanWarning);
        setPhase("done");
      } else {
        setJob(r.job);
      }
    } catch (e) {
      setErr(e instanceof ApiError ? e.message : "Could not make the labels. Try again.");
      setPhase("idle");
    }
  }

  const canCreate = labels > 0 && opts.copies >= 1 && phase !== "working";

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
        aria-labelledby="labels-title"
        className="card w-full max-w-3xl bg-card p-5 shadow-xl"
      >
        <div className="flex items-start justify-between gap-3">
          <div>
            <h2 id="labels-title" className="font-serif text-xl font-semibold">
              Print barcode labels
            </h2>
            <p className="mt-1 text-sm text-muted">
              Each label carries a scannable barcode of the item code.
            </p>
          </div>
          <button type="button" className="btn-ghost h-9 px-3" onClick={onClose}>
            Close
          </button>
        </div>

        <div className="mt-4 grid gap-5 md:grid-cols-[1fr_16rem]">
          <div className="space-y-4">
            <fieldset>
              <legend className="label">Items</legend>
              <div className="space-y-1.5 text-sm">
                {selection && (
                  <label className="flex items-center gap-2">
                    <input
                      ref={firstField}
                      type="radio"
                      name="scope"
                      checked={scope === "selected"}
                      onChange={() => setScope("selected")}
                    />
                    Selected items ({selection.count})
                  </label>
                )}
                {filtered && (
                  <label className="flex items-center gap-2">
                    <input
                      type="radio"
                      name="scope"
                      checked={scope === "filtered"}
                      onChange={() => setScope("filtered")}
                    />
                    Items matching the current filter ({filtered.count})
                  </label>
                )}
                <label className="flex items-center gap-2">
                  <input
                    type="radio"
                    name="scope"
                    checked={scope === "all"}
                    onChange={() => setScope("all")}
                  />
                  All included items ({includedTotal})
                </label>
              </div>
            </fieldset>

            <fieldset>
              <legend className="label">Label size</legend>
              <div className="grid gap-2 sm:grid-cols-3">
                {LABEL_PRESETS.map((p) => (
                  <label
                    key={p.key}
                    className={`cursor-pointer rounded-md border p-2.5 text-sm ${
                      opts.preset === p.key ? "border-accent bg-accent-soft" : "border-line"
                    }`}
                  >
                    <input
                      type="radio"
                      name="preset"
                      className="sr-only"
                      checked={opts.preset === p.key}
                      onChange={() => patch({ preset: p.key })}
                    />
                    <span className="block font-medium">{p.name}</span>
                    <span className="block text-xs text-muted">{p.detail}</span>
                  </label>
                ))}
              </div>
            </fieldset>

            <div className="flex flex-wrap items-end gap-4">
              <div>
                <label className="label" htmlFor="label-copies">
                  Copies of each
                </label>
                <input
                  id="label-copies"
                  type="number"
                  min={1}
                  max={99}
                  className="field w-24"
                  value={opts.copies}
                  onChange={(e) =>
                    patch({ copies: Math.min(99, Math.max(1, Math.floor(Number(e.target.value)) || 1)) })
                  }
                />
              </div>
              {sheet && (
                <div>
                  <label className="label" htmlFor="label-start">
                    Start at label
                  </label>
                  <input
                    id="label-start"
                    type="number"
                    min={1}
                    max={24}
                    className="field w-24"
                    value={opts.start_at}
                    onChange={(e) =>
                      patch({
                        start_at: Math.min(24, Math.max(1, Math.floor(Number(e.target.value)) || 1)),
                      })
                    }
                  />
                </div>
              )}
            </div>
            {sheet && (
              <p className="-mt-2 text-xs text-muted">
                Use “Start at label” to reuse a sheet that is already partly printed (1 is the
                top-left).
              </p>
            )}

            <fieldset>
              <legend className="label">Print on each label</legend>
              <div className="flex flex-wrap gap-x-5 gap-y-1.5 text-sm">
                <span className="text-muted">Barcode (always)</span>
                <label className="flex items-center gap-1.5">
                  <input
                    type="checkbox"
                    checked={opts.show_name}
                    onChange={(e) => patch({ show_name: e.target.checked })}
                  />
                  Name
                </label>
                <label className="flex items-center gap-1.5">
                  <input
                    type="checkbox"
                    checked={opts.show_code}
                    onChange={(e) => patch({ show_code: e.target.checked })}
                  />
                  Code
                </label>
                <label className="flex items-center gap-1.5">
                  <input
                    type="checkbox"
                    checked={opts.show_price}
                    onChange={(e) => patch({ show_price: e.target.checked })}
                  />
                  New price
                </label>
              </div>
            </fieldset>
          </div>

          <div>
            <p className="label">Preview</p>
            <div className="grid min-h-[8rem] place-items-center rounded-md border border-line bg-ground p-3">
              {previewUrl ? (
                <img
                  src={previewUrl}
                  alt="Preview of the first label"
                  className="max-h-40 w-full object-contain shadow-sm"
                />
              ) : (
                <p className="text-center text-xs text-muted">{previewErr ?? "Loading preview…"}</p>
              )}
            </div>
            <p className="mt-1 text-xs text-muted">First label, at true proportions.</p>
          </div>
        </div>

        <div className="mt-5 border-t border-line pt-4">
          <p className="text-sm" aria-live="polite">
            <span className="font-medium tabular-nums">{labels.toLocaleString("en-IN")}</span>{" "}
            label{labels === 1 ? "" : "s"} on{" "}
            <span className="font-medium tabular-nums">{pages.toLocaleString("en-IN")}</span>{" "}
            page{pages === 1 ? "" : "s"}
            {labels > 200 && <span className="text-muted"> · large runs are built in the background</span>}
          </p>

          {err && (
            <p className="err" role="alert">
              {err}
            </p>
          )}

          {phase === "working" && (
            <p className="mt-2 text-sm text-muted" role="status">
              {job
                ? `Building labels… ${job.progress.toLocaleString("en-IN")} of ${job.total.toLocaleString("en-IN")}`
                : "Making your labels…"}
            </p>
          )}

          {phase === "done" && fileUrl && (
            <div className="mt-2 rounded-md border border-line bg-ground p-3 text-sm" role="status">
              <p className="font-medium text-ok">Your labels are ready.</p>
              <p className="mt-1 text-muted">
                When printing, choose <b>Actual size</b> (100%), not “Fit to page”, so the labels
                keep their exact size.
              </p>
              {scanWarning && (
                <p className="mt-1 text-danger">
                  Some barcodes are very thin at this label size and may not scan on a basic
                  scanner. Try a wider label or a shorter item code.
                </p>
              )}
              <div className="mt-2 flex flex-wrap gap-2">
                <a
                  className="btn-primary"
                  href={fileUrl}
                  target="_blank"
                  rel="noopener noreferrer"
                >
                  Open PDF to print
                </a>
                <a className="btn-ghost" href={fileUrl} download={`labels-${fileSlug}.pdf`}>
                  Download
                </a>
              </div>
            </div>
          )}

          <div className="mt-3 flex justify-end gap-2">
            <button type="button" className="btn-ghost" onClick={onClose}>
              Cancel
            </button>
            <button type="button" className="btn-primary" disabled={!canCreate} onClick={create}>
              {phase === "working" ? "Working…" : phase === "done" ? "Make again" : "Create labels"}
            </button>
          </div>
        </div>
      </div>
    </div>
  );
}
