import { useEffect, useMemo, useState } from "react";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { api, ApiError } from "../../lib/api";
import type {
  CatalogSelection,
  PreflightGroup,
  TallyCheck,
  TallyPreflight,
  TallyRun,
  TallySettings,
} from "../../lib/catalog";

const errMsg = (e: unknown, fallback: string) => (e instanceof ApiError ? e.message : fallback);

/**
 * Send the chosen items to Tally as stock items.
 *
 * Everything that could go wrong is checked first (preflight) and shown as a list, each with
 * the action that fixes it. Nothing is sent until every blocking check passes.
 */
export function TallyDialog({
  selection,
  count,
  onClose,
}: {
  selection: CatalogSelection;
  count: number;
  onClose: () => void;
}) {
  const qc = useQueryClient();
  const [includeSynced, setIncludeSynced] = useState(false);
  const [skipExisting, setSkipExisting] = useState(false);
  const [err, setErr] = useState<string | null>(null);
  const [started, setStarted] = useState(false);
  const [rootDraft, setRootDraft] = useState<string | null>(null);

  const selReq = useMemo(() => ({ selection }), [selection]);

  useEffect(() => {
    const onKey = (e: KeyboardEvent) => {
      if (e.key === "Escape") onClose();
    };
    window.addEventListener("keydown", onKey);
    return () => window.removeEventListener("keydown", onKey);
  }, [onClose]);

  // --- settings (group root, new-group policy, mapping) ---
  const settings = useQuery({
    queryKey: ["tally-settings"],
    queryFn: () => api<TallySettings>("/supplier-catalogs/tally/settings"),
  });

  // --- the checklist ---
  const preKey = ["tally-preflight", JSON.stringify(selReq), includeSynced, skipExisting] as const;
  const pre = useQuery({
    queryKey: preKey,
    queryFn: () =>
      api<TallyPreflight>("/item-tally/preflight", {
        method: "POST",
        body: { ...selReq, include_synced: includeSynced, skip_existing: skipExisting },
      }),
    enabled: !started,
  });
  const refresh = () => {
    qc.invalidateQueries({ queryKey: ["tally-preflight"] });
    qc.invalidateQueries({ queryKey: ["tally-settings"] });
  };

  // --- "check Tally" ---
  const check = useQuery({
    queryKey: ["tally-check"],
    queryFn: () => api<TallyCheck | null>("/supplier-catalogs/tally/check"),
    refetchInterval: (q) => {
      const s = q.state.data?.status;
      return s === "queued" || s === "sent" || s === "running" ? 1500 : false;
    },
  });
  const checking = ["queued", "sent", "running"].includes(check.data?.status ?? "");
  const checkStatus = check.data?.status;
  useEffect(() => {
    if (checkStatus === "ok") refresh();
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [checkStatus, check.data?.id]);
  const startCheck = useMutation({
    mutationFn: () =>
      api<TallyCheck>("/supplier-catalogs/tally/check", { method: "POST" }),
    onSuccess: (job) => {
      setErr(null);
      qc.setQueryData(["tally-check"], job);
    },
    onError: (e) => setErr(errMsg(e, "Could not ask Tally. Try again.")),
  });

  // --- settings changes ---
  const saveSettings = useMutation({
    mutationFn: (body: Record<string, unknown>) =>
      api<TallySettings>("/supplier-catalogs/tally/settings", { method: "PUT", body }),
    onSuccess: (s) => {
      setErr(null);
      qc.setQueryData(["tally-settings"], s);
      qc.invalidateQueries({ queryKey: ["tally-preflight"] });
    },
    onError: (e) => setErr(errMsg(e, "Could not save that. Try again.")),
  });

  // --- the run ---
  const push = useMutation({
    mutationFn: () =>
      api<TallyRun>("/item-tally/push", {
        method: "POST",
        body: { ...selReq, include_synced: includeSynced, skip_existing: skipExisting },
      }),
    onSuccess: (r) => {
      setErr(null);
      setStarted(true);
      qc.setQueryData(["tally-run"], r);
    },
    onError: (e) => setErr(errMsg(e, "Could not start. Try again.")),
  });
  const run = useQuery({
    queryKey: ["tally-run"],
    queryFn: () => api<TallyRun>("/item-tally/run"),
    enabled: started,
    refetchInterval: (q) => (q.state.data?.state === "running" ? 1500 : false),
  });
  const runState = run.data?.state;
  useEffect(() => {
    if (runState && runState !== "running") qc.invalidateQueries({ queryKey: ["items"] });
  }, [runState, qc]);

  const p = pre.data;
  const failing = p?.checks.filter((c) => !c.ok && c.blocking) ?? [];
  const failed = (code: string) => failing.some((c) => c.code === code);
  const s = settings.data;
  const busy = push.isPending || startCheck.isPending || checking;

  return (
    <div
      className="fixed inset-0 z-50 grid place-items-center overflow-y-auto bg-black/40 p-4"
      onMouseDown={(e) => {
        if (e.target === e.currentTarget) onClose();
      }}
    >
      <div
        role="dialog"
        aria-modal="true"
        aria-labelledby="tally-title"
        className="card w-full max-w-2xl bg-card p-5 shadow-xl"
      >
        <div className="flex items-start justify-between gap-3">
          <div>
            <h2 id="tally-title" className="font-serif text-xl font-semibold">
              Send to Tally
            </h2>
            <p className="mt-1 text-sm text-muted">
              {count.toLocaleString("en-IN")} chosen item{count === 1 ? "" : "s"} go into Tally as stock
              items, with their code as the part number and your selling price.
            </p>
          </div>
          <button type="button" className="btn-ghost h-9 px-3" onClick={onClose}>
            Close
          </button>
        </div>

        {started ? (
          <RunPanel run={run.data} onDone={onClose} />
        ) : (
          <>
            <section className="mt-4" aria-label="Checks">
              <h3 className="label">Before sending</h3>
              {pre.isLoading && <p className="text-sm text-muted">Checking…</p>}
              {pre.isError && <p className="err">{errMsg(pre.error, "Could not check.")}</p>}
              {p && (
                <ul className="space-y-1.5 text-sm">
                  {p.checks
                    .filter((c) => c.blocking)
                    .map((c) => (
                      <li key={c.code} className="flex items-start gap-2">
                        <span aria-hidden className={c.ok ? "text-ok" : "text-danger"}>
                          {c.ok ? "✓" : "✗"}
                        </span>
                        <span className={c.ok ? "text-muted" : ""}>{c.message}</span>
                      </li>
                    ))}
                </ul>
              )}
            </section>

            {err && <p className="err mt-3" role="alert">{err}</p>}

            {/* the fix for each failing check */}
            <div className="mt-3 flex flex-wrap gap-2">
              {(failed("check_needed") || checkStatus === "error") && !failed("no_company") && (
                <button type="button" className="btn-primary" disabled={busy || failed("agent")} onClick={() => startCheck.mutate()}>
                  {checking ? "Reading Tally…" : "Check Tally now"}
                </button>
              )}
              {!failed("check_needed") && !failed("no_company") && (
                <button type="button" className="btn-ghost" disabled={busy} onClick={() => startCheck.mutate()}>
                  {checking ? "Reading Tally…" : "Check again"}
                </button>
              )}
            </div>
            {checkStatus === "error" && check.data?.error && (
              <p className="err mt-2">{check.data.error}</p>
            )}

            {(failed("name_collision") || skipExisting) && (
              <label className="mt-3 flex items-start gap-2 text-sm">
                <input
                  type="checkbox"
                  className="mt-1"
                  checked={skipExisting}
                  onChange={(e) => setSkipExisting(e.target.checked)}
                />
                <span>
                  Leave out items that already exist in Tally
                  {skipExisting && p ? ` (${p.skipped_existing} left out, nothing overwritten)` : ""}
                </span>
              </label>
            )}

            {p && p.collisions.length > 0 && (
              <div className="mt-3 rounded-md border border-line bg-ground p-3 text-sm">
                <p className="font-medium">Already in Tally under the same name</p>
                <ul className="mt-1 list-disc pl-5 text-xs text-muted">
                  {p.collisions.slice(0, 8).map((c) => (
                    <li key={c.item_id}>
                      {c.name} <span className="font-mono">({c.code})</span>
                    </li>
                  ))}
                </ul>
                {p.collisions.length > 8 && (
                  <details className="mt-1 text-xs text-muted">
                    <summary className="cursor-pointer">
                      Show all {p.collisions.length} (the other {p.collisions.length - 8})
                    </summary>
                    <ul className="mt-1 max-h-60 list-disc overflow-y-auto pl-5">
                      {p.collisions.slice(8).map((c) => (
                        <li key={c.item_id}>
                          {c.name} <span className="font-mono">({c.code})</span>
                        </li>
                      ))}
                    </ul>
                  </details>
                )}
              </div>
            )}

            {p && p.groups.length > 0 && s?.connected && (
              <GroupTable
                groups={p.groups}
                settings={s}
                disabled={saveSettings.isPending}
                onMap={(cid, name) => saveSettings.mutate({ stock_group_map: { [cid]: name } })}
              />
            )}

            {s?.connected && (
              <details className="mt-4 text-sm">
                <summary className="cursor-pointer text-muted">Group settings</summary>
                <div className="mt-3 grid gap-3 sm:grid-cols-2">
                  <div>
                    <label className="label" htmlFor="tally-root">
                      New groups go under
                    </label>
                    <input
                      id="tally-root"
                      className="field"
                      placeholder="Primary"
                      value={rootDraft ?? s.stock_group_root ?? ""}
                      onChange={(e) => setRootDraft(e.target.value)}
                      onBlur={() => {
                        if (rootDraft !== null && rootDraft.trim() !== (s.stock_group_root ?? ""))
                          saveSettings.mutate({ stock_group_root: rootDraft.trim() });
                        setRootDraft(null);
                      }}
                      list="tally-known-groups"
                    />
                    <p className="mt-1 text-xs text-muted">
                      A stock group in Tally. Leave blank for Primary. Items with no group go
                      straight under it.
                    </p>
                  </div>
                  <div>
                    <label className="label" htmlFor="tally-policy">
                      Groups Tally does not have
                    </label>
                    <select
                      id="tally-policy"
                      className="field"
                      value={s.tally_group_create_policy}
                      onChange={(e) => saveSettings.mutate({ tally_group_create_policy: e.target.value })}
                    >
                      <option value="create_missing">Create them</option>
                      <option value="existing_only">Never create, I map each one</option>
                    </select>
                  </div>
                </div>
              </details>
            )}
            <datalist id="tally-known-groups">
              {(s?.known_groups ?? []).map((g) => (
                <option key={g} value={g} />
              ))}
            </datalist>

            {p && p.already_synced > 0 && (
              <label className="mt-3 flex items-center gap-2 text-sm">
                <input type="checkbox" checked={includeSynced} onChange={(e) => setIncludeSynced(e.target.checked)} />
                Send the {p.already_synced} already in Tally again (updates them)
                {p.price_updates > 0 && ` · ${p.price_updates} of them are updated anyway, their price changed`}
              </label>
            )}

            <div className="mt-5 flex items-center justify-between gap-3">
              <p className="text-sm text-muted">
                {p && p.to_push > 0
                  ? `${p.to_push} items in ${p.batches} batch${p.batches === 1 ? "" : "es"}`
                  : count === 0
                    ? "Nothing selected"
                    : ""}
              </p>
              <button
                type="button"
                className="btn-primary"
                disabled={!p?.ok || busy}
                onClick={() => push.mutate()}
              >
                {push.isPending ? "Starting…" : `Send ${p?.to_push ?? ""} to Tally`}
              </button>
            </div>
          </>
        )}
      </div>
    </div>
  );
}

function GroupTable({
  groups,
  settings,
  disabled,
  onMap,
}: {
  groups: PreflightGroup[];
  settings: TallySettings;
  disabled: boolean;
  onMap: (categoryId: string, tallyName: string) => void;
}) {
  const [draft, setDraft] = useState<Record<string, string>>({});
  return (
    <section className="mt-4" aria-label="Groups in Tally">
      <h3 className="label">Groups in Tally</h3>
      <div className="max-h-52 overflow-y-auto rounded-md border border-line">
        <table className="w-full text-sm">
          <thead>
            <tr className="border-b border-line text-left text-[10px] uppercase tracking-wide text-muted">
              <th className="px-3 py-1.5 font-medium">Your group</th>
              <th className="px-3 py-1.5 font-medium">Tally stock group</th>
              <th className="px-3 py-1.5 text-right font-medium">Items</th>
            </tr>
          </thead>
          <tbody>
            {groups.map((g) => (
              <tr key={g.category_id ?? "none"} className="border-b border-line last:border-0">
                <td className="px-3 py-1.5">{g.our_name}</td>
                <td className="px-3 py-1.5">
                  {g.category_id ? (
                    <span className="flex items-center gap-2">
                      <input
                        aria-label={`Tally group for ${g.our_name}`}
                        className="field h-8 flex-1 py-0"
                        list="tally-known-groups"
                        disabled={disabled}
                        value={draft[g.category_id] ?? g.tally_name}
                        onChange={(e) => setDraft((d) => ({ ...d, [g.category_id!]: e.target.value }))}
                        onBlur={() => {
                          const v = (draft[g.category_id!] ?? g.tally_name).trim();
                          if (v && v !== g.tally_name) onMap(g.category_id!, v);
                          setDraft((d) => {
                            const { [g.category_id!]: _drop, ...rest } = d;
                            return rest;
                          });
                        }}
                      />
                      <span
                        className={`whitespace-nowrap rounded-full px-2 py-0.5 text-[10px] font-medium ${
                          g.status === "existing"
                            ? "bg-[#e6efe8] text-ok"
                            : g.status === "create"
                              ? "bg-accent-soft text-accent"
                              : "bg-[#f4e3df] text-danger"
                        }`}
                      >
                        {g.status === "existing" ? "in Tally" : g.status === "create" ? "will be created" : "not in Tally"}
                      </span>
                    </span>
                  ) : (
                    <span className="text-muted">{g.tally_name}</span>
                  )}
                </td>
                <td className="px-3 py-1.5 text-right tabular-nums">{g.item_count}</td>
              </tr>
            ))}
          </tbody>
        </table>
      </div>
      <p className="mt-1 text-xs text-muted">
        Groups Tally already has are never changed.
        {settings.checked_at ? "" : " Check Tally to see which exist."}
      </p>
    </section>
  );
}

function minutes(seconds: number): string {
  if (seconds < 90) return "about a minute";
  return `about ${Math.round(seconds / 60)} minutes`;
}

function RunPanel({ run, onDone }: { run: TallyRun | undefined; onDone: () => void }) {
  // a clock that ticks, so "waiting" shows how long it has really been
  const [now, setNow] = useState(() => Date.now());
  useEffect(() => {
    const h = window.setInterval(() => setNow(Date.now()), 1000);
    return () => window.clearInterval(h);
  }, []);
  if (!run) return <p className="mt-4 text-sm text-muted">Starting…</p>;
  const pct = run.total_items ? Math.round((run.synced / run.total_items) * 100) : 0;
  const since = run.updated_at ? Math.max(0, Math.round((now - Date.parse(run.updated_at + (run.updated_at.endsWith("Z") ? "" : "Z"))) / 1000)) : 0;
  return (
    <section className="mt-5" aria-live="polite" aria-label="Progress">
      {run.state === "running" && (
        <>
          <p className="font-medium">
            {run.waiting
              ? "Waiting for TallyPrime… "
              : run.phase === "waiting_agent"
                ? "Waiting for the shop's agent… "
                : "Sending to Tally… "}
            batch {Math.min(run.batches_done + 1, run.batches)} of {run.batches}
          </p>
          {run.phase === "waiting_agent" && !run.waiting && (
            <p className={`mt-1 text-sm ${since > 180 ? "text-warn" : "text-muted"}`}>
              The agent on the shop PC picks up work about once a minute
              {since > 5 ? ` (waiting ${since < 90 ? `${since}s` : `${Math.round(since / 60)} min`})` : ""}.
              {since > 180 && " This is taking longer than usual: check that the shop PC is on and the agent is running."}
            </p>
          )}
          {run.waiting && (
            <p className="mt-1 text-sm text-warn">
              {run.waiting === "no_company_loaded"
                ? "TallyPrime is open but has no company loaded. Open your company and the send carries on by itself."
                : "TallyPrime is not reachable. Open TallyPrime with your company and the send carries on by itself."}{" "}
              If it stays unavailable the send is cancelled after 10 minutes and you can send again.
            </p>
          )}
          <div className="mt-2 h-2 overflow-hidden rounded bg-line" role="progressbar" aria-valuenow={pct} aria-valuemin={0} aria-valuemax={100}>
            <div className="h-full bg-accent transition-all" style={{ width: `${pct}%` }} />
          </div>
          <p className="mt-1 text-sm text-muted">
            {run.synced} of {run.total_items} items are in Tally.
            {run.eta_seconds != null && run.batches - run.batches_done > 0
              ? ` About ${minutes(run.eta_seconds).replace("about ", "")} left.`
              : ""}{" "}
            {run.waiting ? "" : "Keep TallyPrime open. "}You can close this window.
          </p>
        </>
      )}
      {run.state === "done" && (
        <>
          <p className="font-medium text-ok">
            Done. {run.synced} items are in Tally.
          </p>
          <p className="mt-1 text-sm text-muted">
            Their Tally ids are read back in a moment, so they can be used on sales invoices.
          </p>
          <button type="button" className="btn-primary mt-4" onClick={onDone}>
            Close
          </button>
        </>
      )}
      {(run.state === "error" || run.state === "stopped") && (
        <>
          <p className="font-medium text-danger">
            Stopped after {run.synced} of {run.total_items} items.
          </p>
          {run.error && <p className="err mt-1">{run.error}</p>}
          <p className="mt-2 text-sm text-muted">
            What was sent stays in Tally. Fix the problem and send again: only the items not yet in
            Tally are sent.
          </p>
          <button type="button" className="btn-primary mt-4" onClick={onDone}>
            Close
          </button>
        </>
      )}
    </section>
  );
}
