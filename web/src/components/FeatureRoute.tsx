import type { ReactNode } from "react";
import { Link } from "react-router-dom";
import { FEATURE_NAME, useFeature } from "../lib/features";
import type { FeatureKey } from "../lib/types";

/** A screen that only exists on a paid plan. On the Free plan it shows what the feature is and where to ask for it,
 *  instead of a screen whose every call would be refused. */
export function FeatureRoute({ feature, children }: { feature: FeatureKey; children: ReactNode }) {
  const on = useFeature(feature);
  if (on) return <>{children}</>;
  return (
    <div className="mx-auto mt-6 max-w-md">
      <div className="card p-8 text-center">
        <div className="mb-2 text-3xl" aria-hidden>🔒</div>
        <h1 className="section-title mb-1">Not part of the Free plan</h1>
        <p className="mb-4 text-sm text-muted">
          {FEATURE_NAME[feature]} is on paid plans. Recording payments and the party ledger stay free.
        </p>
        <Link to="/setup" className="btn-primary">See what is included</Link>
        <p className="meta mt-3">Opens Setup, where you can ask Fleek to switch it on.</p>
      </div>
    </div>
  );
}
