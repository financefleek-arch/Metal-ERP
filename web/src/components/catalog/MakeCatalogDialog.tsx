import { useEffect, useMemo, useRef, useState } from "react";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { api, ApiError, apiUpload, getToken } from "../../lib/api";
import { useDebounced } from "../../lib/useDebounced";
import {
  COLUMN_CHOICES,
  type CatalogCheck,
  type CatalogLayout,
  type CatalogSelection,
  type CustomerCatalog,
  type OutputJob,
} from "../../lib/catalog";

const LAYOUT_KEY = "customer-catalog-layout";
const SYNC_LIMIT = 100; // mirrors the server: larger catalogs build in the background

const DEFAULTS: CatalogLayout = {
  columns: 3,
  group_by: "group",
  show_code: true,
  price_basis: "pack",
  contents: true,
  include_expected: false,
  label_expected: false,
};

function loadLayout(): CatalogLayout {
  try {
    const raw = window.localStorage.getItem(LAYOUT_KEY);
    return raw ? { ...DEFAULTS, ...(JSON.parse(raw) as Partial<CatalogLayout>) } : DEFAULTS;
  } catch {
    return DEFAULTS;
  }
}

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

/** One missing-photo row: add the photo on the spot. */
function MissingRow({
  item,
  onAdded,
}: {
  item: { item_id: string; name: string; code: string | null };
  onAdded: () => void;
}) {
  const ref = useRef<HTMLInputElement>(null);
  const [err, setErr] = useState<string | null>(null);
  const up = useMutation({
    mutationFn: (file: File) => {
      const fd = new FormData();
      fd.append("file", file);
      return apiUpload(`/items/${item.item_id}/photo`, fd);
    },
    onSuccess: () => {
      setErr(null);
      onAdded();
    },
    onError: (e) => setErr(e instanceof ApiError ? e.message : "Could not add the photo."),
  });
  return (
    <li className="flex items-center gap-2 py-1 text-xs">
      <span className="min-w-0 flex-1 truncate">
        {item.name}
        {item.code && <span className="ml-1.5 text-muted">{item.code}</span>}
      </span>
      {err && <span className="text-err">{err}</span>}
      <input
        ref={ref}
        type="file"
        accept="image/*"
        className="sr-only"
        onChange={(e) => {
          const f = e.target.files?.[0];
          if (f) up.mutate(f);
          e.target.value = "";
        }}
      />
      <button
        type="button"
        className="btn-ghost h-7 px-2.5 text-xs"
        disabled={up.isPending}
        onClick={() => ref.current?.click()}
      >
        {up.isPending ? "Adding…" : "Add photo"}
      </button>
    </li>
  );
}

/**
 * Make a customer catalog from the items chosen on the Items page (or the next version of an
 * existing one). Checks first: what goes in, what is left out and why, which photos are missing.
 */
