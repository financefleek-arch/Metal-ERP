export type UserRole = "owner" | "accountant" | "viewer" | "counter" | "weighbridge" | "rate_desk";
export type PartyRole = "customer" | "supplier" | "both";
export type AddressType = "bill" | "ship" | "both";

export interface Me {
  id: string;
  email: string;
  role: UserRole;
  tenant_id: string;
  /** ext_inward_import — gates the Inward nav item and its routes. */
  ext_inward_import: boolean;
}

export interface Tenant {
  id: string;
  legal_name: string;
  trade_name: string | null;
  pan: string | null;
  address: string | null;
  city: string | null;
  state_code: string | null;
  pincode: string | null;
  phone: string | null;
  email: string | null;
  bank_holder: string | null;
  bank_name: string | null;
  bank_ac_no: string | null;
  bank_ifsc: string | null;
  bank_branch: string | null;
  upi_id: string | null;
  declaration_text: string | null;
  terms_text: string | null;
  jurisdiction_text: string | null;
  document_label: string;
  gst_enabled: boolean;
  gstin: string | null;
}

export interface PartyAddress {
  id?: string;
  type: AddressType;
  line1: string | null;
  line2: string | null;
  line3: string | null;
  city: string | null;
  state_code: string | null;
  pincode: string | null;
  is_default: boolean;
}

export type PartyStatus = "active" | "archived";
export type PartySource = "manual" | "inward_bill" | "tally_import";

export interface PartyCompleteness {
  complete: boolean;
  missing: string[];
}

export interface PartyListItem {
  id: string;
  legal_name: string;
  role: PartyRole;
  phone: string | null;
  default_state_code: string | null;
  gstin: string | null;
  status: PartyStatus;
  source: PartySource;
  source_ref: string | null;
  last_txn_at: string | null;
  completeness: PartyCompleteness;
}

export interface Party extends PartyListItem {
  email: string | null;
  pan: string | null;
  addresses: PartyAddress[];
  document_count: number;
}

// --- Tally party import ---

export type ImportOutcome = "new" | "link" | "flag" | "skip";

export interface ImportGroup {
  name: string;
  ledger_count: number;
  always: boolean;
  implied_role: PartyRole | null;
}

export interface ImportBatch {
  batch_id: string;
  total: number;
  groups: ImportGroup[];
}

export interface ImportFlag {
  code: string;
  message: string;
}

export interface StagedRow {
  id: string;
  ledger_name: string;
  parent_group: string | null;
  gstin: string | null;
  pan: string | null;
  outcome: ImportOutcome;
  proposed_role: PartyRole;
  role: PartyRole;
  match_method: string;
  match_party_id: string | null;
  match_party_name: string | null;
  decision: string;
  edited_name: string | null;
  flags: ImportFlag[];
  missing: string[];
}

export interface ImportReview {
  batch_id: string;
  counts: Record<ImportOutcome, number>;
  rows: StagedRow[];
}

export interface ImportCommitResult {
  created: number;
  updated: number;
  skipped: number;
  still_flagged: number;
}
