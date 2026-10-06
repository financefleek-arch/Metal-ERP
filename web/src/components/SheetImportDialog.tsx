import { useEffect, useRef, useState } from "react";
import { useQueryClient } from "@tanstack/react-query";
import { ApiError, apiUpload } from "../lib/api";

interface SheetRow {
  row: number;
  code: string;
  name: string;
  result: "changed" | "unchanged" | "error";
  detail: string | null;
}
interface SheetResult {
  dry_run: boolean;
  changed: number;
  unchanged: number;
  errors: number;
  rows: SheetRow[];
}

/** Update items from an Excel (or CSV) sheet, matched by code: check first, then apply. */
export function SheetImportDialog({ onClose }: { onClose: () => void }) {
  const qc = useQueryClient();
  const input = useRef<HTMLInputElement>(null);
  const [file, setFile] = useState<File | null>(null);
  const [res, setRes] = useState<SheetResult | null>(null);
  const [busy, setBusy] = useState(false);
  const [applied, setApplied] = useState(false);
  const [err, setErr] = useState<string | null>(null);

  useEffect(() => {
    const onKey = (e: KeyboardEvent) => {
      if (e.key === "Escape") onClose();
    };
    window.addEventListener("keydown", onKey);
    return () => window.removeEventListener("keydown", onKey);
  }, [onClose]);

  async function run(f: File, dry: boolean) {
    setBusy(true);
    setErr(null);
    try {
      const fd = new FormData();
      fd.append("file", f);
      const r = await apiUpload<SheetResult>(`/item-sheet/import?dry_run=${dry}`, fd);
      setRes(r);
      if (!dry) {
        setApplied(true);
        qc.invalidateQueries({ queryKey: ["items"] });
        qc.invalidateQueries({ queryKey: ["item-tree"] });
      }
    } catch (e) {
      setErr(e instanceof ApiError ? e.message : "Could not read that file. Try again.");
    } finally {
      setBusy(false);
    }
  }

  const problems = res?.rows.filter((r) => r.result === "error") ?? [];
  const changes = res?.rows.filter((r) => r.result === "changed") ?? [];

  return (
    <div
      className="fixed inset-0 z-50 grid place-items-center overflow-y-auto bg-black/40 p-4"
      onMouseDown={(e) => {
        if (e.target === e.currentTarget) onClose();
      }}
    >
      <div role="dialog" aria-modal="true" aria-labelledby="sheet-title" className="card w-full max-w-2xl bg-card p-5 shadow-xl">
        <div className="flex items-start justify-between gap-3">
          <div>
            <h2 id="sheet-title" className="font-serif text-xl font-semibold">
              Update items from Excel
            </h2>
            <p className="mt-1 text-sm text-muted">
              Export your items, change them in Excel, and load the sheet back. Rows are matched by{" "}
              <b>Code</b>, so you can change names. Blank cells change nothing; no item is created or
              deleted.
            </p>
          </div>
          <button type="button" className="btn-ghost h-9 px-3" onClick={onClose}>
            Close
          </button>
        </div>

        <div className="mt-4">
          <input
            ref={input}
            type="file"
            accept=".xlsx,.csv"
            className="sr-only"
            onChange={(e) => {
              const f = e.target.files?.[0] ?? null;
              setFile(f);
              setRes(null);
              setApplied(false);
              if (f) void run(f, true);
              e.target.value = "";
            }}
          />
          <button type="button" className="btn-primary" disabled={busy} onClick={() => input.current?.click()}>
            {file ? "Choose another file" : "Choose a file"}
          </button>
          {file && <span className="ml-3 text-sm text-muted">{file.name}</span>}
        </div>

        {busy && <p className="mt-3 text-sm text-muted">Reading the sheet…</p>}
        {err && (
          <p className="err" role="alert">
            {err}
          </p>
        )}

        {res && (
          <div className="mt-4 text-sm" role="status">
            <p>
              {applied ? "Done: " : "Check: "}
              <b className="tabular-nums">{res.changed}</b> item{res.changed === 1 ? "" : "s"}{" "}
              {applied ? "updated" : "will change"}, <span className="tabular-nums">{res.unchanged}</span> already
              match
              {res.errors > 0 && (
                <>
                  , <b className="tabular-nums text-danger">{res.errors}</b> row{res.errors === 1 ? "" : "s"} cannot be
                  used
                </>
              )}
              .
            </p>
            {problems.length > 0 && (
              <ul className="mt-2 max-h-40 list-disc overflow-y-auto pl-5 text-xs">
                {problems.slice(0, 50).map((r) => (
                  <li key={r.row}>
                    Row {r.row}
                    {r.code ? ` (${r.code})` : ""}: {r.detail}
                  </li>
                ))}
              </ul>
            )}
            {changes.length > 0 && (
              <details className="mt-2 text-xs text-muted">
                <summary className="cursor-pointer">Show the changes</summary>
                <ul className="mt-1 max-h-48 list-disc overflow-y-auto pl-5">
                  {changes.slice(0, 200).map((r) => (
                    <li key={r.row}>
                      {r.name}: {r.detail}
                    </li>
                  ))}
                </ul>
              </details>
            )}
          </div>
        )}

        <div className="mt-4 flex justify-end gap-2">
          <button type="button" className="btn-ghost" onClick={onClose}>
            {applied ? "Close" : "Cancel"}
          </button>
          {!applied && (
            <button
              type="button"
              className="btn-primary"
              disabled={!file || !res || res.changed === 0 || busy}
              onClick={() => file && void run(file, false)}
            >
              {res ? `Update ${res.changed} item${res.changed === 1 ? "" : "s"}` : "Update"}
            </button>
          )}
        </div>
      </div>
    </div>
  );
}
