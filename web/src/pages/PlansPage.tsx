import { useEffect, useState } from "react";
import { Link, useSearchParams } from "react-router-dom";
import { useMutation, useQueryClient } from "@tanstack/react-query";
import { ApiError, api } from "../lib/api";
import { useAuth } from "../lib/auth";
import { beacon } from "../lib/beacon";
import { COMPARE_ROWS, PLANS_KEY, featureLabel, rupees, usePlans } from "../lib/plans";
import type { Plan, PlansOut } from "../lib/plans";
import { PageHeader } from "../components/PageHeader";

interface StartOut {
  short_url: string;
  trial_ends_on: string | null;
  amount: number;
  label: string;
}

const message = (e: unknown) => (e instanceof ApiError ? e.message : "Something went wrong. Try again.");
const day = (iso: string) => new Date(iso).toLocaleDateString("en-IN", { day: "numeric", month: "short" });

/** Today plus `n` days, for "charged on ..." before the server has answered. */
function inDays(n: number): string {
  const d = new Date();
  d.setDate(d.getDate() + n);
  return d.toLocaleDateString("en-IN", { day: "numeric", month: "short" });
}

/** Plan and billing: what the firm is on, the plans and their prices (read live from Fleek), and starting or cancelling a plan. */
export function PlansPage() {
  const { refresh } = useAuth();
  const qc = useQueryClient();
  const { data, isLoading, isError, error } = usePlans();
  const [params] = useSearchParams();
  const [starting, setStarting] = useState<Plan | null>(null);
  const [confirmCancel, setConfirmCancel] = useState(false);
  const [note, setNote] = useState<string | null>(null);

  useEffect(() => {
    beacon("plans_viewed");
  }, []);

  // arrived from a locked feature ("Try free for 3 days"): open that plan's sheet straight away
  useEffect(() => {
    const want = params.get("plan");
    if (!data || !want || !data.can_manage || !data.current.free) return;
    const p = data.plans.find((x) => x.plan === want && x.price_amount > 0);
    if (p) setStarting(p);
  }, [data, params]);

  const cancel = useMutation({
    mutationFn: () => api<{ effective: string; ends_on: string | null }>("/plans/cancel", { method: "POST" }),
    onSuccess: async (r) => {
      setConfirmCancel(false);
      setNote(
        r.effective === "now"
          ? "Cancelled. You are back on the Free plan and nothing was charged."
          : `Cancelled. You keep your plan until ${r.ends_on ? day(r.ends_on) : "the end of the period"}, and nothing more will be charged.`,
      );
      await qc.invalidateQueries({ queryKey: PLANS_KEY });
      await refresh();
    },
    onError: (e) => setNote(message(e)),
  });

  if (isLoading) return <div className="text-sm text-muted">Loading…</div>;
  if (isError || !data) {
    return (
      <div className="mx-auto max-w-xl">
        <PageHeader title="Plan and billing" />
        <p className="text-sm text-danger" role="alert">{message(error)}</p>
      </div>
    );
  }
  const cur = data.current;
  const owner = data.can_manage;
  const paidNow = !cur.free && cur.plan_key;

  return (
    <div className="mx-auto max-w-5xl">
      <PageHeader title="Plan and billing" subtitle="Monthly plans. No GST for now. Stop any time." />
      {note && <p className="mb-3 rounded-lg border border-line bg-accent-soft px-3 py-2 text-sm" role="status">{note}</p>}

      <section className="card mb-5 p-5">
        <div className="flex flex-wrap items-center gap-3">
          <h2 className="section-title">{cur.free ? "You are on Free" : cur.plan_label ?? "Your plan"}</h2>
          {cur.status === "trial" && !cur.free && (
            <span className="rounded-full bg-[#f6ecd5] px-2.5 py-0.5 text-xs font-semibold text-[#7a5c14]">
              Free trial · {cur.trial_days_left ?? 0} {cur.trial_days_left === 1 ? "day" : "days"} left
            </span>
          )}
          {cur.status === "active" && paidNow && (
            <span className="rounded-full bg-[#e6efe8] px-2.5 py-0.5 text-xs font-semibold text-[#3a5a48]">Active</span>
          )}
        </div>
        {cur.free && (
          <p className="meta mt-1">
            Invoices, customers, items and payments, for one user.{" "}
            {data.pdf_days_left && data.pdf_days_left > 0
              ? `You can download and share invoice PDFs for ${data.pdf_days_left} more ${data.pdf_days_left === 1 ? "day" : "days"}; after that it needs Starter.`
              : "Downloading and sharing invoice PDFs now needs Starter."}
          </p>
        )}
        {paidNow && cur.status === "trial" && (
          <p className="meta mt-1">
            Nothing has been charged. If you keep it, your payment is taken when the trial ends; cancel before then and you pay nothing. Your invoices and data stay either way.
          </p>
        )}
        {paidNow && owner && (
          <div className="mt-3">
            {!confirmCancel ? (
              <button className="btn-ghost" onClick={() => setConfirmCancel(true)}>
                {cur.status === "trial" ? "Cancel my trial" : "Cancel my plan"}
              </button>
            ) : (
              <div className="flex flex-wrap items-center gap-2 text-sm">
                <span>Cancel now? No questions asked.</span>
                <button className="btn-primary" disabled={cancel.isPending} onClick={() => cancel.mutate()}>
                  {cancel.isPending ? "Cancelling…" : "Yes, cancel"}
                </button>
                <button className="btn-ghost" onClick={() => setConfirmCancel(false)}>Keep it</button>
              </div>
            )}
          </div>
        )}
      </section>

      <div className="grid gap-3 sm:grid-cols-2 lg:grid-cols-4">
        {data.plans.map((p) => (
          <PlanCard key={p.plan} p={p} data={data} onStart={() => setStarting(p)} />
        ))}
      </div>

      <section className="card mt-5 overflow-x-auto p-4">
        <h2 className="section-title mb-2">Compare</h2>
        <table className="w-full text-sm">
          <thead>
            <tr className="text-left text-xs text-muted">
              <th className="py-1.5 pr-2 font-medium">Feature</th>
              {data.plans.map((p) => (
                <th key={p.plan} className="px-2 py-1.5 text-center font-medium">{p.label}</th>
              ))}
            </tr>
          </thead>
          <tbody>
            <tr className="border-t border-line">
              <td className="py-1.5 pr-2">Invoices, customers, items, payments</td>
              {data.plans.map((p) => <td key={p.plan} className="px-2 text-center">✓</td>)}
            </tr>
            {COMPARE_ROWS.map((k) => (
              <tr key={k} className="border-t border-line">
                <td className="py-1.5 pr-2">{featureLabel(k)}</td>
                {data.plans.map((p) => (
                  <td key={p.plan} className="px-2 text-center">
                    {p.unlocks.includes(k) ? "✓" : p.price_amount === 0 && k === "pdf_out" ? "7 days" : "–"}
                  </td>
                ))}
              </tr>
            ))}
            <tr className="border-t border-line">
              <td className="py-1.5 pr-2">Users</td>
              {data.plans.map((p) => <td key={p.plan} className="px-2 text-center">{p.users}</td>)}
            </tr>
          </tbody>
        </table>
      </section>

      {!owner && <p className="meta mt-3">Only the owner can change the plan.</p>}
      {starting && <StartSheet p={starting} onClose={() => setStarting(null)} />}
      <p className="meta mt-4">
        Questions about a plan? <Link to="/setup" className="underline">See what is included</Link>.
      </p>
    </div>
  );
}

