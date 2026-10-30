import { useEffect, useRef, useState } from "react";
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
  firm: "Set up my bill",
  invoice: "Make my first invoice",
  pdf: "Open my invoices",
  payment: "Record a payment",
  bank: "Add bank or UPI",
};

/** The first steps as a short list. Only the next one asks for a tap, so the page has one obvious thing to do. */
function Steps({ steps }: { steps: SetupStep[] }) {
  const next = steps.find((s) => !s.done);
  return (
    <ul className="divide-y divide-line">
      {steps.map((s) => {
        const isNext = s === next;
        return (
          <li key={s.key} className={`flex items-start gap-3 py-3 ${isNext ? "-mx-3 rounded-lg bg-accent-soft px-3" : ""}`}>
            <span
              aria-hidden
              className={`mt-0.5 grid h-[22px] w-[22px] flex-none place-items-center rounded-full border-2 text-xs text-white ${
                s.done ? "border-ok bg-ok" : isNext ? "border-accent" : "border-line"
              }`}
            >
              {s.done ? "✓" : ""}
            </span>
            <div className="min-w-0 flex-1">
              <div className={s.done ? "text-muted line-through decoration-line" : "font-medium"}>{s.title}</div>
              {!s.done && <div className="meta">{s.hint}</div>}
            </div>
            {!s.done && (
              <Link
                to={s.route}
                className={isNext ? "btn-primary !h-9 flex-none !px-3 md:!h-8" : "flex-none self-center text-sm text-accent underline underline-offset-2"}
                onClick={() => beacon("setup_step_clicked", { step: s.key })}
              >
                {isNext ? (STEP_BUTTON[s.key] ?? "Open") : "Open"}
              </Link>
            )}
          </li>
        );
      })}
    </ul>
  );
}

const PILL: Record<FeatureStatus, { text: string; cls: string } | null> = {
  active: { text: "On", cls: "bg-[#e6efe8] text-[#3a5a48]" },
  setup: { text: "Ready to set up", cls: "bg-[#e3eef2] text-accent" },
  requested: { text: "Requested", cls: "bg-[#f6ecd5] text-[#7a5c14]" },
  locked: null, // a locked card says which plan holds it, below, instead of wearing a label
};

/** One optional feature. It tells you what it does, what to have ready, and the one thing to do next. The free trial
 *  is offered once, above the row, never repeated on each card. */
function FeatureCard({ f, canRequest }: { f: SetupFeature; canRequest: boolean }) {
  const qc = useQueryClient();
  const { data: plans } = usePlans(f.plan_locked);
  // the cheapest paid plan that switches on everything this card needs
  const unlockPlan = f.plan_locked
    ? (plans?.plans ?? [])
        .filter((p) => p.price_amount > 0 && f.needs.every((k) => p.unlocks.includes(k === "tally" ? "tally_backup" : k)))
        .sort((a, b) => a.price_amount - b.price_amount)[0]
    : undefined;
  const ask = useMutation({
    mutationFn: () => api<{ requested_at: string }>("/onboarding/requests", { method: "POST", body: { feature: f.key } }),
    onSuccess: () => qc.invalidateQueries({ queryKey: SETUP_KEY }),
  });
  const pill = PILL[f.status];
  return (
    <article className="flex h-full flex-col rounded-xl border border-line bg-card p-4">
      <div className="flex items-start justify-between gap-2">
        <h3 className="font-serif text-[17px] font-semibold leading-snug">{f.title}</h3>
        {pill && (
          <span className={`flex-none rounded-full px-2 py-0.5 text-[11px] font-medium ${pill.cls}`}>
            {f.status === "requested" && f.requested_at
              ? `Asked ${new Date(f.requested_at).toLocaleDateString("en-IN", { day: "numeric", month: "short" })}`
              : pill.text}
          </span>
        )}
      </div>
      <p className="mt-1.5 text-sm text-muted">{f.summary}</p>
      <dl className="mt-3 space-y-2 text-sm">
        <div>
          <dt className="micro">Good for</dt>
          <dd>{f.who}</dd>
        </div>
        <div>
          <dt className="micro">Have ready</dt>
          <dd>
            <ul className="list-disc pl-5 text-muted">
              {f.ready.map((r) => (
                <li key={r}>{r}</li>
              ))}
            </ul>
          </dd>
        </div>
      </dl>
      <div className="mt-auto pt-4">
        <p className="meta mb-2">About {f.minutes} minutes.</p>
        {f.status === "locked" && f.plan_locked && (
          <Link
            to={unlockPlan ? `/plans?plan=${unlockPlan.plan}` : "/plans"}
            className="text-sm text-accent underline underline-offset-2"
            onClick={() => beacon("locked_feature_tapped", { feature: f.key })}
          >
            {unlockPlan ? `Part of ${unlockPlan.label}, ${rupees(unlockPlan.price_amount)} a month` : "See plans"}
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
          <p className="err" role="alert">
            {ask.error instanceof ApiError ? ask.error.message : "Could not ask Fleek. Try again."}
          </p>
        )}
      </div>
    </article>
  );
}

