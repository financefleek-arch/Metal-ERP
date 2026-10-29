import { useEffect, useState } from "react";
import { Link } from "react-router-dom";
import { useMutation, useQueryClient } from "@tanstack/react-query";
import { ApiError, api } from "../lib/api";
import { useAuth } from "../lib/auth";
import { beacon } from "../lib/beacon";
import { rupees, usePlans } from "../lib/plans";
import { SETUP_KEY, useSetup } from "../lib/setup";
import type { FeatureStatus, SetupFeature, SetupStep } from "../lib/types";
import { PageHeader } from "../components/PageHeader";

/** A circle filled to done/total, with the count inside. */
export function ProgressRing({ done, total, size = 54 }: { done: number; total: number; size?: number }) {
  const pct = total ? Math.round((done / total) * 100) : 0;
  const inner = size - 12;
  return (
    <div
      className="grid flex-none place-items-center rounded-full"
      style={{ width: size, height: size, background: `conic-gradient(#2f6f89 0 ${pct}%, #e6e0d6 0)` }}
      role="img"
      aria-label={`${done} of ${total} steps done`}
    >
      <span className="grid place-items-center rounded-full bg-card text-[13px] font-semibold" style={{ width: inner, height: inner }}>
        {done}/{total}
      </span>
    </div>
  );
}

const STEP_BUTTON: Record<string, string> = {
  firm: "Open firm details",
  invoice: "Make my first invoice",
  pdf: "Open my invoices",
  payment: "Record a payment",
  bank: "Add bank or UPI",
};

function Step({ s, first }: { s: SetupStep; first: boolean }) {
  return (
    <li className={`flex gap-3 py-3 ${first ? "" : "border-t border-line"}`}>
      <span
        aria-hidden
        className={`mt-0.5 grid h-[22px] w-[22px] flex-none place-items-center rounded-full border-2 text-xs text-white ${
          s.done ? "border-ok bg-ok" : "border-line"
        }`}
      >
        {s.done ? "✓" : ""}
      </span>
      <div className="min-w-0 flex-1">
        <div className={s.done ? "text-muted line-through decoration-line" : "font-medium"}>{s.title}</div>
        <div className="meta">{s.hint}</div>
        {!s.done && (
          <Link
            to={s.route}
            className="btn-primary mt-2 !h-9 !px-3 md:!h-8"
            onClick={() => beacon("setup_step_clicked", { step: s.key })}
          >
            {STEP_BUTTON[s.key] ?? "Open"}
          </Link>
        )}
      </div>
    </li>
  );
}

const PILL: Record<FeatureStatus, { text: string; cls: string }> = {
  active: { text: "Active", cls: "bg-[#e6efe8] text-[#3a5a48]" },
  setup: { text: "Set up now", cls: "bg-[#e3eef2] text-accent" },
  requested: { text: "Requested", cls: "bg-[#f6ecd5] text-[#7a5c14]" },
  locked: { text: "Not on your plan", cls: "bg-[#efe9df] text-muted" },
};

function FeatureCard({ f, canRequest }: { f: SetupFeature; canRequest: boolean }) {
  const qc = useQueryClient();
  const { data: plans } = usePlans(f.plan_locked);
  // the cheapest paid plan that switches on everything this card needs, so the card can show a price instead of "ask Fleek"
  const unlockPlan = f.plan_locked
    ? (plans?.plans ?? [])
        .filter((p) => p.price_amount > 0 && f.needs.every((k) => p.unlocks.includes(k === "tally" ? "tally_backup" : k)))
        .sort((a, b) => a.price_amount - b.price_amount)[0]
    : undefined;
  const [open, setOpen] = useState(false);
  const ask = useMutation({
    mutationFn: () => api<{ requested_at: string }>("/onboarding/requests", { method: "POST", body: { feature: f.key } }),
    onSuccess: () => qc.invalidateQueries({ queryKey: SETUP_KEY }),
  });
  const pill = PILL[f.status];
  const locked = f.status === "locked" || f.status === "requested";
  return (
    <div className={`rounded-xl border border-line p-4 ${locked ? "bg-[#fbfaf7]" : "bg-card"}`}>
      <div className="flex flex-wrap items-center gap-2">
        <h3 className="font-semibold">{f.title}</h3>
        <span className={`rounded-full px-2 py-0.5 text-[11px] font-medium ${pill.cls}`}>
          {f.status === "requested" && f.requested_at ? `Requested ${new Date(f.requested_at).toLocaleDateString("en-IN", { day: "numeric", month: "short" })}` : pill.text}
        </span>
      </div>
      <p className="mt-1.5 text-sm text-muted">{f.summary}</p>
      <button className="mt-2 text-xs text-accent hover:text-accent-dark" onClick={() => setOpen((o) => !o)} aria-expanded={open}>
        {open ? "Hide guide" : "What you need to know"}
      </button>
      {open && (
        <div className="mt-2 space-y-2 text-sm">
          <p><span className="micro">Good for</span><br />{f.who}</p>
          <div>
            <span className="micro">Have ready</span>
            <ul className="mt-1 list-disc pl-5 text-muted">{f.ready.map((r) => <li key={r}>{r}</li>)}</ul>
          </div>
          <p className="meta">About {f.minutes} minutes.</p>
        </div>
      )}
      <div className="mt-3">
        {f.status === "locked" && f.plan_locked && (
          <Link
            to={unlockPlan ? `/plans?plan=${unlockPlan.plan}` : "/plans"}
            className="btn-primary !h-9 !px-3 md:!h-8"
            onClick={() => beacon("locked_feature_tapped", { feature: f.key })}
          >
            {unlockPlan
              ? `${unlockPlan.trial_days ? `Try free for ${unlockPlan.trial_days} days` : "Choose"} · ${rupees(unlockPlan.price_amount)} a month`
              : "See plans"}
          </Link>
        )}
        {f.status === "locked" && !f.plan_locked && canRequest && (
          <button className="btn-primary !h-9 !px-3 md:!h-8" disabled={ask.isPending} onClick={() => ask.mutate()}>
            {ask.isPending ? "Asking…" : "Ask Fleek to switch this on"}
          </button>
        )}
        {f.status === "locked" && !f.plan_locked && !canRequest && <p className="meta">Ask the owner to request this.</p>}
        {f.status === "requested" && <p className="meta">Fleek will get back to you. Nothing to do now.</p>}
        {(f.status === "setup" || f.status === "active") && (
          <Link to={f.route} className={f.status === "setup" ? "btn-primary !h-9 !px-3 md:!h-8" : "btn-ghost !h-9 !px-3 md:!h-8"}>
            {f.status === "setup" ? "Set it up" : "Open"}
          </Link>
        )}
        {ask.isError && (
          <p className="err" role="alert">{ask.error instanceof ApiError ? ask.error.message : "Could not ask Fleek. Try again."}</p>
        )}
      </div>
    </div>
  );
}

