import { useQuery } from "@tanstack/react-query";
import { api } from "./api";
import { FEATURE_NAME } from "./features";
import type { FeatureKey } from "./types";

/** One rung of Fleek's plan ladder (read live from Fleek, so a price change there shows here with no deploy). */
export interface Plan {
  plan: string;
  label: string;
  price_amount: number;
  users: number;
  /** The feature keys this plan switches on. */
  unlocks: string[];
  /** Days it can be tried free (0 = not tried; Starter is the 7-day window on Free). */
  trial_days: number;
}

export interface PlansOut {
  plans: Plan[];
  current: {
    plan_key: string | null;
    plan_label: string | null;
    status: string | null;
    trial_days_left: number | null;
    /** Why the plan is not paid, beside `status` (null = an older answer or Fleek has not said). */
    state: "payment_pending" | "past_due" | "payment_failed" | "cancelled" | "trial_ended" | "lapsed" | "trial" | "active" | null;
    /** past_due only: full access until this date. */
    grace_until: string | null;
    /** The shop started this plan itself and holds no paid access: it may start again, or go back to Free. */
    can_retry: boolean;
    free: boolean;
  };
  /** Free plan only: days of invoice-PDF download left in the first 7 days. */
  pdf_days_left: number | null;
  can_manage: boolean;
}

export const PLANS_KEY = ["plans"] as const;

export function usePlans(enabled = true) {
  return useQuery({
    queryKey: PLANS_KEY,
    queryFn: () => api<PlansOut>("/plans"),
    enabled,
    staleTime: 60_000,
    retry: false,
  });
}

/** The signed-out ladder for the landing page. */
export function usePublicPlans() {
  return useQuery({
    queryKey: ["plans-public"],
    queryFn: () => api<{ plans: Plan[] }>("/plans/public", { auth: false }),
    staleTime: 5 * 60_000,
    retry: false,
  });
}

/** "₹1,499" */
export const rupees = (n: number): string => `₹${Math.round(n).toLocaleString("en-IN")}`;

/** The cheapest paid plan that switches `key` on, so a locked screen can say what it costs. */
export function cheapestFor(key: FeatureKey, plans: Plan[] | undefined): Plan | null {
  const k = key === ("tally" as FeatureKey) ? "tally_backup" : key;
  const paid = (plans ?? []).filter((p) => p.price_amount > 0 && p.unlocks.includes(k));
  return paid.length ? paid.reduce((a, b) => (b.price_amount < a.price_amount ? b : a)) : null;
}

/** The features a plan page compares, in the order shown. */
export const COMPARE_ROWS: FeatureKey[] = [
  "pdf_out",
  "whatsapp",
  "orders",
  "reminders",
  "catalog_parse",
  "inward_prices",
  "import",
  "slip",
  "voice",
  "tally_backup",
  "tally_full",
];

export const featureLabel = (k: FeatureKey): string => FEATURE_NAME[k] ?? k;
