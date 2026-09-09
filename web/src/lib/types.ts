export type UserRole = "owner" | "accountant" | "viewer" | "counter" | "weighbridge" | "rate_desk";
export type PartyRole = "customer" | "supplier" | "both";
export type AddressType = "bill" | "ship" | "both";

export interface Me {
  id: string;
  email: string;
  role: UserRole;
  tenant_id: string;
  /** Platform operator — the SPA swaps the whole app for the admin console. */
  is_platform_admin: boolean;
  /** ext_inward_import — gates the Inward nav item and its routes. */
  ext_inward_import: boolean;
}

// --- platform admin (/api/admin/*) ---

/** Roles the operator may assign to a firm user. */
export type AssignableRole = "owner" | "accountant" | "viewer";

export interface AdminUser {
  id: string;
  email: string;
  role: UserRole;
  is_active: boolean;
  is_platform_admin: boolean;
  created_at: string;
}

export interface FirmListItem {
  id: string;
  legal_name: string;
  city: string | null;
  gst_enabled: boolean;
  ext_inward_import: boolean;
  user_count: number;
  active_user_count: number;
  created_at: string;
}

export interface FirmDetail {
  id: string;
  legal_name: string;
  city: string | null;
  gst_enabled: boolean;
  ext_inward_import: boolean;
  created_at: string;
  users: AdminUser[];
}

/** Per-firm WhatsApp number registry (GET/PUT /api/admin/firms/{id}/whatsapp).
 *  There is no token here — sends use the process-wide FleekWA System User
 *  token; this row only picks which number the firm sends from. */
export interface FirmWhatsapp {
  configured: boolean;
  is_active: boolean;
  phone_number_id: string | null;
  waba_id: string | null;
  display_phone_number: string | null;
  updated_at: string | null;
}

export interface FirmWhatsappUpsert {
  phone_number_id: string;
  waba_id: string;
  display_phone_number?: string | null;
  is_active: boolean;
}

/** Result of POST /api/admin/firms/{id}/whatsapp/test — a dummy-data send to
 *  prove the firm's number + token + approved template line up. */
export interface FirmWhatsappTestResult {
  id: string;
  status: string;
  template_name: string;
  to_phone: string;
  wa_message_id: string | null;
  error: string | null;
}

// -------------------------------------------------------------------------
// Tally Connector (F1a) — /api/admin/firms/{id}/tally/*
// -------------------------------------------------------------------------

/** connected | refused | no_company | unknown | null (never reported) */
export type TallyReachabilityStatus = "connected" | "refused" | "no_company" | "unknown" | null;

/** This firm's companion-agent identity + live health
 *  (GET /admin/firms/{id}/tally-shop, or the firm's own GET /tally/agent-status).
 *  The key it authenticates with is never in this response — it's baked
 *  straight into the downloadable installer, never shown to a human. */
export interface FirmTallyShop {
  provisioned: boolean;
  shop_id: string | null;
  is_active: boolean;
  last_checkin_at: string | null;
  last_upload_at: string | null;
  installer_ready: boolean;
  agent_online: boolean;
  tally_status: TallyReachabilityStatus;
  tally_ok_at: string | null;
}

/** Response of provision / rotate-key. The plaintext key is no longer
 *  echoed back — it's baked into the cached installer zip instead. */
export interface FirmTallyShopProvisionResult {
  shop_id: string;
  created: boolean;
  installer_ready: boolean;
}

export interface KnownLedger {
  name: string;
  parent: string | null;
  kind: "ledger" | "group";
}

export interface TallyCompany {
  id: string;
  tenant_id: string;
  company_name: string;
  base_currency: string;
  transport: string;
  shop_id: string | null;
  ledger_map: Record<string, string>;
  known_ledgers: KnownLedger[] | null;
  last_masters_pull_at: string | null;
  created_at: string;
  updated_at: string;
}

export interface TallyCompanyUpsert {
  company_name: string;
}

/** Only the keys present are written; "" clears that slot. */
export type LedgerMapPatch = Partial<
  Record<
    | "sales_ledger"
    | "cash_ledger"
    | "bank_ledger"
    | "round_off_ledger"
    | "cgst_ledger"
    | "sgst_ledger"
    | "igst_ledger"
    | "debtors_parent"
    | "creditors_parent",
    string
  >
