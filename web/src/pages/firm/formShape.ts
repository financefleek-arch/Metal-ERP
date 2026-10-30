import type { GroupCreatePolicy } from "../../lib/catalog";

/** Every box on the Firm page. `shop_name` is the one-name field simple-bill shops see. */
export interface FormShape {
  shop_name: string;
  legal_name: string;
  trade_name: string;
  phone: string;
  pincode: string;
  city: string;
  address: string;
  gstin: string;
  upi_id: string;
  upi_qr_payload: string;
  bank_holder: string;
  bank_ac_no: string;
  /** typed again, only asked when the number changed; never sent */
  bank_ac_confirm: string;
  bank_ifsc: string;
  bank_name: string;
  bank_branch: string;
  document_label: string;
  declaration_text: string;
  terms_text: string;
  jurisdiction_text: string;
  // payment reminders
  reminder_enabled: boolean;
  reminder_auto_send: boolean;
  reminder_days: string;
  default_credit_days: string;
  // customer catalog page and item codes
  order_min_value: string;
  order_terms_line: string;
  order_alert_phone: string;
  catalog_code_prefix: string;
  catalog_group_create_policy: GroupCreatePolicy;
}

export type Tab = "business" | "pay" | "wording" | "reminders" | "catalog" | "connections";

export const clean = (s: string | undefined) => (s ?? "").trim();
export const orNull = (s: string | undefined) => clean(s) || null;
