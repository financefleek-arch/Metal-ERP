import { useState } from "react";
import { Link } from "react-router-dom";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { api, ApiError } from "../lib/api";
import { inr } from "../lib/previewTotal";
import type { Reminder, SendRemindersResult, Tenant } from "../lib/types";

/** Collections: who is due a payment reminder today. One row per customer; nothing goes out until
 *  the shop presses Send (unless the firm chose automatic sending, which only covers bills that
 *  have just come due). */
export function RemindersPanel() {
  const qc = useQueryClient();
  const [unticked, setUnticked] = useState<Set<string>>(new Set());
  const [note, setNote] = useState<string | null>(null);
  const [err, setErr] = useState<string | null>(null);

  const list = useQuery({ queryKey: ["reminders"], queryFn: () => api<Reminder[]>("/reminders") });
  const tenant = useQuery({ queryKey: ["tenant"], queryFn: () => api<Tenant>("/tenant") });

  const rows = list.data ?? [];
  const chosen = rows.filter((r) => r.phone && !unticked.has(r.id));
  const auto = !!tenant.data?.reminder_auto_send && !!tenant.data?.reminder_auto_allowed;

  const refresh = () => qc.invalidateQueries({ queryKey: ["reminders"] });
  const fail = (e: unknown) => setErr(e instanceof ApiError ? e.message : "That did not work. Try again.");

  const run = useMutation({
    mutationFn: () => api<{ proposed: number }>("/reminders/run", { method: "POST" }),
    onSuccess: (r) => {
      setErr(null);
      setNote(r.proposed ? `${r.proposed} new reminder${r.proposed === 1 ? "" : "s"} found.` : "Nothing new is due.");
      refresh();
    },
    onError: fail,
  });
  const send = useMutation({
    mutationFn: (ids: string[]) =>
      api<SendRemindersResult>("/reminders/send", { method: "POST", body: { ids } }),
    onSuccess: (r) => {
      setErr(null);
      const parts = [`Sent ${r.sent}`];
      if (r.failed) parts.push(`${r.failed} failed (see below)`);
      if (r.skipped) parts.push(`${r.skipped} already paid, skipped`);
      setNote(parts.join(", ") + ".");
      setUnticked(new Set());
      refresh();
    },
    onError: fail,
  });
  const skip = useMutation({
    mutationFn: (ids: string[]) => api("/reminders/skip", { method: "POST", body: { ids } }),
    onSuccess: () => {
      setErr(null);
      setNote(null);
      refresh();
    },
    onError: fail,
  });
  const busy = run.isPending || send.isPending || skip.isPending;

  function toggle(id: string) {
    setUnticked((s) => {
      const n = new Set(s);
      if (n.has(id)) n.delete(id);
      else n.add(id);
      return n;
    });
  }

  return (
    <section className="card p-0" aria-label="Reminders due">
      <div className="flex flex-wrap items-center justify-between gap-2 border-b border-line px-4 py-3">
        <div className="min-w-0">
          <h2 className="font-serif text-base font-semibold">
            Reminders due
            {rows.length > 0 && (
              <span className="ml-2 rounded-full bg-[#efe9df] px-2 py-0.5 align-middle text-[11px] font-medium text-muted">
                {rows.length}
              </span>
            )}
          </h2>
          <p className="text-xs text-muted">
            {auto
              ? "Automatic sending is on: reminders that have just come due go out by themselves. Older ones wait here for you."
              : "Nothing is sent until you press Send. Reminders go out from your WhatsApp number."}
          </p>
        </div>
        <button className="btn-ghost h-8 px-3 text-xs" disabled={busy} onClick={() => run.mutate()}>
          {run.isPending ? "Looking…" : "Find reminders now"}
        </button>
      </div>

      {list.isLoading && <p className="px-4 py-3 text-xs text-muted">Loading…</p>}
      {!list.isLoading && rows.length === 0 && (
        <p className="px-4 py-3 text-xs text-muted">No reminders are due right now.</p>
      )}

      <ul className="divide-y divide-line">
        {rows.map((r) => {
          const noPhone = !r.phone;
          const bills = r.invoice_numbers.map((n) => String(n).padStart(4, "0"));
          return (
            <li key={r.id} className="flex items-start gap-3 px-4 py-3">
              <input
                type="checkbox"
                className="mt-1 h-4 w-4 accent-accent"
                aria-label={`Send to ${r.party_name}`}
                disabled={noPhone || busy}
                checked={!noPhone && !unticked.has(r.id)}
                onChange={() => toggle(r.id)}
              />
              <div className="min-w-0 flex-1">
                <div className="flex flex-wrap items-baseline justify-between gap-x-3">
                  <span className="truncate text-sm font-semibold">{r.party_name}</span>
                  <span className="font-mono text-sm tabular-nums">{inr(r.amount)}</span>
                </div>
                <div className="mt-0.5 flex flex-wrap items-center gap-x-2 gap-y-1 text-xs text-muted">
                  <span
                    className={`rounded-full px-2 py-0.5 text-[10px] font-semibold ${
                      r.kind === "statement" ? "bg-[#e3edf2] text-accent" : "bg-[#efe9df]"
                    }`}
                  >
                    {r.kind === "statement" ? "Account statement" : "Payment reminder"}
                  </span>
                  <span>
                    {bills.length === 1 ? "Bill" : "Bills"} {bills.join(", ")}
                  </span>
                  <span className="font-semibold text-danger">{r.oldest_overdue_days} days overdue</span>
                  {r.phone && <span>{r.phone}</span>}
                  {noPhone && (
                    <span className="rounded-full bg-[#f4e3df] px-2 py-0.5 text-[10px] font-semibold text-danger">
                      No phone number
                    </span>
                  )}
                </div>
                {r.status === "failed" && r.error && (
                  <p className="mt-1 text-xs text-danger" role="alert">
                    Not sent: {r.error}
                  </p>
                )}
              </div>
              {noPhone ? (
                <Link to={`/parties/${r.party_id}`} className="btn-ghost h-8 shrink-0 px-3 text-xs leading-8">
                  Add number
                </Link>
              ) : (
                <button className="btn-ghost h-8 shrink-0 px-3 text-xs" disabled={busy} onClick={() => skip.mutate([r.id])}>
                  Skip
                </button>
              )}
            </li>
          );
        })}
      </ul>

      {rows.length > 0 && (
        <div className="flex flex-wrap items-center justify-between gap-2 border-t border-line px-4 py-3">
          <span className="text-xs text-muted">{chosen.length} selected</span>
          <div className="flex gap-2">
            <button
              className="btn-ghost h-9 px-3 text-xs"
              disabled={busy || chosen.length === 0}
              onClick={() => skip.mutate(chosen.map((r) => r.id))}
            >
              Skip selected
            </button>
            <button
              className="btn-primary h-9 px-4 text-xs"
              disabled={busy || chosen.length === 0}
              onClick={() => send.mutate(chosen.slice(0, 25).map((r) => r.id))}
            >
              {send.isPending
                ? "Sending…"
                : `Send ${Math.min(chosen.length, 25)} reminder${chosen.length === 1 ? "" : "s"}`}
            </button>
          </div>
        </div>
      )}
      {chosen.length > 25 && (
        <p className="px-4 pb-3 text-xs text-muted">
          25 go out at a time. Press Send again for the rest.
        </p>
      )}
      {note && !err && <p className="px-4 pb-3 text-xs text-ok">{note}</p>}
      {err && (
        <p className="err px-4 pb-3" role="alert">
          {err}
        </p>
      )}
    </section>
  );
}
