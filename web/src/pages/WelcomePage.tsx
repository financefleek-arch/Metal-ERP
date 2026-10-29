import { useEffect, useState } from "react";
import { Link, Navigate } from "react-router-dom";
import { api } from "../lib/api";
import { useAuth } from "../lib/auth";
import { reportSignup, takeSignupFlag } from "../lib/analytics";

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
  return (
    <div className="mx-auto mt-10 max-w-md">
      <div className="card p-8 text-center">
        <div className="mb-2 text-3xl" aria-hidden>🎉</div>
        <h1 className="page-title mb-2">Your account is ready</h1>
        <p className="mb-5 text-sm text-muted">
          Let us make your first invoice. It takes about two minutes, and you can download it and send it to a customer straight away.
        </p>
        <Link to="/invoices/new" className="btn-primary w-full">Make my first invoice</Link>
        <p className="meta mt-3">
          <Link to="/setup" className="underline">See all the steps</Link> · <Link to="/invoices" className="underline">Skip to my invoices</Link>
        </p>
      </div>
    </div>
  );
}
