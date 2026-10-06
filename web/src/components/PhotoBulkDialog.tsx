import { useEffect, useMemo, useRef, useState } from "react";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { api, ApiError, apiUpload } from "../lib/api";
import { useDebounced } from "../lib/useDebounced";
import type { ItemListItem } from "../lib/types";

interface Staged {
  file_name: string;
  media_id: string | null;
  thumb_url: string | null;
  match: { item_id: string; name: string; code: string | null; method: "code" | "name" } | null;
  error: string | null;
}

const ACCEPT = "image/jpeg,image/png,image/webp";
const BATCH = 100;

/** Add photos to many items at once. Files are matched by name (the item's code, or its name);
 *  you review the matches, fix or skip any, then apply. */
export function PhotoBulkDialog({ onClose }: { onClose: () => void }) {
  const qc = useQueryClient();
  const fileRef = useRef<HTMLInputElement>(null);
  const [staged, setStaged] = useState<Staged[]>([]);
  // user choices: media_id -> item id | "" (skip). Defaults come from the match.
  const [choice, setChoice] = useState<Record<string, string>>({});
  const [names, setNames] = useState<Record<string, string>>({});
  const [busy, setBusy] = useState(false);
  const [err, setErr] = useState<string | null>(null);
  const [result, setResult] = useState<string | null>(null);
  const [picking, setPicking] = useState<string | null>(null);

  useEffect(() => {
    const onKey = (e: KeyboardEvent) => e.key === "Escape" && onClose();
    window.addEventListener("keydown", onKey);
    return () => window.removeEventListener("keydown", onKey);
  }, [onClose]);

  async function stage(files: FileList | null) {
    if (!files || files.length === 0) return;
    setErr(null);
    setResult(null);
    setBusy(true);
    try {
      const all: Staged[] = [];
      const list = Array.from(files);
      for (let i = 0; i < list.length; i += BATCH) {
        const fd = new FormData();
        list.slice(i, i + BATCH).forEach((f) => fd.append("files", f));
        all.push(...(await apiUpload<Staged[]>("/items/photos/stage", fd)));
      }
      setStaged(all);
      const c: Record<string, string> = {};
      const n: Record<string, string> = {};
      for (const s of all) {
        if (s.media_id && s.match) {
          c[s.media_id] = s.match.item_id;
          n[s.media_id] = s.match.name;
        }
      }
      setChoice(c);
      setNames(n);
    } catch (e) {
      setErr(e instanceof ApiError ? e.message : "Could not read those photos. Try again.");
    } finally {
      setBusy(false);
    }
  }

  const pairs = useMemo(
    () =>
      staged
        .filter((s) => s.media_id && choice[s.media_id!])
        .map((s) => ({ media_id: s.media_id!, item_id: choice[s.media_id!] })),
    [staged, choice],
  );
  const apply = useMutation({
    mutationFn: () =>
      api<{ applied: number; skipped: number }>("/items/photos/apply", {
        method: "POST",
        body: { pairs },
      }),
    onSuccess: (r) => {
      setErr(null);
      setResult(`${r.applied} photo${r.applied === 1 ? "" : "s"} added.`);
      setStaged([]);
      qc.invalidateQueries({ queryKey: ["items"] });
      qc.invalidateQueries({ queryKey: ["item"] });
    },
    onError: (e) => setErr(e instanceof ApiError ? e.message : "Could not add the photos."),
  });

  const unmatched = staged.filter((s) => s.media_id && !choice[s.media_id]).length;
  const broken = staged.filter((s) => s.error).length;

  return (
    <div
      className="fixed inset-0 z-50 grid place-items-center overflow-y-auto bg-black/40 p-4"
      onMouseDown={(e) => e.target === e.currentTarget && onClose()}
    >
      <div role="dialog" aria-modal="true" aria-labelledby="pb-title" className="card w-full max-w-2xl bg-card p-5 shadow-xl">
        <div className="flex items-start justify-between gap-3">
          <div>
            <h2 id="pb-title" className="font-serif text-xl font-semibold">Add photos to items</h2>
            <p className="mt-1 text-sm text-muted">
              Name each file with the item's code (for example <span className="font-mono">100234.jpg</span>)
              or its exact name. You check the matches before anything is attached.
            </p>
          </div>
          <button type="button" className="btn-ghost h-9 px-3" onClick={onClose}>Close</button>
        </div>

        <input
          ref={fileRef}
          type="file"
          multiple
          accept={ACCEPT}
          className="sr-only"
          aria-label="Choose photos"
          onChange={(e) => {
            void stage(e.target.files);
            e.target.value = "";
          }}
        />

        {staged.length === 0 && (
          <div className="mt-4 rounded-lg border-2 border-dashed border-line p-6 text-center">
            {busy ? (
              <p className="text-sm text-muted" role="status">Reading photos…</p>
            ) : (
              <>
                <button type="button" className="btn-primary" onClick={() => fileRef.current?.click()}>
                  Choose photos
                </button>
                <p className="mt-2 text-xs text-muted">JPG, PNG or WebP, up to 10 MB each.</p>
              </>
            )}
          </div>
        )}
        {result && <p className="mt-3 text-sm text-ok" role="status">{result}</p>}
        {err && <p className="err mt-3" role="alert">{err}</p>}

        {staged.length > 0 && (
          <>
            <p className="mt-4 text-sm text-muted">
              {pairs.length} of {staged.length} matched
              {unmatched > 0 && ` · ${unmatched} need an item`}
              {broken > 0 && ` · ${broken} could not be read`}
            </p>
            <ul className="mt-2 max-h-80 divide-y divide-line overflow-y-auto rounded-lg border border-line">
              {staged.map((s) => (
                <li key={s.file_name + (s.media_id ?? "")} className="flex items-center gap-3 px-3 py-2">
                  {s.thumb_url ? (
                    <img src={s.thumb_url} alt="" className="h-10 w-10 shrink-0 rounded border border-line object-cover" />
                  ) : (
                    <div className="h-10 w-10 shrink-0 rounded border border-line bg-ground" />
                  )}
                  <div className="min-w-0 flex-1">
                    <p className="truncate text-xs text-muted">{s.file_name}</p>
                    {s.error ? (
                      <p className="text-xs text-danger">{s.error}</p>
                    ) : picking === s.media_id ? (
                      <ItemPicker
                        onPick={(it) => {
                          setChoice((c) => ({ ...c, [s.media_id!]: it.id }));
                          setNames((n) => ({ ...n, [s.media_id!]: it.name }));
                          setPicking(null);
                        }}
                        onCancel={() => setPicking(null)}
                      />
                    ) : choice[s.media_id!] ? (
                      <p className="truncate text-sm">
                        {names[s.media_id!]}
                        {s.match && choice[s.media_id!] === s.match.item_id && (
                          <span className="ml-1 text-[10px] text-muted">matched by {s.match.method}</span>
                        )}
                      </p>
                    ) : (
                      <p className="text-sm text-warn">No item matched</p>
                    )}
                  </div>
                  {!s.error && picking !== s.media_id && (
                    <span className="flex shrink-0 gap-2 text-xs">
                      <button type="button" className="text-accent hover:underline" onClick={() => setPicking(s.media_id)}>
                        {choice[s.media_id!] ? "Change" : "Pick item"}
                      </button>
                      {choice[s.media_id!] && (
                        <button
                          type="button"
                          className="text-muted hover:underline"
                          onClick={() => setChoice((c) => ({ ...c, [s.media_id!]: "" }))}
                        >
                          Skip
                        </button>
                      )}
                    </span>
                  )}
                </li>
              ))}
            </ul>
            <div className="mt-4 flex items-center justify-between gap-3">
              <button type="button" className="btn-ghost" onClick={() => setStaged([])}>
                Start over
              </button>
              <button
                type="button"
                className="btn-primary"
                disabled={pairs.length === 0 || apply.isPending}
                onClick={() => apply.mutate()}
              >
                {apply.isPending ? "Adding…" : `Add ${pairs.length} photo${pairs.length === 1 ? "" : "s"}`}
              </button>
            </div>
          </>
        )}
      </div>
    </div>
  );
}

