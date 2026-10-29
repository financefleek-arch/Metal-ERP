import type { ReactNode } from "react";
import { Link } from "react-router-dom";
import { FEATURE_NAME, useFeature } from "../lib/features";
import { cheapestFor, rupees, usePlans } from "../lib/plans";
import type { FeatureKey } from "../lib/types";

/** A screen that only exists on a paid plan. Without it the screen says what the feature is and which plan has it, with the price,
 *  instead of a screen whose every call would be refused. */
export function FeatureRoute({ feature, children }: { feature: FeatureKey; children: ReactNode }) {
  const on = useFeature(feature);
  const { data } = usePlans(!on);
  if (on) return <>{children}</>;
  const plan = cheapestFor(feature, data?.plans);
  return (
    <div className="mx-auto mt-6 max-w-md">
      <div className="card p-8 text-center">
        <div className="mb-2 text-3xl" aria-hidden>🔒</div>
        <h1 className="section-title mb-1">{FEATURE_NAME[feature]}</h1>
        <p className="mb-4 text-sm text-muted">
          {plan
            ? `It comes with ${plan.label}, ${rupees(plan.price_amount)} a month${plan.trial_days ? `, and you can try it free for ${plan.trial_days} days` : ""}.`
            : "It is part of a paid plan."}{" "}
          Recording payments and the party ledger stay free.
        </p>
        <Link to={plan ? `/plans?plan=${plan.plan}` : "/plans"} className="btn-primary">
          {plan?.trial_days ? `Try free for ${plan.trial_days} days` : "See plans"}
        </Link>
      </div>
    </div>
  );
}
