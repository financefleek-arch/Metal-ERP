import { api } from "./api";

/** Tell the server a friction or demand moment happened (see docs/PLAN-shop-activity-intelligence.md). Keys are
 *  whitelisted on the server; props are a few short labels, never names, amounts or free text. Never throws,
 *  never waits: analytics must not get in the way of the app. */
export function beacon(key: string, props?: Record<string, string | number | boolean>): void {
  void api<void>("/onboarding/activity", { method: "POST", body: { key, props } }).catch(() => {
    /* dropped on purpose */
  });
}
