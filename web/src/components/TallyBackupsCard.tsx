import { useState } from "react";
import { useQuery } from "@tanstack/react-query";
import { api } from "../lib/api";
import { downloadFile } from "../lib/download";
import type { FirmBackupList, FirmBackupSet, FirmTallyShop } from "../lib/types";

/**
 * The firm's own cloud backups of its Tally data — the companion agent
 * ships each TallyPrime backup run (a manifest + one or more data parts)
 * to cloud storage. This lists the recent runs; the firm downloads every
 * file of a run into one folder and uses Tally's own Restore.
 *
 * There is no server-triggered restore: Restore (Alt+F3 -> Data ->
 * Restore) is an exclusive-access company operation Tally's HTTP gateway
 * can't do.
 *
 * Shown only once an agent is provisioned; hidden entirely otherwise so it
 * doesn't pre-empt the "Connect your Tally" card.
 */
function fmtSize(bytes: number): string {
  if (bytes < 1024) return `${bytes} B`;
  if (bytes < 1024 * 1024) return `${(bytes / 1024).toFixed(0)} KB`;
  return `${(bytes / (1024 * 1024)).toFixed(1)} MB`;
}

function BackupSetRow({
  set,
  busy,
  onDownload,
}: {
  set: FirmBackupSet;
  busy: string | null;
  onDownload: (id: string, name: string) => void;
}) {
  const [open, setOpen] = useState(false);
  const when = set.uploaded_at
    ? new Date(set.uploaded_at).toLocaleString()
    : "—";
  const fileWord = set.files.length === 1 ? "file" : "files";

  return (
    <li className="py-2">
      <div className="flex items-center justify-between gap-3">
        <button
          className="min-w-0 text-left"
          onClick={() => setOpen((v) => !v)}
        >
          <p className="truncate text-xs font-medium">
            {open ? "▾" : "▸"} Backup — {when}
          </p>
          <p className="text-[11px] text-muted">
            {set.files.length} {fileWord} · {fmtSize(set.total_bytes)}
          </p>
        </button>
      </div>

      {open && (
        <ul className="mt-1.5 space-y-1 border-l border-line pl-3">
          {set.files.map((f) => (
            <li
              key={f.id}
              className="flex items-center justify-between gap-3"
            >
              <span className="truncate text-[11px]">
                {f.filename}{" "}
                <span className="text-muted">· {fmtSize(f.size_bytes)}</span>
              </span>
              <button
                className="btn-ghost shrink-0 text-[11px]"
                disabled={busy === f.id}
                onClick={() => onDownload(f.id, f.filename)}
              >
                {busy === f.id ? "…" : "↓"}
              </button>
            </li>
          ))}
          <li className="pt-0.5 text-[11px] text-muted">
            Download all {set.files.length} into one folder, then use Tally’s
            Restore.
          </li>
        </ul>
      )}
    </li>
  );
}

export function TallyBackupsCard() {
  const status = useQuery({
    queryKey: ["tally-agent-status"],
    queryFn: () => api<FirmTallyShop>("/tally/agent-status"),
  });
  const provisioned = status.data?.provisioned ?? false;

  const backups = useQuery({
    queryKey: ["tally-backups"],
    queryFn: () => api<FirmBackupList>("/tally/backups"),
    enabled: provisioned,
    refetchInterval: 60_000,
  });

  const [busy, setBusy] = useState<string | null>(null);
  const [err, setErr] = useState<string | null>(null);

  if (!provisioned) return null;

  const download = async (id: string, name: string) => {
    setErr(null);
    setBusy(id);
    try {
      await downloadFile(`/tally/backups/${id}/download`, name);
    } catch {
      setErr("Download failed — try again in a moment.");
    } finally {
      setBusy(null);
    }
  };

  const sets = backups.data?.sets ?? [];
  const keep = backups.data?.retention_count;

  return (
    <div className="card mb-5 p-4 sm:p-5">
      <div className="flex items-baseline justify-between gap-3">
        <h3 className="text-sm font-semibold">Tally backups</h3>
        {keep != null && (
          <span className="text-[11px] text-muted">
            Keeping the last {keep}
          </span>
        )}
      </div>
      <p className="mt-2 max-w-prose text-xs text-muted">
        Copies of your Tally data, uploaded automatically by the companion
        agent. Each backup is a set of files — download all of them into one
        folder, then use <span className="font-semibold">Restore</span> inside
        Tally (Alt+F3 → Data → Restore).
      </p>

      {backups.isLoading ? (
        <p className="mt-3 text-xs text-muted">Loading…</p>
      ) : sets.length === 0 ? (
        <div className="mt-3 space-y-2 text-xs text-muted">
          <p>
            No backups synced yet. If it stays empty, check Tally's backup
            setup:
          </p>
          <ul className="ml-4 list-disc space-y-1">
            <li>
              Gateway of Tally → Alt+Y → Backup is scheduled (at least
              daily).
            </li>
            <li>
              Destination is{" "}
              <span className="font-semibold">Specify Path</span> to a local
              folder — not TallyDrive.
            </li>
            <li>
              It's a normal <span className="font-semibold">Data Backup</span>{" "}
              (the agent uses the <code>TDBK…</code> / <code>TBK…</code> files;
              it ignores the SQL/ODBC <code>TSDBK</code> export).
            </li>
            <li>
              <span className="font-semibold">Versioned backups</span> are on,
              so each run keeps a new file instead of overwriting one.
            </li>
          </ul>
        </div>
      ) : (
        <ul className="mt-3 divide-y divide-[#f3eee4]">
          {sets.map((s, i) => (
            <BackupSetRow
              key={s.set_id ?? s.files[0]?.id ?? i}
              set={s}
              busy={busy}
              onDownload={download}
            />
          ))}
        </ul>
      )}

      {err && <p className="err mt-2">{err}</p>}
    </div>
  );
}