export function MakeCatalogDialog({
  selection,
  count,
  defaultTitle = "",
  seriesId,
  onClose,
}: {
  selection: CatalogSelection;
  count: number;
  defaultTitle?: string;
  seriesId?: string;
  onClose: () => void;
}) {
  const qc = useQueryClient();
  const [layout, setLayout] = useState<CatalogLayout>(loadLayout);
  const [title, setTitle] = useState(defaultTitle);
  const [phase, setPhase] = useState<"idle" | "working" | "done">("idle");
  const [job, setJob] = useState<OutputJob | null>(null);
  const [made, setMade] = useState<CustomerCatalog | null>(null);
  const [fileUrl, setFileUrl] = useState<string | null>(null);
  const [err, setErr] = useState<string | null>(null);
  const [previewUrl, setPreviewUrl] = useState<string | null>(null);
  const [previewErr, setPreviewErr] = useState<string | null>(null);
  const [block, setBlock] = useState(false);

  const grouped = layout.group_by === "group";
  const request = useMemo(
    () => ({ ...layout, contents: grouped && layout.contents, selection }),
    [layout, selection, grouped],
  );
  const requestKey = useDebounced(JSON.stringify(request), 400);

  const check = useQuery({
    queryKey: ["customer-catalog-check", requestKey],
    queryFn: () =>
      api<CatalogCheck>("/customer-catalogs/check", { method: "POST", body: JSON.parse(requestKey) }),
  });
  const c = check.data;

  // preview of the first page, rendered by the server so it is exact
  useEffect(() => {
    let cancelled = false;
    let url: string | null = null;
    (async () => {
      try {
        const res = await postJson("/customer-catalogs/preview", JSON.parse(requestKey));
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
  }, [requestKey]);

  async function finish(version: CustomerCatalog) {
    const res = await fetch(`/api/customer-catalogs/${version.id}/file`, { headers: authHeaders() });
    if (!res.ok) throw await failure(res);
    setFileUrl(URL.createObjectURL(await res.blob()));
    setMade(version);
    setPhase("done");
    qc.invalidateQueries({ queryKey: ["customer-catalogs"] });
  }

  const jobId = job?.id;
  const jobStatus = job?.status;
  useEffect(() => {
    if (!jobId || jobStatus === "done" || jobStatus === "error") return;
    const h = window.setInterval(async () => {
      try {
        const j = await api<OutputJob>(`/customer-catalogs/jobs/${jobId}`);
        setJob(j);
        if (j.status === "done" && j.result_id) {
          const list = await api<CustomerCatalog[]>("/customer-catalogs");
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
  }, [jobId, jobStatus]);

  useEffect(
    () => () => {
      if (fileUrl) URL.revokeObjectURL(fileUrl);
    },
    [fileUrl],
  );

  useEffect(() => {
    const onKey = (e: KeyboardEvent) => {
      if (e.key === "Escape") onClose();
    };
    window.addEventListener("keydown", onKey);
    return () => window.removeEventListener("keydown", onKey);
  }, [onClose]);

  function patch(p: Partial<CatalogLayout>) {
    setLayout((o) => {
      const next = { ...o, ...p };
      try {
        window.localStorage.setItem(LAYOUT_KEY, JSON.stringify(next));
      } catch {
        /* remembering the layout is a convenience only */
      }
      return next;
    });
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
      const res = await postJson("/customer-catalogs", {
        ...request,
        title: title.trim(),
        series_id: seriesId,
        missing_photos: block ? "block" : "placeholder",
      });
      if (res.status === 202) setJob((await res.json()) as OutputJob);
      else if (!res.ok) throw await failure(res);
      else await finish((await res.json()) as CustomerCatalog);
    } catch (e) {
      setErr(e instanceof ApiError ? e.message : "Could not make the catalog. Try again.");
      setPhase("idle");
    }
  }

  const included = c?.included ?? 0;
  const canCreate =
    !!title.trim() && included > 0 && phase !== "working" && !(block && (c?.missing_photos ?? 0) > 0);
  const leftOut = c
    ? [
        c.left_out_out_of_stock && `${c.left_out_out_of_stock} out of stock`,
        c.left_out_expected && `${c.left_out_expected} expected`,
        c.left_out_discontinued && `${c.left_out_discontinued} discontinued`,
        c.no_price && `${c.no_price} with no selling price`,
      ].filter(Boolean)
    : [];

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
        aria-labelledby="mc-title"
        className="card w-full max-w-4xl bg-card p-5 shadow-xl"
      >
        <div className="flex items-start justify-between gap-3">
          <div>
            <h2 id="mc-title" className="font-serif text-xl font-semibold">
              {seriesId ? "New version of customer catalog" : "Make customer catalog"}
            </h2>
            <p className="mt-1 text-sm text-muted">
              A PDF of {count.toLocaleString("en-IN")} chosen item{count === 1 ? "" : "s"} with your prices
              and photos. Supplier prices are never shown.
            </p>
          </div>
          <button type="button" className="btn-ghost h-9 px-3" onClick={onClose}>
            Close
          </button>
        </div>

        <div className="mt-4 grid gap-5 md:grid-cols-[1fr_17rem]">
          <div className="space-y-4">
            <div>
              <label className="label" htmlFor="mc-title-input">
                Title on the cover
              </label>
              <input
                id="mc-title-input"
                className="field"
                placeholder="e.g. Steel range, October"
                maxLength={200}
                value={title}
                onChange={(e) => setTitle(e.target.value)}
              />
            </div>

            <fieldset>
              <legend className="label">Layout</legend>
              <div className="grid gap-2 sm:grid-cols-3">
                {COLUMN_CHOICES.map((ch) => (
                  <label
                    key={ch.value}
                    className={`cursor-pointer rounded-md border p-2.5 text-sm ${
                      layout.columns === ch.value ? "border-accent bg-accent-soft" : "border-line"
                    }`}
                  >
                    <input
                      type="radio"
                      name="mc-cols"
                      className="sr-only"
                      checked={layout.columns === ch.value}
                      onChange={() => patch({ columns: ch.value })}
                    />
                    <span className="block font-medium">{ch.name}</span>
                    <span className="block text-xs text-muted">{ch.detail}</span>
                  </label>
                ))}
              </div>
            </fieldset>

            <fieldset>
              <legend className="label">Arrange and show</legend>
              <div className="flex flex-wrap gap-x-6 gap-y-1.5 text-sm">
                <label className="flex items-center gap-1.5">
                  <input
                    type="radio"
                    name="mc-group"
                    checked={grouped}
                    onChange={() => patch({ group_by: "group" })}
                  />
                  By group
                </label>
                <label className="flex items-center gap-1.5">
                  <input
                    type="radio"
                    name="mc-group"
                    checked={!grouped}
                    onChange={() => patch({ group_by: "none" })}
                  />
                  One list
                </label>
                <label className={`flex items-center gap-1.5 ${grouped ? "" : "opacity-50"}`}>
                  <input
                    type="checkbox"
                    disabled={!grouped}
                    checked={grouped && layout.contents}
                    onChange={(e) => patch({ contents: e.target.checked })}
                  />
                  Contents page
                </label>
                <label className="flex items-center gap-1.5">
                  <input
                    type="checkbox"
                    checked={layout.show_code}
                    onChange={(e) => patch({ show_code: e.target.checked })}
                  />
                  Item code
                </label>
                <label className="flex items-center gap-1.5">
                  <input
                    type="radio"
                    name="mc-basis"
                    checked={layout.price_basis === "pack"}
                    onChange={() => patch({ price_basis: "pack" })}
                  />
                  Price per pack
                </label>
                <label className="flex items-center gap-1.5">
                  <input
                    type="radio"
                    name="mc-basis"
                    checked={layout.price_basis === "piece"}
                    onChange={() => patch({ price_basis: "piece" })}
                  />
                  Price per piece
                </label>
              </div>
            </fieldset>

            <fieldset>
              <legend className="label">Stock</legend>
              <div className="space-y-1.5 text-sm">
                <p className="text-xs text-muted">Items in stock are always included.</p>
                <label className="flex items-center gap-1.5">
                  <input
                    type="checkbox"
                    checked={layout.include_expected}
                    onChange={(e) =>
                      patch({
                        include_expected: e.target.checked,
                        label_expected: e.target.checked && layout.label_expected,
                      })
                    }
                  />
                  Include items that are on order (expected)
                </label>
                <label
                  className={`flex items-center gap-1.5 pl-5 ${layout.include_expected ? "" : "opacity-50"}`}
                >
                  <input
                    type="checkbox"
                    disabled={!layout.include_expected}
                    checked={layout.include_expected && layout.label_expected}
                    onChange={(e) => patch({ label_expected: e.target.checked })}
                  />
                  Mark them &quot;On order&quot; on the photo
                </label>
              </div>
            </fieldset>
          </div>

          <div>
            <p className="label">Preview (first page)</p>
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
            {c ? (
              <>
                <span className="font-medium tabular-nums">{c.included.toLocaleString("en-IN")}</span> of{" "}
                <span className="tabular-nums">{c.selected.toLocaleString("en-IN")}</span> items go in
                {leftOut.length > 0 && (
                  <span className="text-muted"> · left out: {leftOut.join(", ")}</span>
                )}
                {c.included > SYNC_LIMIT && <span className="text-muted"> · built in the background</span>}
              </>
            ) : (
              <span className="text-muted">Checking the items…</span>
            )}
          </p>

          {c && c.missing_photos > 0 && (
            <div className="mt-2 rounded-md border border-line bg-ground p-3">
              <p className="text-sm font-medium">
                {c.missing_photos} item{c.missing_photos === 1 ? " has" : "s have"} no photo
              </p>
              <ul className="mt-1 max-h-40 divide-y divide-line overflow-y-auto">
                {c.missing_photo_items.map((m) => (
                  <MissingRow key={m.item_id} item={m} onAdded={() => check.refetch()} />
                ))}
              </ul>
              {c.missing_photos > c.missing_photo_items.length && (
                <p className="mt-1 text-xs text-muted">
                  Showing the first {c.missing_photo_items.length}. Use Photos on the Items page to add
                  the rest in bulk.
                </p>
              )}
              <label className="mt-2 flex items-center gap-1.5 text-xs">
                <input type="checkbox" checked={block} onChange={(e) => setBlock(e.target.checked)} />
                Do not build until every item has a photo (otherwise a &quot;No photo&quot; placeholder
                is shown)
              </label>
            </div>
          )}

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
              {phase === "working" ? "Working…" : phase === "done" ? "Make again" : "Create catalog"}
            </button>
          </div>
        </div>
      </div>
    </div>
  );
}
