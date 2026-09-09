/** Tally push-status UI: a compact badge for a list row, and a status-plus-
 *  push panel for a detail page. Mirrors WhatsappStatus.tsx's split (list
 *  badge / detail panel). Shared between invoices (F1b-1) and inward bills
 *  (F5d) — both entities expose the same shape (`tally_sync_status` on the
 *  list row, `GET .../push-status` + `POST .../push` on the detail route),
 *  so `TallyPushPanel` takes the two URLs directly rather than hardcoding
 *  an entity type.
 *
 *  The list badge reads the entity's own `tally_sync_status` field
 *  (collapsed server-side from the latest tally_sync_job — no extra
 *  request per row). The detail panel calls the live push-status endpoint
 *  directly, since it needs the specific blocker reasons the list's
 *  three-state summary doesn't carry. */

import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { api } from "../lib/api";
import type { TallyPushBlockers, TallySyncJob } from "../lib/types";

type ListStatus = "pending" | "synced" | "error";

const LIST_LOOK: Record<ListStatus, { label: string; cls: string }> = {
  pending: { label: "Syncing…", cls: "text-muted" },
  synced: { label: "Synced to Tally", cls: "text-[#25a566]" },
  error: { label: "Tally sync failed", cls: "text-danger" },
};

/** One line for the invoice list. `null` => never attempted — renders
 *  nothing at all, since most invoices for most firms will never have a
 *  Tally connection and shouldn't carry UI noise for it. */
export function TallySyncBadge({ status }: { status: ListStatus | null }) {
  if (!status) return null;
  const look = LIST_LOOK[status];
  return (
    <span
      className={`inline-flex items-center gap-1 whitespace-nowrap text-[11px] font-semibold ${look.cls}`}
      title={look.label}
    >
      {look.label}
    </span>
  );
}

/** Detail-page panel: current status + a manual "Push to Tally" button
 *  when the entity is pushable but hasn't been pushed automatically
 *  (finalize/approve's best-effort enqueue missed it — Tally was closed,
 *  the party/item got linked after the fact, etc). Renders nothing if the
 *  firm has no Tally company linked at all (the overwhelmingly common
 *  case) — only shows up once a push is either done, in flight, failed,
 *  or genuinely one click away.
 *
 *  `queryKey` must be unique per entity (e.g.
 *  `["invoice-tally-push-status", id]` /
 *  `["inward-bill-tally-push-status", id]`) so two panels on different
 *  pages don't share a cache entry. */
export function TallyPushPanel({
  statusUrl,
  pushUrl,
  queryKey,
  autoPushHint = "Ready to sync. This usually happens automatically — use the button if it didn't.",
}: {
  statusUrl: string;
  pushUrl: string;
  queryKey: unknown[];
  /** Shown when pushable but not yet pushed — phrase the "usually
   *  automatic" moment for the caller's own entity (finalize vs. approve). */
  autoPushHint?: string;
}) {
  const qc = useQueryClient();

  const pushStatus = useQuery({
    queryKey,
    queryFn: () => api<TallyPushBlockers>(statusUrl),
  });

  const push = useMutation({
    mutationFn: () => api<TallySyncJob>(pushUrl, { method: "POST" }),
    onSuccess: () => void qc.invalidateQueries({ queryKey }),
  });

  // No Tally company for this firm at all — the overwhelmingly common
  // case. Say nothing; this isn't a feature the firm has opted into.
  if (pushStatus.isError || pushStatus.isLoading) return null;

  const blockers = pushStatus.data?.blockers ?? [];
  const isNoCompany = blockers.some((b) => b.code === "no_tally_company");
  if (isNoCompany) return null;

  const pushable = pushStatus.data?.pushable ?? false;

  return (
    <div className="card p-4">
      <div className="flex items-baseline justify-between">
        <h3 className="font-serif text-sm font-semibold">Tally</h3>
        {pushable && (
          <button
            className="btn-ghost h-8 px-3 text-xs"
            disabled={push.isPending}
            onClick={() => push.mutate()}
          >
            {push.isPending ? "Pushing…" : "Push to Tally"}
          </button>
        )}
      </div>

      {push.isSuccess && (
        <p className="mt-2 text-xs text-[#25a566]">
          Queued — the shop's agent will send this on its next check-in.
        </p>
      )}
      {push.isError && <p className="mt-2 text-xs text-danger">Could not start the push.</p>}

      {!pushable && blockers.length > 0 && (
        <ul className="mt-2 space-y-1 text-xs text-muted">
          {blockers.map((b) => (
            <li key={b.code}>{b.message}</li>
          ))}
        </ul>
      )}

      {pushable && !push.isSuccess && (
        <p className="mt-2 text-xs text-muted">{autoPushHint}</p>
      )}
    </div>
  );
}
