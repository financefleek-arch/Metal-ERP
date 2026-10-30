import { useEffect } from "react";
import { featureLabel, rupees, usePublicPlans } from "../lib/plans";

const PLAY_URL = "https://play.google.com/store/apps/details?id=in.fleekfinance.metalerp";
const PRIVACY = "https://fleekfintech.com/privacy-policy/";
const TERMS = "https://fleekfintech.com/terms/";

// `id` is what an ad sitelink points at (metal.fleekfinance.in/#invoices, /#catalog).
const FEATURES: { id?: string; icon: string; tone: string; title: string; text: string }[] = [
  { icon: "kg", tone: "bg-accent", title: "Bill by weight", text: "Weighment slips and weight-based rates, for metal sold by the kilo." },
  { icon: "₹", tone: "bg-brass-light", title: "Know who owes you", text: "See who owes you and for how long. Record every payment against the right bill." },
  { id: "invoices", icon: "WA", tone: "bg-wa", title: "Send on WhatsApp", text: "Share the invoice in one tap. Payment reminders go out only after you approve them." },
  { id: "catalog", icon: "QR", tone: "bg-[#6b5b95]", title: "Customers order by link", text: "Share a catalog link. They pick items and order, and you turn each order into an invoice." },
];

const STEPS = [
  { title: "Create your account", text: "Your mobile number and a WhatsApp code. Nothing else to fill in." },
  { title: "Add a customer and an item", text: "Type them in, or bring them over from Tally." },
  { title: "Bill and send", text: "Enter the weight, tap, and share the PDF on WhatsApp." },
];

/** What the hero shows instead of a screenshot: a made-up invoice and the WhatsApp message it becomes. */
function InvoiceArt({ tilt }: { tilt: string }) {
  return (
    <div aria-hidden>
      <div className={`rounded-xl bg-white p-3.5 text-ink shadow-[0_10px_24px_rgba(0,0,0,0.22)] ${tilt}`}>
        <div className="mb-1.5 flex items-baseline justify-between border-b border-line pb-1.5">
          <b className="font-serif text-sm">Invoice 0042</b>
          <span className="text-[11px] text-muted">Example · Sharma Metals</span>
        </div>
        <table className="w-full text-xs tabular-nums">
          <thead>
            <tr className="micro text-left">
              <th className="pb-1 font-medium">Item</th>
              <th className="pb-1 font-medium">Weight</th>
              <th className="pb-1 text-right font-medium">Amount</th>
            </tr>
          </thead>
          <tbody>
            <tr><td className="py-0.5">Brass sheet</td><td>12.40 kg</td><td className="text-right">₹6,448</td></tr>
            <tr><td className="py-0.5">Copper wire</td><td>8.15 kg</td><td className="text-right">₹6,520</td></tr>
          </tbody>
        </table>
        <div className="mt-1.5 flex justify-between border-t border-ink pt-1.5 text-sm font-semibold tabular-nums"><span>Total</span><span>₹12,968</span></div>
        <div className="mt-1 font-mono text-[11px] text-muted">Weighment slip attached</div>
      </div>
      <div className="relative -mt-3 ml-auto w-[78%] rounded-[10px_10px_2px_10px] bg-[#dcf8c6] px-2.5 py-2 text-xs text-[#1d2b19] shadow-[0_6px_14px_rgba(0,0,0,0.18)]">
        <b className="block">Invoice 0042 · ₹12,968</b>
        Sent on WhatsApp ✓✓
      </div>
    </div>
  );
}

