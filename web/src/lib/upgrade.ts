import { createContext, useContext } from "react";
import type { FeatureKey } from "./types";

export interface UpgradeApi {
  /** A locked feature was tapped (or the server said not_on_plan): say what it is and what unlocks it. */
  open: (feature: FeatureKey) => void;
}

export const UpgradeCtx = createContext<UpgradeApi>({ open: () => undefined });

export const useUpgrade = (): UpgradeApi => useContext(UpgradeCtx);

/** Read a not_on_plan refusal from an error's `detail` ({code: "not_on_plan", feature}). */
export function notOnPlanFeature(detail: unknown): FeatureKey | null {
  if (detail && typeof detail === "object" && (detail as { code?: string }).code === "not_on_plan") {
    return (detail as { feature: FeatureKey }).feature;
  }
  return null;
}