>;

export interface TallySyncJob {
  id: string;
  direction: string;
  kind: string;
  status: "queued" | "sent" | "running" | "ok" | "error";
  r2_key: string | null;
  batch_id: string | null;
  counts: {
    ledgers?: number;
    items?: number;
    dummies_skipped?: number;
  } | null;
  error: string | null;
  created_at: string;
  completed_at: string | null;
  // present on the detail endpoint; drives the "Waiting on Tally" step
  last_agent_status?: "no_company_loaded" | "tally_unavailable" | null;
  last_agent_status_at?: string | null;
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
  // Opening balance carried from before go-live (positive = party owes you).
  // `locked` is true once the party has a finalized invoice or any payment —
  // the field is then read-only (server rejects a change with 409).
  opening_balance: string;
  opening_balance_locked: boolean;
  completeness: PartyCompleteness;
}

export interface Party extends PartyListItem {
  email: string | null;
  pan: string | null;
  opening_balance_as_of: string | null;
  addresses: PartyAddress[];
  document_count: number;
}

// --- de-duplication: POST /api/parties/resolve + the structured 409 ---

export type PartyResolveMethod = "gstin" | "phone" | "exact" | "fuzzy" | null;

export interface PartyMatchRef {
  id: string;
  legal_name: string;
  gstin: string | null;
  phone: string | null;
  city: string | null;
  last_txn_at: string | null;
  status: string;
  score: number | null;
}

export interface PartyResolveResult {
  method: PartyResolveMethod;
  confidence: number | null;
  weak: boolean;
  candidates: PartyMatchRef[];
}

/** `detail` of the 409 that POST/PATCH /api/parties returns on a likely dup.
 *  - `party_exists`       GSTIN / phone / exact-name hit; `match` is set;
 *                         NOT bypassable (?force=true is ignored).
 *  - `party_maybe_exists` fuzzy / ambiguous; `candidates` set; re-send with
 *                         ?force=true after the operator confirms "new". */
export interface PartyDuplicate409 {
  code: "party_exists" | "party_maybe_exists";
  message: string;
  match: PartyMatchRef | null;
  candidates: PartyMatchRef[];
}

