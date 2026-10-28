import { useEffect, useState } from "react";
import { Link, Navigate } from "react-router-dom";
import { reportSignup, takeSignupFlag } from "../lib/analytics";

/** The one page only a brand-new account sees (right after both codes are right). It is the page the ad campaign counts as a
 *  sign-up, so it must not be reachable by returning users: without the flag it goes straight to the app. */
export function WelcomePage() {
  const [fresh] = useState(takeSignupFlag);
  useEffect(() => {
    if (fresh) reportSignup();
  }, [fresh]);
  if (!fresh) return <Navigate to="/invoices" replace />;
  return (
    <div className="mx-auto mt-10 max-w-md">
      <div className="card p-8 text-center">
        <div className="mb-2 text-3xl" aria-hidden>🎉</div>
        <h1 className="page-title mb-2">Your account is ready</h1>
        <p className="mb-5 text-sm text-muted">
          You are on the Free plan: invoices, customers, items and payments. Six short steps will get your first invoice out.
        </p>
        <Link to="/setup" className="btn-primary">Start setup</Link>
        <p className="meta mt-3"><Link to="/invoices" className="underline">Skip to my invoices</Link></p>
      </div>
    </div>
  );
}
