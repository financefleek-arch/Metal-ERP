import { useEffect, useState } from "react";
import { useMutation, useQueryClient } from "@tanstack/react-query";
import { api, ApiError } from "../../lib/api";
import type { CatalogSelection } from "../../lib/catalog";
import { copyText, linkUrl, whatsappShare } from "./shareLinkUtils";

export interface ShareLink {
  id: string;
  title: string;
  token: string;
  path: string;
  include_expected: boolean;
  expires_at: string | null;
  revoked_at: string | null;
  live: boolean;
  view_count: number;
  last_viewed_at: string | null;
  created_at: string;
  selection_kind: "ids" | "filter";
}

/** Make a public link to the chosen items. Customers who open it see the items as they are now. */
export function ShareLinkDialog({
  selection,
  count,
  firmName,
  onClose,
}: {
  selection: CatalogSelection;
  count: number;
  firmName?: string;
  onClose: () => void;
}) {
  const qc = useQueryClient();
  const [title, setTitle] = useState("");
  const [expected, setExpected] = useState(false);
  const [days, setDays] = useState<"" | "7" | "30" | "90">("");
  const [made, setMade] = useState<ShareLink | null>(null);
  const [copied, setCopied] = useState(false);
  const [err, setErr] = useState<string | null>(null);

  useEffect(() => {
    const onKey = (e: KeyboardEvent) => e.key === "Escape" && onClose();
    window.addEventListener("keydown", onKey);
    return () => window.removeEventListener("keydown", onKey);
  }, [onClose]);

  const create = useMutation({
    mutationFn: () =>
      api<ShareLink>("/share-links", {
        method: "POST",
        body: {
          title: title.trim(),
          selection,
          include_expected: expected,
          expires_at: days ? new Date(Date.now() + Number(days) * 86_400_000).toISOString() : null,
        },
      }),
    onSuccess: (l) => {
      setErr(null);
      setMade(l);
      qc.invalidateQueries({ queryKey: ["share-links"] });
    },
    onError: (e) => setErr(e instanceof ApiError ? e.message : "Could not make the link. Try again."),
  });

  return (
    <div
      className="fixed inset-0 z-50 grid place-items-center overflow-y-auto bg-black/40 p-4"
      onMouseDown={(e) => {
        if (e.target === e.currentTarget) onClose();
      }}
    >
      <div role="dialog" aria-modal="true" aria-labelledby="sl-title" className="card w-full max-w-md bg-card p-5 shadow-xl">
        <div className="flex items-start justify-between gap-3">
          <div>
            <h2 id="sl-title" className="font-serif text-xl font-semibold">
              Share catalog with customers
            </h2>
            <p className="mt-1 text-sm text-muted">
              {count.toLocaleString("en-IN")} chosen item{count === 1 ? "" : "s"}. Anyone with the link can look at
              them, with your current prices. Only items in stock with a price are shown.
            </p>
          </div>
          <button type="button" className="btn-ghost h-9 px-3" onClick={onClose}>
            Close
          </button>
        </div>

        {!made ? (
          <>
            <div className="mt-4">
              <label className="label" htmlFor="sl-name">
                Name shown to customers
              </label>
              <input
                id="sl-name"
                className="field"
                placeholder="e.g. Steel range, October"
                maxLength={200}
                value={title}
                onChange={(e) => setTitle(e.target.value)}
              />
            </div>
            <label className="mt-3 flex items-start gap-2 text-sm">
              <input type="checkbox" className="mt-1" checked={expected} onChange={(e) => setExpected(e.target.checked)} />
              <span>
                Also show items that are on order
                <span className="block text-xs text-muted">They appear tagged &ldquo;On order&rdquo;.</span>
              </span>
            </label>
            <div className="mt-3">
              <label className="label" htmlFor="sl-exp">
                Link stops working
              </label>
              <select id="sl-exp" className="field w-48" value={days} onChange={(e) => setDays(e.target.value as typeof days)}>
                <option value="">Never (until you stop it)</option>
                <option value="7">After 7 days</option>
                <option value="30">After 30 days</option>
                <option value="90">After 90 days</option>
              </select>
            </div>
            {err && (
              <p className="err" role="alert">
                {err}
              </p>
            )}
            <div className="mt-4 flex justify-end gap-2">
              <button type="button" className="btn-ghost" onClick={onClose}>
                Cancel
              </button>
              <button
                type="button"
                className="btn-primary"
                disabled={!title.trim() || create.isPending}
                onClick={() => create.mutate()}
              >
                {create.isPending ? "Making…" : "Make link"}
              </button>
            </div>
          </>
        ) : (
          <div className="mt-4" role="status">
            <p className="text-sm font-medium text-ok">Your link is ready.</p>
            <div className="mt-2 flex gap-2">
              <input readOnly className="field font-mono text-xs" value={linkUrl(made)} onFocus={(e) => e.currentTarget.select()} />
              <button
                type="button"
                className="btn-primary"
                onClick={async () => {
                  setCopied(await copyText(linkUrl(made)));
                  window.setTimeout(() => setCopied(false), 1500);
                }}
              >
                {copied ? "Copied" : "Copy"}
              </button>
            </div>
            <div className="mt-3 flex flex-wrap gap-2">
              <a className="btn-ghost" href={whatsappShare(made, firmName)} target="_blank" rel="noopener noreferrer">
                Send on WhatsApp
              </a>
              <a className="btn-ghost" href={made.path} target="_blank" rel="noopener noreferrer">
                Preview
              </a>
            </div>
            <p className="mt-3 text-xs text-muted">
              Manage or stop your links under Items &rsaquo; Catalogs. Stopping a link takes effect at once.
            </p>
          </div>
        )}
      </div>
    </div>
  );
}
