import { Link } from "react-router-dom";
import { cheapestFor, rupees, usePlans } from "../lib/plans";
import { beacon } from "../lib/beacon";
import type { FeatureKey } from "../lib/types";

/** A connection the firm's plan does not include, shown in place so it can be found and bought, never hidden. */
export function LockedConnection({ feature, title, text }: { feature: FeatureKey; title: string; text: string }) {
  const { data } = usePlans();
  const plan = cheapestFor(feature, data?.plans);
  return (
    <div className="card mb-5 p-4 sm:p-5">
      <div className="flex items-baseline justify-between gap-3">
        <h3 className="text-sm font-semibold">{title}</h3>
        <span className="rounded-full bg-[#efe9df] px-2 py-0.5 text-[11px] font-medium text-muted">Not on your plan</span>
      </div>
      <p className="mt-2 max-w-prose text-sm text-muted">{text}</p>
      <Link
        to={plan ? `/plans?plan=${plan.plan}` : "/plans"}
        className="mt-3 inline-block text-sm text-accent underline underline-offset-2"
        onClick={() => beacon("locked_feature_tapped", { feature })}
      >
        {plan ? `Part of ${plan.label}, ${rupees(plan.price_amount)} a month` : "See plans"}
      </Link>
    </div>
  );
}
