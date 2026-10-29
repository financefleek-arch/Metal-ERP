import { useState } from "react";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { api, ApiError } from "../../lib/api";
import { useDialogKeys } from "../../lib/keyboard";
import type { ShareLink } from "./ShareLinkDialog";
import { ShareLinkOptions } from "./ShareLinkOptions";
import { copyText, linkUrl, optionsBad, optionsOf, whatsappShare, type LinkOptions } from "./shareLinkUtils";

/** The shop's public catalog links: copy, send, stop, resume. */
export function ShareLinksPanel() {
  const qc = useQueryClient();
  const [err, setErr] = useState<string | null>(null);
  const [copied, setCopied] = useState<string | null>(null);
  const [editing, setEditing] = useState<ShareLink | null>(null);
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

  const save = useMutation({
    mutationFn: (v: { l: ShareLink; o: LinkOptions }) =>
      api<ShareLink>(`/share-links/${v.l.id}`, { method: "PATCH", body: v.o }),
    onSuccess: () => {
      setErr(null);
      setEditing(null);
      qc.invalidateQueries({ queryKey: ["share-links"] });
    },
    onError: (e) => setErr(e instanceof ApiError ? e.message : "That did not work. Try again."),
  });

  const rows = links.data ?? [];
  return (
    <section className="mt-8" aria-label="Catalog links">
      <h2 className="font-serif text-lg font-semibold">Links for customers</h2>
      <p className="mt-1 text-sm text-muted">
        Public links to a catalog. Customers see your current prices. Make one from the Items page:
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
                  <span className="ml-2 rounded-full border border-line px-2 py-0.5 text-[11px] text-muted">
                    {l.revoked_at ? "Stopped" : "Expired"}
                  </span>
                )}
              </p>
              <p className="truncate font-mono text-xs text-muted">{linkUrl(l)}</p>
              <p className="text-xs text-muted tabular-nums">
                Opened {l.view_count} time{l.view_count === 1 ? "" : "s"}
                {l.expires_at ? ` · stops ${new Date(l.expires_at).toLocaleDateString("en-IN")}` : ""}
                {l.selection_kind === "filter" ? " · follows a filter" : ""}
                {l.allow_pieces ? " · pieces allowed" : ""}
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
              <button type="button" className="btn-ghost h-8 px-3 text-xs" onClick={() => setEditing(l)}>
                Options
              </button>
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
      {editing && (
        <EditOptions
          link={editing}
          busy={save.isPending}
          onClose={() => setEditing(null)}
          onSave={(o) => save.mutate({ l: editing, o })}
        />
      )}
    </section>
  );
}

function EditOptions({
  link,
  busy,
  onClose,
  onSave,
}: {
  link: ShareLink;
  busy: boolean;
  onClose: () => void;
  onSave: (o: LinkOptions) => void;
}) {
  const [opts, setOpts] = useState<LinkOptions>(optionsOf(link));
  useDialogKeys(onClose);
  return (
    <div
      className="fixed inset-0 z-50 grid place-items-center overflow-y-auto bg-black/40 p-4"
      onMouseDown={(e) => {
        if (e.target === e.currentTarget) onClose();
      }}
    >
      <div role="dialog" aria-modal="true" aria-label={`Options for ${link.title}`} className="card w-full max-w-md bg-card p-5 shadow-xl">
        <h3 className="font-serif text-lg font-semibold">{link.title}</h3>
        <p className="mt-1 text-xs text-muted">Changes apply at once. The link address stays the same.</p>
        <div className="mt-3">
          <ShareLinkOptions value={opts} onChange={setOpts} idPrefix="ed" />
        </div>
        <div className="mt-4 flex justify-end gap-2">
          <button type="button" className="btn-ghost" onClick={onClose}>
            Cancel
          </button>
          <button type="button" className="btn-primary" disabled={busy || optionsBad(opts)} onClick={() => onSave(opts)}>
            {busy ? "Saving…" : "Save"}
          </button>
        </div>
      </div>
    </div>
  );
}
