import { useEffect, useState } from "react";
import { Link, Navigate } from "react-router-dom";
import { api } from "../lib/api";
import { useAuth } from "../lib/auth";
import { reportSignup, takeSignupFlag } from "../lib/analytics";

const HAVE = [
  { title: "Invoices", text: "Make and send" },
  { title: "Customers", text: "Keep their balance" },
  { title: "Items", text: "Rates by weight" },
  { title: "Payments", text: "Match to bills" },
];

/** "Your account is ready": shown once, to the owner of a firm that signed itself up. The server remembers that it was seen
 *  (not a browser tab), so a refresh, a second phone or a link from WhatsApp does not show it again, and an older or
 *  operator-made account never sees it. The ad campaign counts it as the sign-up, so that report still needs the tab flag. */
export function WelcomePage() {
  const { me } = useAuth();
  const [show] = useState(() => !!me?.show_welcome); // held, so marking it seen does not take the page away mid-read
  const [fresh] = useState(takeSignupFlag);
  useEffect(() => {
    if (!show) return;
    if (fresh) reportSignup();
    void api<void>("/onboarding/welcome-seen", { method: "POST" }).catch(() => {
      /* it simply shows once more */
    });
  }, [show, fresh]);
  if (!show) return <Navigate to="/invoices" replace />;
  const days = me?.pdf_days_left ?? null;
  const canImport = me?.features?.import !== false;
  return (
    <div className="mx-auto mt-6 max-w-md overflow-hidden rounded-xl border border-line bg-card">
      <div className="bg-gradient-to-br from-accent-deep to-accent px-5 pb-5 pt-6 text-center text-white">
        <div aria-hidden className="mx-auto mb-2.5 grid h-14 w-14 place-items-center rounded-full bg-white text-2xl font-semibold text-ok">✓</div>
        <h1 className="font-serif text-[25px] font-semibold leading-tight">
          Welcome. {me?.firm_name ? `${me.firm_name} is ready.` : "Your account is ready."}
        </h1>
        <p className="mt-1.5 text-sm text-[#d6e6ee]">
          You are on the Free plan{days && days > 0 ? `, with invoice PDFs free for ${days} ${days === 1 ? "day" : "days"}` : ""}.
        </p>
      </div>
      <div className="p-5">
        <ul className="grid grid-cols-2 gap-2">
          {HAVE.map((h) => (
            <li key={h.title} className="rounded-lg bg-accent-soft px-2.5 py-2 text-xs">
              <b className="block text-[13px]">{h.title}</b>
              {h.text}
            </li>
          ))}
        </ul>
        <p className="mt-4 text-sm text-muted">Your first invoice takes about two minutes. You can download it and send it to a customer straight away.</p>
        <Link to="/invoices/new" className="btn-brass mt-3 w-full" data-umami-event="welcome-first-invoice">Make my first invoice</Link>
        {canImport && (
          <p className="meta mt-3 text-center">
            Using Tally? <Link to="/parties/import" className="underline">Bring in your customers and items</Link>
          </p>
        )}
        <p className="meta mt-2 text-center">
          <Link to="/setup" className="underline">See all the steps</Link> · <Link to="/invoices" className="underline">Skip to my invoices</Link>
        </p>
      </div>
    </div>
  );
}
