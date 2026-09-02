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

// --- items ---

export type ItemType = "bulk" | "mrp";
export type ItemStatus = "unconfirmed" | "confirmed" | "archived";
export type ItemSource = "manual" | "auto_from_invoice" | "auto_from_purchase" | "import";

export interface ItemListItem {
  id: string;
  name: string;
  item_type: ItemType;
  category: string | null;
  rate_mode: RateMode;
  uom: string | null;
  secondary_uom: string | null;
  hsn_code: string | null;
  metal: string | null;
  shape: string | null;
  grade: string | null;
  size_text: string | null;
  default_rate: string | null;
  last_rate: string | null;
  last_purchase_rate: string | null;
  last_sold_at: string | null;
  gst_rate: string | null;
  price_min: string | null;
  price_max: string | null;
  times_billed: number;
  status: ItemStatus;
  source: ItemSource;
}

export interface Item extends ItemListItem {
  name_normalized: string;
  thickness_mm: string | null;
  width_mm: string | null;
  length_mm: string | null;
  finish: string | null;
  conversion_factor: string | null;
  weight_per_uom: string | null;
  purchase_uom: string | null;
  mrp: string | null;
  default_discount_pct: string | null;
  last_purchased_at: string | null;
  merged_into_id: string | null;
  notes: string | null;
  rate_in_band: boolean | null;
  document_count: number;
}

export interface HsnOption {
  code: string;
  description: string;
  gst_rate: number | null;
}

// --- invoice line type-ahead: POST /api/items/resolve ---

export type ResolveMethod = "exact" | "alias" | "fuzzy" | null;

export interface ResolveCandidate extends ItemListItem {
  score: number;
}

export interface ResolveResult {
  method: ResolveMethod;
  confidence: number | null;
  weak: boolean;
  candidates: ResolveCandidate[];
}

// --- bulk operations: PATCH /api/items/bulk, POST /api/items/bulk-delete ---

/** Fields the bulk-edit sheet may set. Keep in lockstep with
 *  BULK_EDITABLE_FIELDS in api/app/schemas_item.py. */
export type BulkField =
  | "uom"
  | "purchase_uom"
  | "secondary_uom"
  | "default_discount_pct"
  | "default_rate"
  | "item_type"
  | "hsn_code"
  | "metal"
  | "shape"
  | "finish"
  | "category_id"
  | "group_id"
  | "status"
  | "notes";

export type BulkResult =
  | "changed"
  | "skipped"
  | "deleted"
  | "archived"
  | "blocked"
  | "error";

export interface BulkOutcome {
  id: string;
  name: string;
  result: BulkResult;
  detail: string | null;
}

export interface BulkUpdateResult {
  dry_run: boolean;
  changed: number;
  unchanged: number;
  errors: number;
  learned_rule_ids: string[];
  rows: BulkOutcome[];
}

export interface BulkDeleteResult {
  dry_run: boolean;
  deleted: number;
  archived: number;
  blocked: number;
  errors: number;
  rows: BulkOutcome[];
}

// --- catalogue hierarchy ---

export type RateMode = "piece" | "kg";

export interface ItemCategoryRow {
  id: string;
  name: string;
  sort: number;
  group_count: number;
  item_count: number;
}

export interface GroupOut {
  id: string;
  name: string;
  name_normalized: string;
  category_id: string | null;
  category_name: string | null;
  hsn_code: string | null;
  uom: string | null;
  item_type: ItemType;
  default_rate_mode: RateMode;
  item_count: number;
}

export interface GroupLeaf {
  id: string;
  size_pos: number | null;
  size_label: string | null;
  size_text: string | null;
  sku: string | null;
  rate_mode: RateMode;
  weight_per_piece: string | null;
  default_rate: string | null;
  last_rate: string | null;
  generated_name: string;
}

export interface GroupDetail extends GroupOut {
  leaves: GroupLeaf[];
}

export interface TreeLeaf {
  id: string;
  name: string;
  size_label: string | null;
  default_rate: string | null;
  status: ItemStatus;
}

export interface TreeGroup {
  id: string;
  name: string;
  item_type: ItemType;
  leaves: TreeLeaf[];
}

export interface TreeCategory {
  id: string | null;
  name: string;
  groups: TreeGroup[];
  loose: TreeLeaf[];
}

