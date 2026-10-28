import { useState } from "react";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { api, ApiError } from "../../lib/api";
import type { GroupSuggestions, ItemFilter } from "../../lib/catalog";

/**
 * Step 2 of the review: products with no group, bunched by the group we suggest for them.
 * Accept a whole bunch in one click (renaming it first if you like), or all of them. What had
 * no suggestion gets one group name for all of it. Nothing here runs per row.
 */
export function GroupsReviewPanel({
  catalogId,
  onLook,
  onClose,
}: {
  catalogId: string;
  /** Show these rows in the table instead. */
  onLook: (f: Pick<ItemFilter, "suggested_group" | "no_suggestion">) => void;
  onClose: () => void;
}) {
  const qc = useQueryClient();
  const [names, setNames] = useState<Record<string, string>>({});
  const [leftName, setLeftName] = useState("");
  const [err, setErr] = useState<string | null>(null);
  const [busy, setBusy] = useState<string | null>(null);

  const q = useQuery({
    queryKey: ["catalog-group-suggestions", catalogId],
    queryFn: () => api<GroupSuggestions>(`/supplier-catalogs/${catalogId}/group-suggestions`),
  });
  const d = q.data;

  const refresh = () => {
    qc.invalidateQueries({ queryKey: ["catalog-group-suggestions", catalogId] });
    qc.invalidateQueries({ queryKey: ["catalog-items", catalogId] });
    qc.invalidateQueries({ queryKey: ["catalog-groups", catalogId] });
    qc.invalidateQueries({ queryKey: ["supplier-catalog", catalogId] });
    qc.invalidateQueries({ queryKey: ["catalog-queues", catalogId] });
    qc.invalidateQueries({ queryKey: ["item-categories"] });
  };

  const give = (filter: ItemFilter, group: string) =>
    api<{ updated: number }>(`/supplier-catalogs/${catalogId}/items/bulk`, {
      method: "PATCH",
      body: { filter: { ...filter, no_group: true }, changes: { group_name: group } },
    });

  const run = async (key: string, jobs: Array<() => Promise<unknown>>) => {
    setBusy(key);
    setErr(null);
    try {
      for (const job of jobs) await job();
    } catch (e) {
      setErr(e instanceof ApiError ? e.message : "That did not work. Try again.");
    } finally {
      setBusy(null);
      refresh();
    }
  };

  const nameOf = (c: { name: string }) => (names[c.name] ?? c.name).trim() || c.name;
  const accept = (c: GroupSuggestions["clusters"][number]) =>
    run(c.name, [() => give({ suggested_group: c.name }, nameOf(c))]);
  const acceptAll = () =>
    d && run("all", d.clusters.map((c) => () => give({ suggested_group: c.name }, nameOf(c))));
  const leftover = useMutation({
    mutationFn: () => give({ no_suggestion: true }, leftName.trim()),
    onSuccess: () => {
      setErr(null);
      setLeftName("");
      refresh();
    },
    onError: (e) => setErr(e instanceof ApiError ? e.message : "That did not work. Try again."),
  });

  const inClusters = d ? d.clusters.reduce((n, c) => n + c.count, 0) : 0;
  const finished = d !== undefined && d.ungrouped === 0;

  return (
    <div className="border-t border-line" role="region" aria-label="Groups">
      <div className="flex flex-wrap items-center justify-between gap-3 p-4">
        <div>
          <p className="font-medium">
            {q.isLoading
              ? "Looking at the products…"
              : finished
                ? "Every product has a group."
                : `${d?.ungrouped ?? 0} products have no group`}
          </p>
          {!finished && (
            <p className="text-xs text-muted">
              Sorted by name into suggested groups. Accept a whole group in one click; rename it first
              if you like.
            </p>
          )}
        </div>
        <div className="flex gap-2">
          {d && d.clusters.length > 0 && (
            <button
              type="button"
              className="btn-primary"
              disabled={busy !== null}
              onClick={() => void acceptAll()}
            >
              {busy === "all" ? "Grouping…" : `Accept all suggestions · ${inClusters}`}
            </button>
          )}
          <button type="button" className="btn-ghost" onClick={onClose}>
            Close
          </button>
        </div>
      </div>

      {err && (
        <p className="err px-4 pb-2" role="alert">
          {err}
        </p>
      )}
      {q.isError && <p className="err px-4 pb-2">Could not load the suggestions. Try again.</p>}

      {d && d.clusters.length > 0 && (
        <ul className="border-t border-line">
          {d.clusters.map((c) => (
            <li
              key={c.name}
              className="grid grid-cols-[minmax(0,1fr)_auto] items-center gap-x-4 gap-y-2 border-b border-line px-4 py-2.5 sm:grid-cols-[minmax(12rem,1fr)_4rem_9rem_auto]"
            >
              <input
                className="field h-9"
                aria-label={`Group name for ${c.count} products`}
                value={names[c.name] ?? c.name}
                disabled={busy !== null}
                onChange={(e) => setNames((n) => ({ ...n, [c.name]: e.target.value }))}
              />
              <span className="text-right text-sm font-medium tabular-nums">{c.count}</span>
              <span className="hidden gap-1 sm:flex" aria-hidden>
                {c.sample_urls.map((u) => (
                  <img
                    key={u}
                    src={u}
                    alt=""
                    loading="lazy"
                    className="h-[30px] w-11 rounded border border-line object-cover"
                  />
                ))}
              </span>
              <span className="col-span-2 flex items-center gap-3 sm:col-span-1 sm:justify-end">
                <button
                  type="button"
                  className="text-sm text-accent hover:underline"
                  onClick={() => onLook({ suggested_group: c.name })}
                >
                  Look through
                </button>
                <button
                  type="button"
                  className="btn-primary h-9"
                  disabled={busy !== null}
                  onClick={() => void accept(c)}
                >
                  {busy === c.name ? "Grouping…" : `Accept ${c.count}`}
                </button>
              </span>
            </li>
          ))}
        </ul>
      )}

      {d && d.unsuggested > 0 && (
        <div className="flex flex-wrap items-center justify-between gap-3 px-4 py-3">
          <div>
            <p className="text-sm font-medium">{d.unsuggested} have no suggestion</p>
            <p className="text-xs text-muted">Give them all one group, or look through them.</p>
          </div>
          <div className="flex flex-wrap items-center gap-2">
            <input
              className="field h-9 w-52"
              list="catalog-groups-list"
              placeholder={`Group for all ${d.unsuggested}`}
              aria-label="Group for the products with no suggestion"
              value={leftName}
              onChange={(e) => setLeftName(e.target.value)}
            />
            <button
              type="button"
              className="btn-ghost h-9"
              disabled={!leftName.trim() || leftover.isPending || busy !== null}
              onClick={() => leftover.mutate()}
            >
              {leftover.isPending ? "Grouping…" : `Apply to ${d.unsuggested}`}
            </button>
            <button
              type="button"
              className="text-sm text-accent hover:underline"
              onClick={() => onLook({ no_suggestion: true })}
            >
              Look through them
            </button>
          </div>
        </div>
      )}
    </div>
  );
}
