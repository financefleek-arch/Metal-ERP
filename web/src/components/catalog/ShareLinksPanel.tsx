import { useState } from "react";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { api, ApiError } from "../../lib/api";
import { copyText, linkUrl, whatsappShare, type ShareLink } from "./ShareLinkDialog";

/** The shop's public catalog links: copy, send, stop, resume. */
export function ShareLinksPanel() {
  const qc = useQueryClient();
  const [err, setErr] = useState<string | null>(null);
  const [copied, setCopied] = useState<string | null>(null);
  const links = useQuery({ queryKey: ["share-links"], queryFn: () => api<ShareLink[]>("/share-links") });

  const toggle = useMutation({
    mutationFn: (l: ShareLink) =>
      api<ShareLink>(`/share-links/${l.id}`, { method: "PATCH", body: { revoked: l.live } }),
    onSuccess: () => {
      setErr(null);
      qc.invalidateQueries({ queryKey: ["share-links"] });
    },
    onError: (e) => setErr(e instanceof ApiError ? e.message : "That did not work. Try again."),
  });

  const rows = links.data ?? [];
  return (
    <section className="mt-8" aria-label="Catalog links">
      <h2 className="font-serif text-lg font-semibold">Links for customers</h2>
      <p className="mt-1 text-sm text-muted">
        Public links to a catalog. Customers see current prices and what is in stock. Make one from the Items page:
        select items, then Share catalog link.
      </p>
      {err && (
        <p className="err" role="alert">
          {err}
        </p>
      )}
      <div className="card mt-3 divide-y divide-line bg-card">
        {links.isLoading && <p className="p-4 text-sm text-muted">Loading…</p>}
        {links.data && rows.length === 0 && <p className="p-5 text-center text-sm text-muted">No links yet.</p>}
        {rows.map((l) => (
          <div key={l.id} className="flex flex-wrap items-center gap-x-4 gap-y-2 p-3">
            <div className="min-w-0 flex-1">
              <p className="truncate font-medium">
                {l.title}
                {!l.live && (
                  <span className="ml-2 rounded-full border border-line px-2 py-0.5 text-[10px] text-muted">
                    {l.revoked_at ? "Stopped" : "Expired"}
                  </span>
                )}
              </p>
              <p className="truncate font-mono text-xs text-muted">{linkUrl(l)}</p>
              <p className="text-xs text-muted tabular-nums">
                Opened {l.view_count} time{l.view_count === 1 ? "" : "s"}
                {l.expires_at ? ` · stops ${new Date(l.expires_at).toLocaleDateString("en-IN")}` : ""}
                {l.selection_kind === "filter" ? " · follows a filter" : ""}
              </p>
            </div>
            <div className="flex flex-wrap gap-2">
              {l.live && (
                <>
                  <button
                    type="button"
                    className="btn-ghost h-8 px-3 text-xs"
                    onClick={async () => {
                      if (await copyText(linkUrl(l))) {
                        setCopied(l.id);
                        window.setTimeout(() => setCopied(null), 1500);
                      }
                    }}
                  >
                    {copied === l.id ? "Copied" : "Copy"}
                  </button>
                  <a
                    className="btn-ghost h-8 px-3 text-xs"
                    href={whatsappShare(l)}
                    target="_blank"
                    rel="noopener noreferrer"
                  >
                    WhatsApp
                  </a>
                </>
              )}
              {(l.live || l.revoked_at) && (
                <button
                  type="button"
                  className="btn-ghost h-8 px-3 text-xs"
                  disabled={toggle.isPending}
                  onClick={() => toggle.mutate(l)}
                >
                  {l.live ? "Stop sharing" : "Resume"}
                </button>
              )}
            </div>
          </div>
        ))}
      </div>
    </section>
  );
}
