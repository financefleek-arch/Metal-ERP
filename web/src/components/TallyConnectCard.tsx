import { useState } from "react";
import { useQuery } from "@tanstack/react-query";
import { api } from "../lib/api";
import { downloadFile } from "../lib/download";
import type { FirmTallyShop } from "../lib/types";

/**
 * "Connect your Tally" — the shop's own self-serve download of the same
 * installer the Fleek operator can hand out. Shown on the firm's own
 * dashboard/settings; renders nothing once Tally sync is confirmed
 * connected, so it doesn't linger as clutter after setup.
 */
export function TallyConnectCard() {
  const status = useQuery({
    queryKey: ["tally-agent-status"],
    queryFn: () => api<FirmTallyShop>("/tally/agent-status"),
    // cheap poll so the card updates itself once the shop runs the
    // installer and (later) flips Tally to Server mode — no manual refresh
    refetchInterval: 20_000,
  });
  const [downloading, setDownloading] = useState(false);
  const [err, setErr] = useState<string | null>(null);

  const data = status.data;
  if (status.isLoading || !data) return null;

  // Nothing to do or show once Tally sync is confirmed live.
  if (data.tally_status === "connected") return null;

  const download = async () => {
    setErr(null);
    setDownloading(true);
    try {
      await downloadFile("/tally/installer", "tally-agent.zip");
    } catch {
      setErr(
        data.provisioned
          ? "Download failed — try again in a moment."
          : "Tally sync hasn't been set up yet — ask Fleek to enable it.",
      );
    } finally {
      setDownloading(false);
    }
  };

  return (
    <div className="card mb-5 border-warn/30 bg-warn/5 p-4 sm:p-5">
      <div className="flex items-baseline justify-between gap-3">
        <h3 className="text-sm font-semibold">Connect your Tally</h3>
        <span className="rounded-full bg-warn/15 px-2 py-0.5 text-[11px] font-semibold text-warn">
          Not connected yet
        </span>
      </div>
      <p className="mt-2 max-w-prose text-xs text-muted">
        Install this small program on the PC where TallyPrime runs. It keeps
        your party &amp; item lists in sync — nothing to configure, just
        download and run.
      </p>

      {data.provisioned ? (
        <>
          <button
            className="btn-primary mt-3"
            disabled={downloading}
            onClick={() => void download()}
          >
            {downloading ? "Downloading…" : "↓ Download for Windows"}
          </button>
          <p className="mt-2 max-w-prose text-[11px] text-muted">
            After running it, turn on{" "}
            <span className="font-semibold">"TallyPrime acts as Server"</span>{" "}
            in Tally (F1 → Settings → Connectivity). A step-by-step guide is
            inside the download.
          </p>
          {data.agent_online && (
            <p className="mt-1.5 text-[11px] text-ok">
              Installed and checking in — waiting on Tally to be reachable.
            </p>
          )}
        </>
      ) : (
        <p className="mt-3 text-xs text-muted">
          Ask Fleek to enable Tally sync for your account, then this card
          will offer the download.
        </p>
      )}

      {err && <p className="err mt-2">{err}</p>}
    </div>
  );
}