// --- Tally item import ---

export type ItemImportOutcome = "new" | "link" | "skip" | "flag";

export interface StockGroupCount {
  name: string;
  item_count: number;
}

export interface ItemImportBatch {
  batch_id: string;
  total: number;
  dummies_skipped: number;
  groups: StockGroupCount[];
}

export interface StagedItemRow {
  id: string;
  stock_name: string;
  parent_group: string | null;
  base_units: string | null;
  hsn: string | null;
  gst_rate: string | null;
  standard_rate: string | null;
  item_type: ItemType;
  rate_mode: RateMode;
  parsed: {
    metal: string | null;
    shape: string | null;
    grade: string | null;
    size_text: string | null;
    sku: string | null;
  };
  outcome: ItemImportOutcome;
  match_item_id: string | null;
  match_item_name: string | null;
  decision: string;
  edited_name: string | null;
  seed_hsn: boolean;
  flags: { code: string; message: string }[];
}

export interface ItemImportReview {
  batch_id: string;
  counts: Record<ItemImportOutcome, number>;
  rows: StagedItemRow[];
}

export interface ItemImportCommitResult {
  created: number;
  updated: number;
  skipped: number;
  still_flagged: number;
  hsn_seeded: number;
  groups_created: number;
}

// --- invoices ---

export type InvoiceStatus = "draft" | "final" | "cancelled";
export type PdfStatus = "none" | "rendered" | "failed";

export interface InvoiceLineIn {
  item_id: string | null;
  group_id: string | null;
  description: string;
  hsn_code: string | null;
  quantity: string;
  uom: string | null;
  unit_rate: string;
  discount: string;
  size_pos: number | null;
  segment_no: number;
}

export interface InvoiceLineOut {
  id: string;
  sl_no: number;
  item_id: string | null;
  description: string;
  hsn_code: string | null;
  quantity: string;
  uom: string | null;
  unit_rate: string;
  discount: string;
  line_total: string | null;
  segment_no: number;
}

export interface InvoiceTotals {
  subtotal: string;
  discount_total: string;
  taxable_total: string;
  round_off: string;
  grand_total: string;
  amount_in_words: string;
}

export interface WeighmentSlipIn {
  seg: number;
  recorded_kg: string;
}

export interface SegmentMeasure {
  seg: number;
  line_from: number;
  line_to: number;
  weight_kg: string;
  count: number;
  recorded_kg: string | null;
}

export interface InvoiceMeasure {
  total_weight_kg: string;
  total_count: number;
  segment_count: number;
  segments: SegmentMeasure[];
}

export interface PartyBrief {
  id: string;
  legal_name: string;
  gstin: string | null;
  pan: string | null;
  default_state_code: string | null;
}

export interface Invoice {
  id: string;
  doc_type: string;
  series: string;
  number: number | null;
  fy: string;
  date: string;
  status: InvoiceStatus;
  template_version: string;
  party_id: string;
  party: PartyBrief | null;
  bill_to_addr_id: string | null;
  ship_to_addr_id: string | null;
  notes: string | null;
  terms_snapshot: string | null;
  declaration_snapshot: string | null;
  invoice_discount: string;
  totals: InvoiceTotals;
  measure: InvoiceMeasure;
  pdf_status: PdfStatus;
  has_pdf: boolean;
  lines: InvoiceLineOut[];
  finalize_blockers: string[];
  created_at: string;
  updated_at: string;
}

export interface InvoiceListItem {
  id: string;
  number: number | null;
  fy: string;
  date: string;
  status: InvoiceStatus;
  party_id: string;
  party_name: string;
  grand_total: string | null;
  pdf_status: PdfStatus;
}

export interface FinalizeResult {
  id: string;
  number: number;
  fy: string;
  status: InvoiceStatus;
  totals: InvoiceTotals;
  measure: InvoiceMeasure;
  pdf_status: PdfStatus;
  created_item_ids: string[];
  learned_group_ids: string[];
}

/** Result of POST /api/items/resolve — drives the line type-ahead. */
export interface ItemResolveResult {
  item_id: string | null;
  method: string | null;
  confidence: number | null;
  weak: boolean;
  candidates: { item_id: string; name: string; score: number }[];
}
