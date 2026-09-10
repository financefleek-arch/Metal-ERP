import { useState } from "react";
import { useQuery } from "@tanstack/react-query";
import { api } from "../lib/api";
import { downloadFile } from "../lib/download";
import type { FirmBackupList, FirmTallyShop } from "../lib/types";

/**
 * The firm's own cloud backups of its Tally data — the companion agent
 * ships each landed Tally backup to cloud storage; this lists the last few
 * and lets the firm download one.
 *
 * There is no server-triggered restore: the firm downloads the file and
 * runs Tally's own Restore (Alt+F3 -> Data -> Restore), which is an
 * exclusive-access company operation Tally's HTTP gateway can't do.
 *
 * Shown only once an agent is provisioned; hidden entirely otherwise so it
 * doesn't pre-empt the "Connect your Tally" card.
 */
function fmtSize(bytes: number): string {
  if (bytes < 1024) return `${bytes} B`;
  if (bytes < 1024 * 1024) return `${(bytes / 1024).toFixed(0)} KB`;
  return `${(bytes / (1024 * 1024)).toFixed(1)} MB`;
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

  const rows = backups.data?.backups ?? [];
  const keep = backups.data?.retention_count;

  return (
    <div className="card mb-5 p-4 sm:p-5">
      <div className="flex items-baseline justify-between gap-3">
        <h3 className="text-sm font-semibold">Tally backups</h3>
        {keep != null && (
          <span className="text-[11px] text-muted">Keeping the last {keep}</span>
        )}
      </div>
      <p className="mt-2 max-w-prose text-xs text-muted">
        Copies of your Tally data, uploaded automatically by the companion
        agent. To restore one, download it and use{" "}
        <span className="font-semibold">Restore</span> inside Tally
        (Alt+F3 → Data → Restore).
      </p>

      {backups.isLoading ? (
        <p className="mt-3 text-xs text-muted">Loading…</p>
      ) : rows.length === 0 ? (
        <p className="mt-3 text-xs text-muted">
          No backups synced yet — they’ll appear here once the agent has
          uploaded your first Tally backup.
        </p>
      ) : (
        <ul className="mt-3 divide-y divide-[#f3eee4]">
          {rows.map((b) => (
            <li
              key={b.id}
              className="flex items-center justify-between gap-3 py-2"
            >
              <div className="min-w-0">
                <p className="truncate text-xs font-medium">{b.filename}</p>
                <p className="text-[11px] text-muted">
                  {b.uploaded_at
                    ? new Date(b.uploaded_at).toLocaleString()
                    : "—"}{" "}
                  · {fmtSize(b.size_bytes)}
                </p>
              </div>
              <button
                className="btn-ghost shrink-0 text-xs"
                disabled={busy === b.id}
                onClick={() => void download(b.id, b.filename)}
              >
                {busy === b.id ? "Downloading…" : "↓ Download"}
              </button>
            </li>
          ))}
        </ul>
      )}

      {err && <p className="err mt-2">{err}</p>}
    </div>
  );
}
