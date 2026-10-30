import { useEffect, useState } from "react";
import { Navigate } from "react-router-dom";
import { api } from "../lib/api";
import { useAuth } from "../lib/auth";
import { reportSignup, takeSignupFlag } from "../lib/analytics";
import { BillSetupFlow } from "./BillSetupFlow";

/** "Your account is ready": shown once, to the owner of a firm that signed itself up. The server remembers that it was seen
 *  (not a browser tab), so a refresh, a second phone or a link from WhatsApp does not show it again, and an older or
 *  operator-made account never sees it. The ad campaign counts it as the sign-up, so that report still needs the tab flag.
 *  What it shows is the bill-setup conversation: the shop's own bill, filling in as it answers three short questions. */
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
    <BillSetupFlow
      heading={
        <div className="mb-4">
          <h1 className="font-serif text-[25px] font-semibold leading-tight">
            Welcome{me?.firm_name ? `, ${me.firm_name}` : ""}.
          </h1>
          <p className="mt-1 text-sm text-muted">This is the bill your customers will get. Let us make it yours.</p>
        </div>
      }
    />
  );
}