function PlanCard({ p, data, onStart }: { p: Plan; data: PlansOut; onStart: () => void }) {
  const here = data.current.plan_key === p.plan || (p.price_amount === 0 && data.current.free);
  const recommended = p.plan.endsWith("growth");
  const canStart = data.can_manage && data.current.free && p.price_amount > 0;
  const label = p.trial_days > 0 ? `Try free for ${p.trial_days} days` : `Choose ${p.label}`;
  return (
    <div className={`card flex flex-col gap-2 p-4 ${here ? "border-accent" : recommended ? "border-ok" : ""}`}>
      <div className="flex items-center gap-2">
        <span className="font-semibold">{p.label}</span>
        {here && <span className="rounded-full bg-accent-soft px-2 text-[11px] font-semibold text-accent">Your plan</span>}
        {recommended && !here && <span className="rounded-full bg-[#e6efe8] px-2 text-[11px] font-semibold text-[#3a5a48]">Most shops</span>}
      </div>
      <div className="font-serif text-2xl font-semibold">
        {p.price_amount === 0 ? "₹0" : rupees(p.price_amount)}
        {p.price_amount > 0 && <span className="meta ml-1 font-sans font-normal">/ month</span>}
      </div>
      <ul className="meta space-y-0.5">
        <li>{p.users} {p.users === 1 ? "user" : "users"}</li>
        {p.unlocks.slice(0, 6).map((k) => (
          <li key={k}>✓ {featureLabel(k as never)}</li>
        ))}
        {p.price_amount === 0 && <li>✓ PDF download for your first 7 days</li>}
      </ul>
      {canStart && (
        <button className="btn-primary mt-auto" onClick={onStart}>
          {label}
        </button>
      )}
    </div>
  );
}

