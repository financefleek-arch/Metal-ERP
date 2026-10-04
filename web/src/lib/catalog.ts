// Supplier Catalog: API types and helpers (ext_supplier_catalog).

export type CatalogStatus = "extracting" | "ready" | "error";

export interface CatalogListItem {
  id: string;
  title: string;
  source_filename: string;
  page_count: number;
  item_count: number;
  /** Prefix for products that have no group (the firm's "ungrouped" prefix). */
  code_prefix: string;
  supplier_party_id: string | null;
  supplier_name: string | null;
  /** Bulk margin in percent as a string, e.g. "25.00" (= +25%). */
  bulk_margin_pct: string;
  rounding_step: number;
  status: CatalogStatus;
  created_at: string;
}

export interface CatalogDetail extends CatalogListItem {
  /** Items that carry their own (item-level) margin instead of the bulk margin. */
  item_margin_count: number;
}

export interface CatalogUploadOut extends CatalogListItem {
  /** True when this exact file was uploaded before and the existing catalog is returned. */
  already_exists: boolean;
  /** Photos with no price line (blank filler cells) that were left out. */
  skipped_cells: number;
  warnings: string[];
  /** Rows that reused a product (code, group, name) from an earlier catalog of this supplier. */
  matched_items: number;
  new_products: number;
  /** Rows that look like a product you already have: confirm or dismiss. */
  suggestions: number;
}

export interface ProductRef {
  product_id: string;
  code: string;
  name: string;
}

export interface CatalogItem {
  id: string;
  page_no: number;
  position: number;
  /** Our code: the group's code and a number, e.g. BM-0042. */
  code: string;
  supplier_code: string;
  display_name: string;
  name_raw: string;
  brand: string | null;
  size_text: string | null;
  pack_qty: number;
  carton_qty: number | null;
  /** Decimals arrive as strings. Price is per pack, as the supplier quotes it. */
  cost_price: string;
  /** The item's own margin in percent, or null when it uses the bulk margin. */
  item_margin_pct: string | null;
  sell_price: string;
  category_id: string | null;
  category_name: string | null;
  /** Set when the policy is "suggest only" and the group does not exist yet. */
  suggested_group: string | null;
  included: boolean;
  /** Signed, cacheable URL. Works in an <img> tag (no bearer header needed). */
  image_url: string | null;
  /** Code 128 bar pattern for `code` ('1' = bar, '0' = space); draw it with <Barcode>. */
  barcode: string | null;
  product_id: string | null;
  /** True once the code was used (label, customer catalog, item, Tally): it never changes. */
  code_locked: boolean;
  /** Another product that looks like the same thing: accept or dismiss. */
  suggestion: ProductRef | null;
  item_id: string | null;
  tally_status: string;
}

export interface GroupSummary {
  category_id: string | null;
  name: string;
  code_prefix: string | null;
  item_count: number;
  included_count: number;
}

export interface ItemFilter {
  q?: string;
  category_id?: string;
  no_group?: boolean;
  included?: boolean;
  brand?: string;
  /** true = only items with their own (item-level) margin; false = only those on the bulk margin. */
  has_item_margin?: boolean;
}

export type ItemPatch = Partial<{
  display_name: string;
  brand: string | null;
  size_text: string | null;
  pack_qty: number;
  cost_price: string;
  included: boolean;
  /** null or blank clears the group; a new name creates it. */
  group_name: string | null;
  /** A number sets this item's own margin (%); null clears it. */
  item_margin_pct: string | null;
}>;

export type BulkChanges = {
  group_name?: string | null;
  included?: boolean;
  item_margin_pct?: string | null;
};

export type RoundingStep = 1 | 5 | 10;

export type BulkTarget = { ids: string[] } | { filter: ItemFilter };

export type GroupCreatePolicy = "auto" | "suggest_only";

export const PAGE_SIZE = 100;

