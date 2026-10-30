import { Navigate } from "react-router-dom";
import { useAuth } from "../lib/auth";
import { BillSetupFlow } from "./BillSetupFlow";

/** The same bill-setup conversation, any time, for the owner: the Setup checklist sends firms here to finish their details. */
export function StartPage() {
  const { me } = useAuth();
  if (me?.role !== "owner") return <Navigate to="/invoices" replace />;
  return (
    <BillSetupFlow
      heading={
        <div className="mb-4">
          <h1 className="font-serif text-[25px] font-semibold leading-tight">Set up your bill</h1>
          <p className="mt-1 text-sm text-muted">Three short questions. Watch your bill fill in as you answer.</p>
        </div>
      }
    />
  );
}
