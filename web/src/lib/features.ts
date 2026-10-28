import { useAuth } from "./auth";
import type { FeatureKey } from "./types";

/** May this firm use a plan feature. A feature the server did not mention is allowed (older server, or a firm
 *  that is not on the Free plan). The server enforces the same rule; this only decides what the screen shows. */
export function useFeature(key: FeatureKey): boolean {
  const { me } = useAuth();
  return me?.features?.[key] !== false;
}

/** Whether the firm is on the Free plan. */
export function useIsFree(): boolean {
  const { me } = useAuth();
  return me?.plan_tier === "free";
}

/** Plain-language names, shared by the lock screen and Setup. */
export const FEATURE_NAME: Record<FeatureKey, string> = {
  import: "Importing parties and items",
  slip: "Creating an invoice from a photo",
  voice: "Creating an invoice by speaking",
  collections: "Collections and ageing",
  reminders: "Payment reminders",
  whatsapp: "Sending from your own WhatsApp number",
  tally: "The Tally connection",
};