/** Query string for GET /supplier-catalogs/{id}/items. */
export function itemsQuery(filter: ItemFilter, cursor: string | null): string {
  const p = new URLSearchParams();
  p.set("limit", String(PAGE_SIZE));
  if (filter.q) p.set("q", filter.q);
  if (filter.no_group) p.set("no_group", "true");
  else if (filter.category_id) p.set("category_id", filter.category_id);
  if (filter.included !== undefined) p.set("included", String(filter.included));
  if (filter.brand) p.set("brand", filter.brand);
  if (filter.has_item_margin !== undefined) p.set("has_item_margin", String(filter.has_item_margin));
  if (cursor) p.set("cursor", cursor);
  return p.toString();
}

/** Matches the server: 2-8 letters or digits, stored upper-case. */
export function validCodePrefix(v: string): boolean {
  return /^[A-Za-z0-9]{2,8}$/.test(v.trim());
}

/** Same rule as the server: -99.99 to 1000 percent, at most 2 decimals. */
export function validMargin(v: string): boolean {
  const s = v.trim();
  if (!/^[+-]?\d{1,4}(\.\d{1,2})?$/.test(s)) return false;
  const n = Number(s);
  return n >= -99.99 && n <= 1000;
}

/** "25.00" -> "25", "12.50" -> "12.5", "-10.00" -> "-10". */
export function trimMargin(v: string): string {
  const n = Number(v);
  return Number.isNaN(n) ? v : String(n);
}

/** What a margin does to a Rs 100 supplier price: 25 -> "Rs 100 -> Rs 125". */
export function marginExample(pct: number): string {
  if (!Number.isFinite(pct) || pct <= -100) return "";
  const price = Math.round(100 * (100 + pct)) / 100;
  return `₹100 → ₹${price}`;
}

/** "+25%", "-10%", "0%". */
export function marginLabel(v: string | number): string {
  const n = typeof v === "string" ? Number(v) : v;
  if (!Number.isFinite(n)) return "";
  const r = Math.round(n * 100) / 100;
  return `${r > 0 ? "+" : ""}${r}%`;
}

/** "Pack of 6" / "Single piece": the unit the supplier's price is for. */
export function packLabel(packQty: number): string {
  return packQty === 1 ? "Single piece" : `Pack of ${packQty}`;
}

/** Indian digit grouping, no currency symbol. "1099.50" -> "1,099.50". */
export function money(v: string | number): string {
  const n = typeof v === "string" ? Number(v) : v;
  if (Number.isNaN(n)) return String(v);
  return n.toLocaleString("en-IN", { minimumFractionDigits: 2, maximumFractionDigits: 2 });
}

// --------------------------------------------------------------------------
// barcode labels
// --------------------------------------------------------------------------

export type LabelPreset = "roll_50x25" | "roll_38x25" | "sheet_a4_3x8";

export const LABEL_PRESETS: {
  key: LabelPreset;
  name: string;
  detail: string;
  perPage: number;
}[] = [
  { key: "roll_50x25", name: "Roll 50 × 25 mm", detail: "One label per page", perPage: 1 },
  { key: "roll_38x25", name: "Roll 38 × 25 mm", detail: "One label per page", perPage: 1 },
  { key: "sheet_a4_3x8", name: "A4 sheet, 3 × 8", detail: "24 labels of 70 × 37 mm", perPage: 24 },
];

export interface LabelOptions {
  preset: LabelPreset;
  copies: number;
  show_name: boolean;
  show_code: boolean;
  show_price: boolean;
  /** First slot used on the first sheet (A4 preset only). */
  start_at: number;
}

export type LabelRequest = LabelOptions & {
  ids?: string[];
  filter?: ItemFilter;
  all_included?: boolean;
};

export interface OutputJob {
  id: string;
  status: "queued" | "running" | "done" | "error";
  progress: number;
  total: number;
  page_count: number | null;
  scan_warning: boolean;
  error: string | null;
  /** For a customer-catalog job: the version it created once `status` is "done". */
  result_id: string | null;
}

/** Pages needed for `labels` labels. Mirrors the server. */
export function labelPages(labels: number, preset: LabelPreset, startAt: number): number {
  const per = LABEL_PRESETS.find((p) => p.key === preset)?.perPage ?? 1;
  if (per === 1) return labels;
  const skip = (startAt - 1) % per;
  return Math.ceil((skip + labels) / per);
}

