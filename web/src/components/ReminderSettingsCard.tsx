import { useEffect, useState } from "react";
import { useMutation, useQueryClient } from "@tanstack/react-query";
import { api, ApiError } from "../lib/api";
import type { Tenant } from "../lib/types";

/** Firm page: payment reminders. The daily list is built for you; sending is yours unless you
 *  choose automatic, which only covers bills that have just come due. */
export function ReminderSettingsCard({ tenant }: { tenant: Tenant | undefined }) {
  const qc = useQueryClient();
  const [on, setOn] = useState(false);
  const [auto, setAuto] = useState(false);
  const [days, setDays] = useState("7,15,30");
  const [credit, setCredit] = useState("0");
  const [saved, setSaved] = useState(false);
  const [err, setErr] = useState<string | null>(null);

  useEffect(() => {
    if (!tenant) return;
    setOn(tenant.reminder_enabled);
    setAuto(tenant.reminder_auto_send);
    setDays(tenant.reminder_days);
    setCredit(String(tenant.default_credit_days ?? 0));
  }, [tenant]);

  const allowed = tenant?.reminder_auto_allowed !== false;
  const daysOk = /^\s*\d{1,3}(\s*,\s*\d{1,3}){0,4}\s*$/.test(days);
  const creditOk = /^\d{1,3}$/.test(credit.trim()) && Number(credit) <= 365;
  const first = daysOk ? Math.min(...days.split(",").map((d) => Number(d.trim()))) : null;
  const dirty =
    !!tenant &&
    (on !== tenant.reminder_enabled ||
      auto !== tenant.reminder_auto_send ||
      days.replace(/\s/g, "") !== tenant.reminder_days ||
      credit.trim() !== String(tenant.default_credit_days ?? 0));

  const save = useMutation({
    mutationFn: () =>
      api<Tenant>("/tenant", {
        method: "PATCH",
        body: {
          reminder_enabled: on,
          reminder_auto_send: on && auto && allowed,
          reminder_days: days.replace(/\s/g, ""),
          default_credit_days: Number(credit),
        },
      }),
    onSuccess: (t) => {
      setErr(null);
      setSaved(true);
      window.setTimeout(() => setSaved(false), 1800);
      qc.setQueryData(["tenant"], t);
      qc.invalidateQueries({ queryKey: ["tenant"] });
      qc.invalidateQueries({ queryKey: ["collections-ageing"] });
    },
    onError: (e) => setErr(e instanceof ApiError ? e.message : "Could not save. Try again."),
  });

  return (
    <section className="card mt-5 max-w-2xl bg-card p-4" aria-label="Payment reminders">
      <h3 className="font-serif text-base font-semibold">Payment reminders</h3>
      <p className="mt-1 text-xs text-muted">
        Each morning the system lists customers whose bills are overdue. You approve the list; nothing is sent on
        its own unless you choose automatic below.
      </p>

      <label className="mt-3 flex items-center gap-2 text-sm">
        <input type="checkbox" className="h-4 w-4 accent-accent" checked={on} onChange={(e) => setOn(e.target.checked)} />
        Find overdue bills every morning
      </label>

      <div className="mt-3 flex flex-wrap items-end gap-4">
        <div>
          <label className="label" htmlFor="rm-credit">
            Credit days
          </label>
          <input
            id="rm-credit"
            className="field w-24"
            inputMode="numeric"
            value={credit}
            onChange={(e) => setCredit(e.target.value)}
          />
        </div>
        <div>
          <label className="label" htmlFor="rm-days">
            Remind after (days overdue)
          </label>
          <input id="rm-days" className="field w-40" value={days} onChange={(e) => setDays(e.target.value)} />
        </div>
        {first !== null && creditOk && (
          <p className="max-w-xs text-xs text-muted">
            A bill is overdue {Number(credit)} days after its date, so the first reminder goes out on day{" "}
            {Number(credit) + first}.
          </p>
        )}
      </div>

      <fieldset className="mt-4 border-t border-line pt-3" disabled={!on}>
        <legend className="label">When they are found</legend>
        <label className="flex items-center gap-2 text-sm">
          <input type="radio" name="rm-mode" className="accent-accent" checked={!auto} onChange={() => setAuto(false)} />
          <span>
            <b>I review the list first</b> <span className="text-xs text-muted">(recommended)</span>
          </span>
        </label>
        <label className={`mt-1.5 flex items-center gap-2 text-sm ${allowed ? "" : "opacity-50"}`}>
          <input
            type="radio"
            name="rm-mode"
            className="accent-accent"
            disabled={!allowed}
            checked={auto && allowed}
            onChange={() => setAuto(true)}
          />
          <b>Send automatically, no approval</b>
        </label>
        {!allowed && (
          <p className="mt-1 text-xs text-muted">Automatic sending is not switched on for your firm. Please contact Fleek.</p>
        )}
        {auto && allowed && (
          <p className="mt-2 rounded-md border border-[#e8d9bd] bg-[#faf4ec] px-3 py-2 text-xs text-[#6d4f16]">
            A reminder goes out on its own, between 9am and 7pm, as soon as a bill reaches one of the days above.{" "}
            <b>Bills that were already overdue for longer are not sent automatically</b>; they wait on your list, so
            switching this on never messages everyone at once. At most 40 a day.
          </p>
        )}
      </fieldset>

      {!daysOk && (
        <p className="err" role="alert">
          Enter up to 5 day counts, like 7,15,30.
        </p>
      )}
      {!creditOk && (
        <p className="err" role="alert">
          Credit days must be a number from 0 to 365.
        </p>
      )}
      {err && (
        <p className="err" role="alert">
          {err}
        </p>
      )}
      <div className="mt-4">
        <button className="btn-primary" disabled={!dirty || !daysOk || !creditOk || save.isPending} onClick={() => save.mutate()}>
          {save.isPending ? "Saving…" : saved ? "Saved" : "Save"}
        </button>
      </div>
    </section>
  );
}
