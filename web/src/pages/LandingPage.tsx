import { useEffect } from "react";
import { rupees, usePublicPlans } from "../lib/plans";

const PLAY_URL = "https://play.google.com/store/apps/details?id=in.fleekfinance.metalerp";
const PRIVACY = "https://fleekfintech.com/privacy-policy/";
const TERMS = "https://fleekfintech.com/terms/";

// `id` is what an ad sitelink points at (metal.fleekfinance.in/#invoices, /#catalog).
const FEATURES: { id?: string; title: string; text: string }[] = [
  { title: "Bill by weight", text: "Make invoices on your phone with weighment slips and weight-based rates for metal sold by the kilo." },
  { title: "Know who owes you", text: "See who owes you and for how long, and record every payment against the right bill." },
  { id: "invoices", title: "Send on WhatsApp", text: "Share the invoice PDF with your customer in one tap, and send payment reminders you approve first." },
  { id: "catalog", title: "Customers order by link", text: "Share a catalog link. Customers pick items and order; you turn each order into an invoice." },
];

export function LandingPage({ onSignup, onSignin }: { onSignup: () => void; onSignin: () => void }) {
  const android = /android/i.test(navigator.userAgent);
  const { data: ladder } = usePublicPlans();
  // The page is drawn after load, so the browser cannot jump to a #section by itself.
  useEffect(() => {
    const id = window.location.hash.slice(1);
    if (id) document.getElementById(id)?.scrollIntoView({ block: "center" });
  }, []);
  return (
    <div className="min-h-[100dvh]">
      <header className="mx-auto flex max-w-5xl items-center justify-between px-4 py-4">
        <span className="font-serif text-lg font-semibold">Tally Companion</span>
        <button onClick={onSignin} data-umami-event="landing-signin" className="text-sm text-accent hover:text-accent-dark">
          Sign in
        </button>
      </header>

      <main className="mx-auto max-w-5xl px-4 pb-10">
        <section className="py-8 md:py-14">
          <h1 className="max-w-2xl font-serif text-[32px] font-semibold leading-tight md:text-5xl">
            Billing for metal and bartan shops
          </h1>
          <p className="mt-4 max-w-xl text-lg text-muted">
            Make invoices by weight, see who owes you, and share bills on WhatsApp. Free to start, no card needed.
          </p>
          <div className="mt-6 flex flex-wrap items-center gap-3">
            <button onClick={onSignup} data-umami-event="landing-create-account" className="btn-primary !h-12 px-6 text-base md:!h-11">
              Create free account
            </button>
            {android && (
              <a href={PLAY_URL} data-umami-event="landing-google-play" className="btn-ghost !h-12 px-5 text-base md:!h-11">
                Get it on Google Play
              </a>
            )}
          </div>
          <p className="meta mt-3">
            Works in any browser{android ? " and as an Android app" : ""}. Create your account with your mobile number.
          </p>
        </section>

        <section className="grid gap-3 sm:grid-cols-2">
          {FEATURES.map((f) => (
            <div key={f.title} id={f.id} className="card scroll-mt-4 p-5 target:ring-2 target:ring-accent">
              <h2 className="section-title">{f.title}</h2>
              <p className="mt-1 text-sm text-muted">{f.text}</p>
            </div>
          ))}
        </section>

        <section className="card mt-6 p-5">
          <h2 className="section-title">Start free, grow when you are ready</h2>
          <p className="mt-1 text-sm text-muted">
            The free plan covers invoices, customers, items and recording payments, for one user, and you can download and share your invoice PDFs
            for your first 7 days. Monthly plans after that, no GST for now.
          </p>
          {ladder && (
            <ul className="mt-3 grid gap-2 text-sm sm:grid-cols-2">
              {ladder.plans
                .filter((p) => p.price_amount > 0)
                .map((p) => (
                  <li key={p.plan} className="rounded-lg border border-line px-3 py-2">
                    <b>{p.label}</b> · {rupees(p.price_amount)} a month
                    {p.trial_days > 0 && <span className="meta"> · {p.trial_days} days free</span>}
                  </li>
                ))}
            </ul>
          )}
        </section>
      </main>

      <footer className="mx-auto max-w-5xl px-4 pb-10 text-xs text-muted">
        <p>
          <a className="underline" href={PRIVACY} data-umami-event="landing-privacy">Privacy policy</a> · <a className="underline" href={TERMS} data-umami-event="landing-terms">Terms</a> ·{" "}
          <a className="underline" href="/delete-account" data-umami-event="landing-delete-account">Delete your account</a> · contact@fleekfinance.in
        </p>
        <p className="mt-2">
          Tally Companion is built by Fleek Fintech. Tally and TallyPrime are trademarks of Tally Solutions Pvt. Ltd. This app is not
          affiliated with or endorsed by Tally Solutions.
        </p>
      </footer>
    </div>
  );
}