/** Runs of bars in a Code 128 pattern: [start, length] pairs. */
export function barcodeRuns(pattern: string): [number, number][] {
  const out: [number, number][] = [];
  let i = 0;
  while (i < pattern.length) {
    if (pattern[i] === "1") {
      let j = i;
      while (j < pattern.length && pattern[j] === "1") j++;
      out.push([i, j - i]);
      i = j;
    } else i++;
  }
  return out;
}

// --------------------------------------------------------------------------
// customer catalogs
// --------------------------------------------------------------------------

export type CatalogColumns = 2 | 3 | 4;
export type GroupBy = "group" | "none";
export type PriceBasis = "pack" | "piece";

export interface CustomerCatalogOptions {
  columns: CatalogColumns;
  group_by: GroupBy;
  show_code: boolean;
  price_basis: PriceBasis;
  /** A contents page (needs grouping and at least two groups). */
  contents: boolean;
  /** Cover title for customers; blank = the catalog's name. */
  title: string;
}

export type CustomerCatalogRequest = Omit<CustomerCatalogOptions, "title"> & {
  title?: string;
  ids?: string[];
  filter?: ItemFilter;
  all_included?: boolean;
};

export interface CustomerCatalog {
  id: string;
  version: number;
  title: string;
  options: Record<string, unknown>;
  item_count: number;
  page_count: number;
  byte_size: number;
  /** Prices or items changed after this version was made: make a new one. */
  stale: boolean;
  created_at: string;
}

export const COLUMN_CHOICES: { value: CatalogColumns; name: string; detail: string }[] = [
  { value: 2, name: "2 across", detail: "Large photos, about 6 per page" },
  { value: 3, name: "3 across", detail: "Balanced, about 12 per page" },
  { value: 4, name: "4 across", detail: "Compact, about 20 per page" },
];

/** Human file size: "1.2 MB". */
export function fileSize(bytes: number): string {
  if (bytes < 1024 * 1024) return `${Math.max(1, Math.round(bytes / 1024))} KB`;
  return `${(bytes / (1024 * 1024)).toFixed(1)} MB`;
}

// --- S5: promote to items, push to Tally ---

export interface PromoteOut {
  total: number;
  create: number;
  link_existing: number;
  reuse: number;
  already: number;
  /** Created with the code appended because another product has the same name. */
  renamed: number;
  examples: string[];
}

export interface TallySettings {
  connected: boolean;
  company_name: string | null;
  stock_group_root: string | null;
  tally_group_create_policy: "create_missing" | "existing_only";
  /** {item_category_id: Tally stock group name} */
  stock_group_map: Record<string, string>;
  checked_at: string | null;
  check_is_fresh: boolean;
  known_groups: string[];
}

export interface TallyCheck {
  id: string;
  status: "queued" | "sent" | "running" | "ok" | "error";
  error: string | null;
  groups: number | null;
  items: number | null;
  linked: number | null;
  created_at: string;
}

export interface PreflightCheck {
  code: string;
  ok: boolean;
  message: string;
  blocking: boolean;
}

export interface PreflightGroup {
  category_id: string | null;
  our_name: string;
  tally_name: string;
  status: "existing" | "create" | "missing";
  item_count: number;
}

export interface TallyPreflight {
  ok: boolean;
  total: number;
  to_push: number;
  already_synced: number;
  skipped_existing: number;
  not_promoted: number;
  batches: number;
  root: string | null;
  checked_at: string | null;
  checks: PreflightCheck[];
  groups: PreflightGroup[];
  collisions: { item_id: string; code: string; name: string }[];
}

export interface TallyRun {
  run_id: string | null;
  state: "none" | "running" | "done" | "error" | "stopped";
  batches: number;
  batches_done: number;
  total_items: number;
  synced: number;
  error: string | null;
  updated_at: string | null;
  /** while running: Tally is not ready and the batch is being retried */
  waiting?: "tally_unavailable" | "no_company_loaded" | null;
}
