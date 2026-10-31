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

// Opening the payment page changes nothing on the firm: it stays on Free until the autopay is approved. So this page remembers, in this browser, that
// the owner went to pay, and asks Fleek whether it was approved when they come back (the licence is cached, so it would not notice on its own).
const OPENED_KEY = "plan_payment_opened_at";
const OPENED_FOR_MS = 2 * 60 * 60 * 1000;
function paymentOpened(): boolean {
  try {
    const t = Number(localStorage.getItem(OPENED_KEY));
    return t > 0 && Date.now() - t < OPENED_FOR_MS;
  } catch {
    return false;
  }
}
function setPaymentOpened(on: boolean) {
  try {
    if (on) localStorage.setItem(OPENED_KEY, String(Date.now()));
    else localStorage.removeItem(OPENED_KEY);
  } catch {
    /* private window: the button below still works */
  }
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
  const [opened, setOpened] = useState(paymentOpened);

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
          ? "Done. You are on the Free plan. Your invoices and data are safe."
          : `Cancelled. You keep your plan until ${r.ends_on ? day(r.ends_on) : "the end of the period"}, and nothing more will be charged.`,
      );
      await qc.invalidateQueries({ queryKey: PLANS_KEY });
      await refresh();
    },
    onError: (e) => setNote(message(e)),
  });

  // Back from the payment page (or the tab was left open): ask whether the autopay was approved, so the page does not wait on a webhook.
  const pending = (data?.current.state === "payment_pending" || opened) && !!data?.can_manage;
  const check = useMutation({
    mutationFn: () => api<{ changed: boolean }>("/plans/check", { method: "POST" }),
    onSuccess: async (r) => {
      if (r.changed) {
        setPaymentOpened(false);
        setOpened(false);
        await qc.invalidateQueries({ queryKey: PLANS_KEY });
        await refresh();
      }
    },
  });
  // on a paid plan there is nothing left to wait for
  useEffect(() => {
    if (opened && data && !data.current.free) {
      setPaymentOpened(false);
      setOpened(false);
    }
  }, [opened, data]);
  const checkNow = check.mutate;
  useEffect(() => {
    if (!pending) return;
    checkNow();
    const onVisible = () => document.visibilityState === "visible" && checkNow();
    document.addEventListener("visibilitychange", onVisible);
    return () => document.removeEventListener("visibilitychange", onVisible);
  }, [pending, checkNow]);

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
        {cur.free && opened && owner && (
          <div className="mt-3 flex flex-wrap items-center gap-2 text-sm">
            <span className="meta">Went to the payment page? If you approved it, check here. If not, you are still on Free and nothing changed.</span>
            <button className="btn-ghost" disabled={check.isPending} onClick={() => check.mutate()}>
              {check.isPending ? "Checking…" : "I approved it. Check again"}
            </button>
          </div>
        )}
        {paidNow && cur.status === "trial" && (
          <p className="meta mt-1">
            Nothing has been charged. If you keep it, your payment is taken when the trial ends; cancel before then and you pay nothing. Your invoices and data stay either way.
          </p>
        )}
        {notice(cur) && (
          <p className="mt-2 rounded-lg border border-line bg-[#f6ecd5] px-3 py-2 text-sm text-[#5b4410]" role="status">{notice(cur)}</p>
        )}
        {cur.can_retry && owner && (
          <div className="mt-3 flex flex-wrap items-center gap-2">
            {cur.state === "payment_pending" && (
              <button className="btn-ghost" disabled={check.isPending} onClick={() => check.mutate()}>
                {check.isPending ? "Checking…" : "I approved it. Check again"}
              </button>
            )}
            <button className="btn-ghost" disabled={cancel.isPending} onClick={() => cancel.mutate()}>
              {cancel.isPending ? "Working…" : "Stay on Free"}
            </button>
          </div>
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

/** What went wrong with the last payment, in plain words (null when nothing did). Nothing here ever says we charged when we did not. */
function notice(cur: PlansOut["current"]): string | null {
  const plan = cur.plan_label ?? "your plan";
  switch (cur.state) {
    case "payment_pending":
      return `The payment for ${plan} was not completed, so nothing was charged and you are on Free. Choose a plan below to try again.`;
    case "past_due":
      return `Your last payment for ${plan} did not go through. It stays on${cur.grace_until ? ` until ${day(cur.grace_until)}` : " for a few days"} while we retry. Please make sure your UPI or card can pay.`;
    case "payment_failed":
      return `The payment for ${plan} failed, so you are back on Free. Your invoices and data are safe. Choose a plan below to start again.`;
    case "cancelled":
      return `${plan} was cancelled and its paid time is over, so you are on Free. Your invoices and data are safe.`;
    case "trial_ended":
      return `The ${plan} trial ended without a payment, so you are on Free. Your invoices and data are safe.`;
    default:
      return null;
  }
}

function PlanCard({ p, data, onStart }: { p: Plan; data: PlansOut; onStart: () => void }) {
  const here = data.current.plan_key === p.plan || (p.price_amount === 0 && data.current.free);
  const recommended = p.plan.endsWith("growth");
  const canStart = data.can_manage && data.current.free && p.price_amount > 0;
  const label = p.trial_days > 0 ? `Try free for ${p.trial_days} days` : `Choose ${p.label}`;
  return (
    <div className={`card flex flex-col gap-2 p-4 ${here ? "border-accent" : recommended ? "border-2 border-brass-light bg-gradient-to-b from-brass-soft/40 to-card" : ""}`}>
      <div className="flex items-center gap-2">
        <span className="font-semibold">{p.label}</span>
        {here && <span className="rounded-full bg-accent-soft px-2 text-[11px] font-semibold text-accent">Your plan</span>}
        {recommended && !here && <span className="rounded-full bg-brass-light px-2 text-[11px] font-semibold text-white">Most shops</span>}
      </div>
      <div className={`font-serif text-2xl font-semibold ${p.price_amount > 0 ? "text-brass" : ""}`}>
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
        <button className="btn-brass mt-auto" onClick={onStart}>
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
      setPaymentOpened(true);
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
              <li>The trial starts once you approve. If you do not, you stay on Free and nothing changes.</li>
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
        <button className="btn-brass w-full" disabled={start.isPending} onClick={() => start.mutate()}>
          {start.isPending ? "Opening the payment page…" : trial ? `Start my ${p.trial_days} days` : "Continue to payment"}
        </button>
        <button className="btn-ghost mt-2 w-full" onClick={onClose}>Not now</button>
      </div>
    </>
  );
}