/** The wording before anything is started: strong and plain about what is, and is not, charged. */
function StartSheet({ p, onClose }: { p: Plan; onClose: () => void }) {
  const [err, setErr] = useState<string | null>(null);
  const trial = p.trial_days > 0;
  const start = useMutation({
    mutationFn: () => api<StartOut>("/plans/start", { method: "POST", body: { plan: p.plan } }),
    onSuccess: (r) => {
      window.location.assign(r.short_url); // the hosted page where the owner approves UPI AutoPay or a card
    },
    onError: (e) => setErr(message(e)),
  });
  return (
    <>
      <div className="fixed inset-0 z-40 bg-black/45" onClick={onClose} />
      <div
        role="dialog"
        aria-label={trial ? `Try ${p.label} free` : `Choose ${p.label}`}
        className="fixed inset-x-0 bottom-0 z-50 mx-auto max-w-md rounded-t-2xl bg-card p-5 pb-[calc(1.25rem+env(safe-area-inset-bottom,0px))] shadow-xl md:bottom-auto md:top-16 md:rounded-2xl"
      >
        <h2 className="section-title mb-1">{trial ? `Try ${p.label} free for ${p.trial_days} days` : `Choose ${p.label}`}</h2>
        <ul className="my-3 space-y-3 text-sm">
          {trial ? (
            <>
              <li>
                <b>Nothing is charged today.</b> {rupees(p.price_amount)} is taken on <b>{inDays(p.trial_days)}</b> only if you keep {p.label}, and only
                through the payment you approve.
              </li>
              <li><b>Cancel in one tap</b> from Plan and billing, any time, no questions. Your invoices and data stay.</li>
              <li>You approve UPI AutoPay (or a card) on a secure page. We never see your card.</li>
              {p.unlocks.includes("whatsapp") && <li>We set up WhatsApp with you, about 20 minutes. We will message you to find a time.</li>}
            </>
          ) : (
            <>
              <li>{rupees(p.price_amount)} a month, charged when you approve the payment on the next page.</li>
              <li><b>Cancel any time</b> from Plan and billing. Your invoices and data stay.</li>
              <li>You approve UPI AutoPay (or a card) on a secure page. We never see your card.</li>
            </>
          )}
        </ul>
        {err && <p className="err mb-2" role="alert">{err}</p>}
        <button className="btn-primary w-full" disabled={start.isPending} onClick={() => start.mutate()}>
          {start.isPending ? "Opening the payment page…" : trial ? `Start my ${p.trial_days} days` : "Continue to payment"}
        </button>
        <button className="btn-ghost mt-2 w-full" onClick={onClose}>Not now</button>
      </div>
    </>
  );
}
