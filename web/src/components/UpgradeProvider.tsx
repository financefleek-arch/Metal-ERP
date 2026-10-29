import { useCallback, useMemo, useState } from "react";
import type { ReactNode } from "react";
import { Link } from "react-router-dom";
import { beacon } from "../lib/beacon";
import { FEATURE_NAME } from "../lib/features";
import { cheapestFor, rupees, usePlans } from "../lib/plans";
import type { FeatureKey } from "../lib/types";
import { UpgradeCtx } from "../lib/upgrade";

export function UpgradeProvider({ children }: { children: ReactNode }) {
  const [feature, setFeature] = useState<FeatureKey | null>(null);
  const open = useCallback((f: FeatureKey) => {
    beacon(f === "whatsapp" ? "whatsapp_teaser_tapped" : "locked_feature_tapped", { feature: f });
    setFeature(f);
  }, []);
  const api = useMemo(() => ({ open }), [open]);
  return (
    <UpgradeCtx.Provider value={api}>
      {children}
      {feature && <Sheet feature={feature} onClose={() => setFeature(null)} />}
    </UpgradeCtx.Provider>
  );
}

function Sheet({ feature, onClose }: { feature: FeatureKey; onClose: () => void }) {
  const { data } = usePlans();
  const plan = cheapestFor(feature, data?.plans);
  const trial = plan && plan.trial_days > 0;
  return (
    <>
      <div className="fixed inset-0 z-40 bg-black/45" onClick={onClose} />
      <div
        role="dialog"
        aria-label="Not on your plan"
        className="fixed inset-x-0 bottom-0 z-50 mx-auto max-w-md rounded-t-2xl bg-card p-5 pb-[calc(1.25rem+env(safe-area-inset-bottom,0px))] shadow-xl md:bottom-auto md:top-1/4 md:rounded-2xl"
      >
        <h2 className="section-title mb-1">{FEATURE_NAME[feature] ?? "This feature"}</h2>
        <p className="mb-3 text-sm text-muted">
          {plan ? (
            <>
              It comes with <b>{plan.label}</b>, {rupees(plan.price_amount)} a month
              {trial ? `, and you can try it free for ${plan.trial_days} days` : ""}.
            </>
          ) : (
            "It is part of a paid plan."
          )}
        </p>
        <Link to={plan ? `/plans?plan=${plan.plan}` : "/plans"} className="btn-primary w-full" onClick={onClose}>
          {trial ? `Try free for ${plan!.trial_days} days` : "See plans"}
        </Link>
        <button className="btn-ghost mt-2 w-full" onClick={onClose}>
          Not now
        </button>
      </div>
    </>
  );
}
