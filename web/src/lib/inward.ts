// Inward Bill Import — API types (ext_inward_import).

export type InwardStatus =
  | "uploaded"
  | "extracting"
  | "needs_review"
  | "approved"
  | "rejected"
  | "error";

export type ExtractionMethod = "einvoice_qr" | "template" | "table" | "vision_llm";
export type MatchMethod = "exact" | "alias" | "fuzzy" | "llm" | "new" | "manual";
export type SupplyType = "intra" | "inter";

export interface InwardBillListItem {
  id: string;
  source_filename: string;
  supplier_name: string | null;
  supplier_gstin: string | null;
  bill_no: string | null;
  bill_date: string | null;
  grand_total: string | null;
  status: InwardStatus;
  reconciled: boolean | null;
  extraction_method: ExtractionMethod | null;
  extraction_confidence: string | null;
  created_at: string;
}

export interface InwardLine {
  id: string;
  sl_no: number;
  description: string;
  hsn: string | null;
  quantity: string | null;
  uom: string | null;
  unit_rate: string | null;
  discount_pct: string | null;
  taxable_value: string | null;
  cgst_rate: string | null;
  cgst_amt: string | null;
  sgst_rate: string | null;
  sgst_amt: string | null;
  igst_rate: string | null;
  igst_amt: string | null;
  line_total: string | null;
  match_method: MatchMethod | null;
  match_confidence: string | null;
  matched_item_id: string | null;
  new_item_staged_json: Record<string, unknown> | null;
  review_flag: string | null;
}

export interface Reconciliation {
  reconciled: boolean | null;
  discrepancy: string | null;
  taxable_total: string | null;
  cgst_total: string | null;
  sgst_total: string | null;
  igst_total: string | null;
  round_off: string | null;
  grand_total: string | null;
}

export interface SupplierBlock {
  matched_party_id: string | null;
  matched_party_name: string | null;
  staged: Record<string, unknown> | null;
  supply_type: SupplyType | null;
  place_of_supply_state_code: string | null;
}

export interface InwardBill {
  id: string;
  source_filename: string;
  status: InwardStatus;
  bill_no: string | null;
  bill_date: string | null;
  sales_order_ref: string | null;
  amount_in_words: string | null;
  extraction_method: ExtractionMethod | null;
  extraction_confidence: string | null;
  error_message: string | null;
  reject_reason: string | null;
  tally_xml_path: string | null;
  created_at: string;
  supplier: SupplierBlock;
  reconciliation: Reconciliation;
  lines: InwardLine[];
  approve_blockers: string[];
}

export interface ApproveResult {
  status: InwardStatus;
  created_supplier_id: string | null;
  promoted_party_id: string | null;
  created_item_ids: string[];
  linked_line_count: number;
  xml_download_url: string;
}

export interface LedgerConfig {
  creditors_group: string;
  purchase_ledger: string;
  cgst_ledger: string;
  sgst_ledger: string;
  igst_ledger: string;
  round_off_ledger: string;
  xml_encoding: string;
}