function ItemPicker({ onPick, onCancel }: { onPick: (it: ItemListItem) => void; onCancel: () => void }) {
  const [q, setQ] = useState("");
  const dq = useDebounced(q.trim(), 250);
  const res = useQuery({
    queryKey: ["item-search", dq],
    queryFn: () => api<ItemListItem[]>(`/items?q=${encodeURIComponent(dq)}`),
    enabled: dq.length >= 2,
  });
  return (
    <div>
      <input
        autoFocus
        className="field h-8 text-xs"
        placeholder="Search an item…"
        aria-label="Search an item"
        value={q}
        onChange={(e) => setQ(e.target.value)}
        onKeyDown={(e) => e.key === "Escape" && onCancel()}
      />
      {(res.data?.length ?? 0) > 0 && (
        <ul className="mt-1 max-h-32 overflow-y-auto rounded border border-line bg-card text-xs shadow">
          {res.data!.slice(0, 8).map((it) => (
            <li key={it.id}>
              <button type="button" className="block w-full px-2 py-1.5 text-left hover:bg-accent-soft" onClick={() => onPick(it)}>
                {it.name}
                {it.sku && <span className="ml-1 font-mono text-muted">{it.sku}</span>}
              </button>
            </li>
          ))}
        </ul>
      )}
    </div>
  );
}