/** Narrow an `ApiError.detail` to a party-dedupe 409 body. */
export function isDup409(d: unknown): d is PartyDuplicate409 {
  return (
    !!d &&
    typeof d === "object" &&
    ((d as PartyDuplicate409).code === "party_exists" ||
      (d as PartyDuplicate409).code === "party_maybe_exists")
  );
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

export interface ImportCurrentBatch {
  batch_id: string | null;
  total: number;
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
  item_count: number;
}

export interface GroupLeaf {
  id: string;
  size_pos: number | null;
  size_label: string | null;
  size_text: string | null;
  sku: string | null;
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
  /** leaf count — leaves themselves come from /items/tree/leaves on expand */
  leaf_count: number;
}

export interface TreeCategory {
  id: string | null;
  name: string;
  groups: TreeGroup[];
  /** count of leaves in this category with no group */
  loose_count: number;
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

export interface ItemImportCurrentBatch {
  batch_id: string | null;
  total: number;
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
  /** persisted UI hint only — the original % the operator typed, if they
   *  entered the discount as a percentage rather than a ₹ amount. `discount`
   *  above (an absolute ₹) is always the value billing math uses. */
  discount_pct: string | null;
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
  discount_pct: string | null;
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
  phone: string | null;
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
  party_id: string | null;
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
  /** Only set once status === "final"; null on draft/cancelled. */
  paid_amount: string | null;
  balance_due: string | null;
  payment_status: InvoicePaymentStatus | null;
}

export type WhatsappStatus = "pending" | "sent" | "delivered" | "read" | "failed";

export interface InvoiceListItem {
  id: string;
  number: number | null;
  fy: string;
  date: string;
  status: InvoiceStatus;
  party_id: string | null;
  party_name: string;
  grand_total: string | null;
  pdf_status: PdfStatus;
  /** Only set once status === "final"; null on draft/cancelled. */
  payment_status: InvoicePaymentStatus | null;
  /** Latest whatsapp_message.status for this invoice; null if never sent. */
  whatsapp_status: WhatsappStatus | null;
}

export interface InvoiceWhatsappMessage {
  id: string;
  to_phone: string;
  template_name: string;
  status: WhatsappStatus;
  error: string | null;
  wa_message_id: string | null;
  sent_at: string | null;
  delivered_at: string | null;
  read_at: string | null;
  created_at: string;
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

// --- payments (party ledger, bill-wise allocation) ---

export type PaymentMode = "cash" | "upi" | "bank" | "cheque";
export type PaymentStatus = "posted" | "reversed";
export type AllocationType = "against_invoice" | "on_account";
export type InvoicePaymentStatus = "unpaid" | "partial" | "paid";

export interface PaymentAllocationIn {
  invoice_id: string | null;
  type: AllocationType;
  amount: string;
}

export interface PaymentCreate {
  party_id: string;
  date: string;
  amount: string;
  mode: PaymentMode;
  ref_no: string | null;
  notes: string | null;
  ledger_name: string | null;
  allocations: PaymentAllocationIn[];
}

export interface PaymentAllocationOut {
  id: string;
  invoice_id: string | null;
  invoice_number: number | null;
  type: AllocationType;
  amount: string;
}

export interface PaymentOut {
  id: string;
  party_id: string;
  party_name: string | null;
  date: string;
  amount: string;
  mode: PaymentMode;
  ref_no: string | null;
  notes: string | null;
  voucher_no: number | null;
  ledger_name: string | null;
  status: PaymentStatus;
  reversed_at: string | null;
  reversed_reason: string | null;
  allocations: PaymentAllocationOut[];
  created_at: string;
  updated_at: string;
}

/** One row in a party's open-invoices allocation table (payment dialog). */
export interface OpenInvoiceForAllocation {
  invoice_id: string;
  number: number;
  date: string;
  grand_total: string;
  balance_due: string;
  days_old: number;
}

/** One allocation row nested inside a payment's PartyLedgerEntry — raw
 *  shape (no invoice number join), unlike PaymentAllocationOut. */
export interface LedgerAllocation {
  invoice_id: string | null;
  type: AllocationType;
  amount: string;
}

/** One row in a party's running statement (Account tab) — newest first,
 *  running_balance computed server-side walking oldest-to-newest. */
export interface PartyLedgerEntry {
  kind: "invoice" | "payment" | "opening";
  date: string;
  ref_id: string;
  /** "INV #123" / "INV (draft)" / "PMT #45" */
  ref_label: string;
  debit: string;
  credit: string;
  running_balance: string;
  /** invoice: draft/final/cancelled. payment: posted/reversed. */
  status: string;
  /** only set for kind === "payment" */
  allocations: LedgerAllocation[] | null;
}

/** One row in the Collections list — parties with outstanding balance > 0. */
export type CollectionsScope = "outstanding" | "overpaid" | "either";

export interface CollectionsRow {
  party_id: string;
  legal_name: string;
  phone: string | null;
  /** positive = party owes us; negative = we owe the party (on-account
   *  credit exceeds what's billed). Never zero — settled parties don't appear. */
  outstanding_balance: string;
  oldest_unpaid_days: number | null;
  open_invoice_count: number;
}

/** Ageing bucket key. UI labels: lt30 "<30 days", d30 "30+", d60 "60+",
 *  d90p ">3 months". Mutually exclusive — a party's balance is split across
 *  them and they sum to `total`. */
export type AgeingBucket = "lt30" | "d30" | "d60" | "d90p";

/** One row in the Collections ageing dashboard (F3a). Only parties with a
 *  positive net (money owed to us) appear. */
export interface AgeingRow {
  party_id: string;
  legal_name: string;
  phone: string | null;
  lt30: string;
  d30: string;
  d60: string;
  d90p: string;
  total: string;
  worst_bucket: AgeingBucket;
  /** worst_bucket !== "lt30" — powers the Overdue scope chip. */
  is_overdue: boolean;
  oldest_bill_date: string | null;
  oldest_bill_number: number | null;
  last_payment_date: string | null;
  open_invoice_count: number;
}