export function LandingPage({ onSignup, onSignin }: { onSignup: () => void; onSignin: () => void }) {
  const android = /android/i.test(navigator.userAgent);
  const { data: ladder } = usePublicPlans();
  const paid = (ladder?.plans ?? []).filter((p) => p.price_amount > 0);
  // The page is drawn after load, so the browser cannot jump to a #section by itself.
  useEffect(() => {
    const id = window.location.hash.slice(1);
    if (id) document.getElementById(id)?.scrollIntoView({ block: "center" });
  }, []);
  const signup = (where: string) => (
    <button onClick={onSignup} data-umami-event={where === "hero" ? "landing-create-account" : `landing-create-account-${where}`} className="btn-brass !h-12 px-6 text-base md:!h-11">
      Create free account
    </button>
  );
  return (
    <div className="min-h-[100dvh] pb-20 md:pb-0">
      <header className="bg-accent-deep text-white">
        <div className="mx-auto flex max-w-5xl items-center justify-between px-4 py-3.5">
          <span className="font-serif text-lg font-semibold">Tally Companion</span>
          <button onClick={onSignin} data-umami-event="landing-signin" className="text-sm text-white/85 hover:text-white">
            Sign in
          </button>
        </div>
      </header>

      <section className="bg-gradient-to-br from-accent-deep via-[#24566e] to-accent text-white">
        <div className="mx-auto grid max-w-5xl items-center gap-8 px-4 pb-10 pt-6 md:grid-cols-[1.1fr_0.9fr] md:gap-12 md:py-14">
          <div>
            <span className="inline-block rounded-full bg-white/15 px-3 py-0.5 text-xs font-semibold text-[#f1cf86]">For metal and bartan shops</span>
            <h1 className="mt-3 max-w-xl font-serif text-[32px] font-semibold leading-[1.1] md:text-5xl">
              Weigh it. Bill it. <span className="text-[#f1cf86]">Get paid faster.</span>
            </h1>
            <p className="mt-4 max-w-md text-base text-[#d6e6ee] md:text-lg">
              Make invoices by weight on your phone, see who owes you, and send the bill on WhatsApp in one tap.
            </p>
            <div className="mt-6 flex flex-wrap items-center gap-3">
              {signup("hero")}
              {android && (
                <a href={PLAY_URL} data-umami-event="landing-google-play" className="btn !h-12 border border-white/40 px-5 text-base text-white hover:bg-white/10 md:!h-11">
                  Get it on Google Play
                </a>
              )}
            </div>
            <p className="mt-3 text-xs text-[#b7d0dc]">
              Free to start · No card · Takes 2 minutes. Works in any browser{android ? " and as an Android app" : ""}.
            </p>
          </div>
          <InvoiceArt tilt="-rotate-1 md:rotate-1" />
        </div>
      </section>

      <div className="border-b border-line bg-card">
        <div className="mx-auto grid max-w-5xl grid-cols-3 gap-2 px-4 py-3.5 text-center text-xs text-muted">
          <div><b className="block font-serif text-base text-ink">2 min</b>to your first invoice</div>
          <div><b className="block font-serif text-base text-ink">₹0</b>to start, no card</div>
          <div><b className="block font-serif text-base text-ink">Tally</b>friendly</div>
        </div>
      </div>

      <main className="mx-auto max-w-5xl px-4">
        <section className="pt-8">
          <h2 className="font-serif text-2xl font-semibold">Up and running in three steps</h2>
          <p className="mt-1 text-muted">No set-up call and no training.</p>
          <ol className="mt-4 grid gap-3 md:grid-cols-3">
            {STEPS.map((s, i) => (
              <li key={s.title} className="card flex items-start gap-3 p-3.5">
                <span className="grid h-8 w-8 flex-none place-items-center rounded-full bg-accent font-serif font-semibold text-white">{i + 1}</span>
                <span><b className="block">{s.title}</b><span className="text-sm text-muted">{s.text}</span></span>
              </li>
            ))}
          </ol>
        </section>

        <section className="pt-9">
          <h2 className="font-serif text-2xl font-semibold">Made for how you sell</h2>
          <div className="mt-4 grid gap-3 sm:grid-cols-2">
            {FEATURES.map((f) => (
              <div key={f.title} id={f.id} className="card flex scroll-mt-4 gap-3 p-3.5 target:ring-2 target:ring-accent">
                <span aria-hidden className={`grid h-10 w-10 flex-none place-items-center rounded-[9px] text-[13px] font-semibold text-white ${f.tone}`}>{f.icon}</span>
                <span><b className="block">{f.title}</b><span className="text-sm text-muted">{f.text}</span></span>
              </div>
            ))}
          </div>
        </section>
      </main>

      <section id="plans" className="mt-10 border-y border-[#cfe0e8] bg-accent-soft">
        <div className="mx-auto max-w-5xl px-4 py-8">
          <h2 className="font-serif text-2xl font-semibold">Start free. Pay only when you grow.</h2>
          <p className="mt-1 text-[#4b6b7a]">Monthly plans. No GST for now. Stop any time.</p>
          <div className="mt-4 flex items-center gap-3 rounded-xl border border-dashed border-ok bg-card px-3.5 py-3">
            <span aria-hidden className="grid h-7 w-7 flex-none place-items-center rounded-full bg-ok text-white">✓</span>
            <span>
              <b>Free to begin with</b>
              <span className="block text-sm text-muted">
                Invoices, customers, items and payments for one user. Download and share invoice PDFs free for your first 7 days.
              </span>
            </span>
          </div>
          {paid.length > 0 && (
            <ul className={`mt-4 grid gap-3 ${paid.length === 3 ? "md:grid-cols-3" : "md:grid-cols-2"}`}>
              {paid.map((p) => {
                const rec = p.plan.endsWith("growth");
                return (
                  <li key={p.plan} className={`relative flex flex-col rounded-xl bg-card p-4 ${rec ? "border-2 border-brass-light bg-gradient-to-b from-brass-soft/40 to-card" : "border border-line"}`}>
                    {rec && <span className="absolute -top-2.5 right-3 rounded-full bg-brass-light px-2.5 py-0.5 text-[11px] font-semibold text-white">Most shops</span>}
                    <div className="flex items-baseline justify-between gap-2">
                      <span className="font-semibold">{p.label}</span>
                      <span className="font-serif text-[26px] font-semibold text-brass tabular-nums">
                        {rupees(p.price_amount)}
                        <span className="ml-0.5 font-sans text-xs font-normal text-muted">/month</span>
                      </span>
                    </div>
                    <ul className="mt-2 space-y-0.5 text-sm text-muted">
                      <li>{p.users} {p.users === 1 ? "user" : "users"}</li>
                      {p.unlocks.slice(0, 3).map((k) => (
                        <li key={k}>✓ {featureLabel(k as never)}</li>
                      ))}
                    </ul>
                    {p.trial_days > 0 && <span className="mt-3 inline-block text-xs font-semibold text-brass">{p.trial_days} days free, cancel in one tap</span>}
                  </li>
                );
              })}
            </ul>
          )}
          <div className="mt-5">{signup("plans")}</div>
        </div>
      </section>

      <section className="bg-ink px-4 py-9 text-center text-white">
        <h2 className="mx-auto max-w-md font-serif text-[22px] font-semibold leading-tight">Your first invoice is two minutes away</h2>
        <p className="mt-1.5 text-[#c9c3b6]">Free to start. No card.</p>
        <div className="mt-4">{signup("footer")}</div>
      </section>

      <footer className="mx-auto max-w-5xl px-4 py-6 text-xs text-muted">
        <p>
          <a className="underline" href={PRIVACY} data-umami-event="landing-privacy">Privacy policy</a> · <a className="underline" href={TERMS} data-umami-event="landing-terms">Terms</a> ·{" "}
          <a className="underline" href="/delete-account" data-umami-event="landing-delete-account">Delete your account</a> · contact@fleekfinance.in
        </p>
        <p className="mt-2">
          Tally Companion is built by Fleek Fintech. Tally and TallyPrime are trademarks of Tally Solutions Pvt. Ltd. This app is not
          affiliated with or endorsed by Tally Solutions.
        </p>
      </footer>

      {/* a phone keeps the way in under the thumb while reading */}
      <div className="fixed inset-x-0 bottom-0 z-20 flex items-center gap-3 border-t border-line bg-card px-4 pt-2.5 pb-[calc(0.625rem+env(safe-area-inset-bottom,0px))] shadow-[0_-6px_16px_rgba(0,0,0,0.08)] md:hidden">
        <span className="flex-1 text-xs leading-tight"><b className="block text-[13px]">Free to start</b>No card needed</span>
        <button onClick={onSignup} data-umami-event="landing-create-account-sticky" className="btn-brass !h-10 px-5">Create account</button>
      </div>
    </div>
  );
}