/** A row of cards you swipe on a phone and step through with arrows on a computer. */
function Carousel({ label, children }: { label: string; children: React.ReactNode[] }) {
  const track = useRef<HTMLDivElement>(null);
  const [idx, setIdx] = useState(0);
  const count = children.length;

  const onScroll = () => {
    const el = track.current;
    const card = el?.firstElementChild as HTMLElement | null;
    if (!el || !card) return;
    setIdx(Math.max(0, Math.min(count - 1, Math.round(el.scrollLeft / (card.offsetWidth + 12)))));
  };
  const go = (i: number) => {
    const card = track.current?.children[Math.max(0, Math.min(count - 1, i))] as HTMLElement | undefined;
    card?.scrollIntoView({ behavior: "smooth", inline: "start", block: "nearest" });
  };

  return (
    <div>
      <div className="mb-2 flex items-center justify-between gap-3">
        <h2 className="section-title">{label}</h2>
        <div className="hidden gap-1.5 md:flex">
          <button type="button" className="btn-ghost !h-8 !w-8 !px-0" aria-label="Previous" disabled={idx === 0} onClick={() => go(idx - 1)}>
            ‹
          </button>
          <button type="button" className="btn-ghost !h-8 !w-8 !px-0" aria-label="Next" disabled={idx >= count - 1} onClick={() => go(idx + 1)}>
            ›
          </button>
        </div>
      </div>
      <div
        ref={track}
        onScroll={onScroll}
        className="-mx-4 flex snap-x snap-mandatory gap-3 overflow-x-auto scroll-smooth px-4 pb-3 [scrollbar-width:none] sm:mx-0 sm:px-0 [&::-webkit-scrollbar]:hidden"
        role="list"
        aria-label={label}
      >
        {children.map((c, i) => (
          <div key={i} role="listitem" className="w-[84%] flex-none snap-start sm:w-[320px]">
            {c}
          </div>
        ))}
      </div>
      <div className="mt-1 flex justify-center gap-1.5" aria-hidden>
        {children.map((_, i) => (
          <span key={i} className={`h-1.5 rounded-full transition-all ${i === idx ? "w-5 bg-accent" : "w-1.5 bg-line"}`} />
        ))}
      </div>
    </div>
  );
}

export function SetupPage() {
  const { me } = useAuth();
  const qc = useQueryClient();
  const { data, isLoading, isError } = useSetup();
  const owner = me?.role === "owner";
  const anyPlanLocked = !!data?.features.some((f) => f.status === "locked" && f.plan_locked);
  const { data: plans } = usePlans(anyPlanLocked);
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
  const paid = (plans?.plans ?? []).filter((p) => p.price_amount > 0);
  const trialDays = paid.reduce((m, p) => Math.max(m, p.trial_days), 0);
  const from = paid.length ? Math.min(...paid.map((p) => p.price_amount)) : null;

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

      <section className="card mb-6 max-w-2xl p-5">
        <div className="mb-2 flex items-center gap-3">
          <ProgressRing done={data.done} total={data.total} />
          <div>
            <h2 className="section-title">Your first steps</h2>
            <p className="meta">Each one ticks itself when it is done.</p>
          </div>
        </div>
        <Steps steps={data.steps} />
      </section>

      <section aria-label="More features">
        {anyPlanLocked && trialDays > 0 && from !== null && (
          <div className="mb-4 flex flex-wrap items-center justify-between gap-3 rounded-xl border border-brass/40 bg-brass-soft px-4 py-3">
            <div className="min-w-0">
              <div className="font-semibold text-brass-dark">Try the paid features free for {trialDays} days</div>
              <div className="text-sm text-muted">Plans start at {rupees(from)} a month. Look through the features below first.</div>
            </div>
            <Link to="/plans" className="btn-brass !h-9 !px-3 md:!h-8" onClick={() => beacon("locked_feature_tapped", { feature: "banner" })}>
              See plans
            </Link>
          </div>
        )}
        <Carousel label="Add more when you are ready">
          {data.features.map((f) => (
            <FeatureCard key={f.key} f={f} canRequest={owner} />
          ))}
        </Carousel>
      </section>
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