export function SetupPage() {
  const { me } = useAuth();
  const qc = useQueryClient();
  const { data, isLoading, isError } = useSetup();
  const owner = me?.role === "owner";
  useEffect(() => {
    beacon("setup_viewed");
  }, []);
  const toggle = useMutation({
    mutationFn: (dismissed: boolean) => api<void>("/onboarding/dismissed", { method: "PUT", body: { dismissed } }),
    onSuccess: () => qc.invalidateQueries({ queryKey: SETUP_KEY }),
  });

  if (isLoading) return <div className="text-sm text-muted">Loading…</div>;
  if (isError || !data) return <div className="text-sm text-danger">Could not load Setup. Try again.</div>;

  const allDone = data.done === data.total;
  return (
    <div className="mx-auto max-w-5xl">
      <PageHeader
        title={allDone ? "You are set up" : "Get invoicing going"}
        subtitle={
          data.plan_tier === "free"
            ? "You are on the Free plan: invoices, customers, items and payments, for one user."
            : data.plan_label
              ? `Your plan: ${data.plan_label}`
              : undefined
        }
        actions={
          owner && (
            <button className="btn-ghost" onClick={() => toggle.mutate(!data.dismissed)}>
              {data.dismissed ? "Show Setup on my home screen" : "Hide Setup for now"}
            </button>
          )
        }
      />
      <div className="grid gap-6 lg:grid-cols-[5fr_7fr]">
        <section className="card h-fit p-5">
          <div className="mb-2 flex items-center gap-3">
            <ProgressRing done={data.done} total={data.total} />
            <h2 className="section-title">Your first steps</h2>
          </div>
          <ul>{data.steps.map((s, i) => <Step key={s.key} s={s} first={i === 0} />)}</ul>
          <p className="meta mt-2">Steps tick themselves when the data exists.</p>
        </section>
        <section>
          <h2 className="section-title mb-3">Add more when you are ready</h2>
          <div className="grid gap-3 sm:grid-cols-2 lg:grid-cols-1 xl:grid-cols-2">
            {data.features.map((f) => <FeatureCard key={f.key} f={f} canRequest={owner} />)}
          </div>
        </section>
      </div>
    </div>
  );
}

/** The card at the top of Sales while the first steps are not done and Setup has not been hidden. */
export function SetupCard() {
  const { me } = useAuth();
  const qc = useQueryClient();
  const { data } = useSetup(me?.role === "owner");
  const hide = useMutation({
    mutationFn: () => api<void>("/onboarding/dismissed", { method: "PUT", body: { dismissed: true } }),
    onSuccess: () => qc.invalidateQueries({ queryKey: SETUP_KEY }),
  });
  if (!data || data.dismissed || data.done === data.total) return null;
  const next = data.steps.find((s) => !s.done);
  return (
    <div className="mb-4 flex flex-wrap items-center gap-3 rounded-xl border border-[#cfe0e8] bg-accent-soft px-4 py-3">
      <ProgressRing done={data.done} total={data.total} size={44} />
      <div className="min-w-0 flex-1">
        <div className="font-semibold">Finish setting up</div>
        {next && <div className="meta">Next: {next.title.toLowerCase()}</div>}
      </div>
      <Link
        to={next?.route ?? "/setup"}
        className="btn-primary !h-9 !px-3 md:!h-8"
        onClick={() => next && beacon("setup_step_clicked", { step: next.key, from: "card" })}
      >
        {next ? STEP_BUTTON[next.key] ?? "Continue" : "Continue"}
      </Link>
      <button className="btn-ghost !h-9 !px-3 md:!h-8" onClick={() => hide.mutate()}>Hide for now</button>
    </div>
  );
}
