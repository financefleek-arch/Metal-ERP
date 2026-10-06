import { useCallback, useEffect, useRef, useState } from "react";
import { useQueryClient } from "@tanstack/react-query";
import { api, ApiError, apiPage, apiUpload } from "../lib/api";
import type { ItemFilter, ItemListItem } from "../lib/types";

const PAGE = 50;
const MAX_MB = 15;

function query(f: ItemFilter, cursor: string | null): string {
  const p = new URLSearchParams();
  p.set("limit", String(PAGE));
  if (f.q) p.set("q", f.q);
  for (const a of f.availability ?? []) p.append("availability", a);
  if (f.catalog_id) p.set("catalog_id", f.catalog_id);
  if (f.supplier_id) p.set("supplier_id", f.supplier_id);
  p.set("no_photo", "true");
  if (cursor) p.set("cursor", cursor);
  return p.toString();
}

/**
 * Photo queue: one item at a time that still has no photo. Take a picture (opens the camera on
 * a phone), choose a file, or paste one; it saves and moves to the next. A scanner (or typing a
 * code) jumps straight to that item, even if it already has a photo.
 */
export function PhotoQueueDialog({ filter, onClose }: { filter: ItemFilter; onClose: () => void }) {
  const qc = useQueryClient();
  const camera = useRef<HTMLInputElement>(null);
  const file = useRef<HTMLInputElement>(null);
  const [queue, setQueue] = useState<ItemListItem[]>([]);
  const [total, setTotal] = useState<number | null>(null);
  const [done, setDone] = useState(0);
  const [loading, setLoading] = useState(true);
  const [busy, setBusy] = useState(false);
  const [err, setErr] = useState<string | null>(null);
  const [scan, setScan] = useState("");
  const [picked, setPicked] = useState<ItemListItem | null>(null); // from a scan

  const load = useCallback(async () => {
    setLoading(true);
    try {
      const [page, n] = await Promise.all([
        apiPage<ItemListItem[]>(`/items?${query(filter, null)}`),
        api<{ count: number }>("/items/count", { method: "POST", body: { ...filter, no_photo: true } }),
      ]);
      setQueue(page.data);
      setTotal(n.count);
    } catch (e) {
      setErr(e instanceof ApiError ? e.message : "Could not load the items.");
    } finally {
      setLoading(false);
    }
  }, [filter]);

  useEffect(() => {
    void load();
  }, [load]);

  const current = picked ?? queue[0] ?? null;

  const advance = useCallback(
    (wasUpload: boolean) => {
      if (picked) {
        setPicked(null);
      } else {
        setQueue((q) => q.slice(1));
      }
      if (wasUpload) {
        setDone((d) => d + 1);
        setTotal((t) => (t == null ? t : Math.max(0, t - (picked ? 0 : 1))));
      }
    },
    [picked],
  );

  // top up the queue when it runs low but more are waiting
  useEffect(() => {
    if (!loading && queue.length === 0 && (total ?? 0) > 0 && !picked) void load();
  }, [queue.length, loading, total, picked, load]);

  const upload = useCallback(
    async (f: File) => {
      if (!current) return;
      if (f.size > MAX_MB * 1024 * 1024) {
        setErr(`That photo is over ${MAX_MB} MB.`);
        return;
      }
      setBusy(true);
      setErr(null);
      try {
        const fd = new FormData();
        fd.append("file", f);
        await apiUpload(`/items/${current.id}/photo`, fd);
        advance(true);
        qc.invalidateQueries({ queryKey: ["items"] });
      } catch (e) {
        setErr(e instanceof ApiError ? e.message : "Could not save that photo. Try again.");
      } finally {
        setBusy(false);
      }
    },
    [current, advance, qc],
  );

  // paste an image from the clipboard
  useEffect(() => {
    const onPaste = (e: ClipboardEvent) => {
      const f = [...(e.clipboardData?.files ?? [])].find((x) => x.type.startsWith("image/"));
      if (f) {
        e.preventDefault();
        void upload(f);
      }
    };
    window.addEventListener("paste", onPaste);
    return () => window.removeEventListener("paste", onPaste);
  }, [upload]);

  useEffect(() => {
    const onKey = (e: KeyboardEvent) => {
      if (e.key === "Escape") onClose();
    };
    window.addEventListener("keydown", onKey);
    return () => window.removeEventListener("keydown", onKey);
  }, [onClose]);

  async function findByCode() {
    const code = scan.trim();
    if (!code) return;
    setErr(null);
    try {
      const rows = await api<ItemListItem[]>(`/items?q=${encodeURIComponent(code)}&limit=20`);
      const hit = rows.find((r) => r.sku === code) ?? (rows.length === 1 ? rows[0] : null);
      if (!hit) {
        setErr(`No item has the code ${code}.`);
        return;
      }
      setPicked(hit);
      setScan("");
    } catch (e) {
      setErr(e instanceof ApiError ? e.message : "Could not look that up.");
    }
  }

  const left = Math.max(0, (total ?? 0) - (picked ? 0 : 0));

  return (
    <div
      className="fixed inset-0 z-50 grid place-items-center overflow-y-auto bg-black/40 p-4"
      onMouseDown={(e) => {
        if (e.target === e.currentTarget) onClose();
      }}
    >
      <div role="dialog" aria-modal="true" aria-labelledby="pq-title" className="card w-full max-w-lg bg-card p-5 shadow-xl">
        <div className="flex items-start justify-between gap-3">
          <div>
            <h2 id="pq-title" className="font-serif text-xl font-semibold">
              Photo queue
            </h2>
            <p className="mt-1 text-sm text-muted">
              {loading
                ? "Loading…"
                : total === 0 && !picked
                  ? "Every item here has a photo."
                  : `${left.toLocaleString("en-IN")} item${left === 1 ? "" : "s"} waiting for a photo${
                      done ? ` · ${done} added this time` : ""
                    }.`}
            </p>
          </div>
          <button type="button" className="btn-ghost h-9 px-3" onClick={onClose}>
            Done
          </button>
        </div>

        <form
          className="mt-3 flex gap-2"
          onSubmit={(e) => {
            e.preventDefault();
            void findByCode();
          }}
        >
          <input
            className="field"
            placeholder="Scan or type an item code"
            aria-label="Scan or type an item code"
            value={scan}
            onChange={(e) => setScan(e.target.value)}
          />
          <button className="btn-ghost" disabled={!scan.trim()}>
            Go
          </button>
        </form>

        {current && (
          <div className="mt-4 rounded-lg border border-line bg-ground p-4 text-center">
            {current.photo_url ? (
              <img src={current.photo_url} alt="" className="mx-auto mb-3 h-40 rounded object-contain" />
            ) : (
              <div className="mx-auto mb-3 grid h-28 w-40 place-items-center rounded border border-dashed border-line text-xs text-muted">
                No photo yet
              </div>
            )}
            <p className="text-base font-medium">{current.name}</p>
            <p className="text-xs text-muted">{current.sku ? `Code ${current.sku}` : "No code yet"}</p>
          </div>
        )}

        {err && (
          <p className="err" role="alert">
            {err}
          </p>
        )}

        {current && (
          <>
            <input
              ref={camera}
              type="file"
              accept="image/*"
              capture="environment"
              className="sr-only"
              tabIndex={-1}
              onChange={(e) => {
                const f = e.target.files?.[0];
                if (f) void upload(f);
                e.target.value = "";
              }}
            />
            <input
              ref={file}
              type="file"
              accept="image/*"
              className="sr-only"
              tabIndex={-1}
              onChange={(e) => {
                const f = e.target.files?.[0];
                if (f) void upload(f);
                e.target.value = "";
              }}
            />
            <div className="mt-4 flex flex-wrap gap-2">
              <button type="button" className="btn-primary flex-1" disabled={busy} onClick={() => camera.current?.click()}>
                {busy ? "Saving…" : "Take photo"}
              </button>
              <button type="button" className="btn-ghost flex-1" disabled={busy} onClick={() => file.current?.click()}>
                Choose a file
              </button>
              <button type="button" className="btn-ghost" disabled={busy} onClick={() => advance(false)}>
                Skip
              </button>
            </div>
            <p className="mt-2 text-xs text-muted">You can also paste a picture (Ctrl+V). It saves and moves to the next item.</p>
          </>
        )}
      </div>
    </div>
  );
}
