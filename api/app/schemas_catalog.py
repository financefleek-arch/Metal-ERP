"""Supplier Catalog API schemas."""

from __future__ import annotations

from datetime import datetime
from decimal import Decimal
from typing import Annotated, Literal

from pydantic import BaseModel, ConfigDict, Field, PlainSerializer

# Always serialised with fixed decimals, so the JSON is the same on every database
# (SQLite drops trailing zeros; Postgres NUMERIC keeps them).
Money = Annotated[Decimal, PlainSerializer(lambda v: f"{v:.2f}", return_type=str)]
MarginOut = Annotated[Decimal, PlainSerializer(lambda v: f"{v:.2f}", return_type=str)]
MarginOrNone = Annotated[
    Decimal | None,
    PlainSerializer(lambda v: None if v is None else f"{v:.2f}", return_type=str | None),
]


class CatalogListItem(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: str
    title: str
    source_filename: str
    page_count: int
    item_count: int
    code_prefix: str
    supplier_party_id: str | None = None
    supplier_name: str | None = None
    bulk_margin_pct: MarginOut
    rounding_step: int
    status: str
    created_at: datetime


class CatalogDetail(CatalogListItem):
    # Items that carry their own margin instead of the bulk margin.
    item_margin_count: int = 0


class CatalogUploadOut(CatalogListItem):
    # True when this exact file was uploaded before and the existing catalog is returned.
    already_exists: bool = False
    # Photos with no price line (blank filler cells) that were left out.
    skipped_cells: int = 0
    warnings: list[str] = []
    # Rows that reused a product (code, group, name) from an earlier catalog of this supplier.
    matched_items: int = 0
    new_products: int = 0
    # Rows that look like a product you already have (same name): confirm or dismiss.
    suggestions: int = 0


# A margin in percent, at most 2 decimals: -99.99 (a discount) up to 1000. 25 = +25%.
Margin = Annotated[
    Decimal | None,
    Field(ge=Decimal("-99.99"), le=Decimal("1000"), decimal_places=2),
]


class CatalogPatch(BaseModel):
    """Changing `bulk_margin_pct` or `rounding_step` reprices every item. `supplier_party_id`
    can be set once, on a catalog uploaded without a supplier."""

    title: str | None = Field(default=None, min_length=1, max_length=200)
    supplier_party_id: str | None = None
    bulk_margin_pct: Margin = None
    rounding_step: Literal[1, 5, 10] | None = None


class ProductRef(BaseModel):
    product_id: str
    code: str
    name: str


class CatalogItemOut(BaseModel):
    id: str
    page_no: int
    position: int
    code: str
    supplier_code: str
    display_name: str
    name_raw: str
    brand: str | None
    size_text: str | None
    pack_qty: int
    carton_qty: int | None
    cost_price: Money
    item_margin_pct: MarginOrNone
    sell_price: Money
    category_id: str | None
    category_name: str | None
    suggested_group: str | None
    included: bool
    image_url: str | None
    # Code 128 bar pattern for `code` ('1' = bar, '0' = space), drawn as SVG in the browser.
    barcode: str | None
    # The product this row is an offer of; it owns the code. A locked code never changes.
    product_id: str | None
    code_locked: bool
    # Another product that looks like the same thing (same name): accept or dismiss.
    suggestion: ProductRef | None
    item_id: str | None
    tally_status: str


class CatalogItemPatch(BaseModel):
    """Only the fields present in the request are changed. `group_name` null or
    blank clears the group; a new name creates the group.
    """

    display_name: str | None = Field(default=None, min_length=1, max_length=300)
    brand: str | None = Field(default=None, max_length=40)
    size_text: str | None = Field(default=None, max_length=60)
    pack_qty: int | None = Field(default=None, ge=1, le=10000)
    cost_price: Decimal | None = Field(default=None, ge=0, le=Decimal("9999999999999.99"))
    included: bool | None = None
    group_name: str | None = Field(default=None, max_length=120)
    # A number sets this item's own margin (%); null clears it (back to the bulk margin).
    item_margin_pct: Margin = None


class ItemFilter(BaseModel):
    q: str | None = None
    category_id: str | None = None
    no_group: bool = False
    included: bool | None = None
    brand: str | None = None
    # True = only items with their own (item-level) margin; False = only those on the bulk margin.
    has_item_margin: bool | None = None


class BulkChanges(BaseModel):
    group_name: str | None = Field(default=None, max_length=120)
    included: bool | None = None
    # A number sets the item-level margin (%) on every target item; null clears it.
    item_margin_pct: Margin = None


class BulkPatch(BaseModel):
    """Apply `changes` to the listed `ids` or to everything matching `filter`.
    Exactly one of the two is required, so an empty request never touches the
    whole catalog by accident.
    """

    ids: list[str] | None = Field(default=None, max_length=5000)
    filter: ItemFilter | None = None
    changes: BulkChanges


class BulkResult(BaseModel):
    updated: int


class GroupSummary(BaseModel):
    category_id: str | None
    name: str
    code_prefix: str | None = None
    item_count: int
    included_count: int


# --------------------------------------------------------------------------
# labels
# --------------------------------------------------------------------------

LabelPreset = Literal["roll_50x25", "roll_38x25", "sheet_a4_3x8"]


class LabelOptionsIn(BaseModel):
    preset: LabelPreset = "roll_50x25"
    copies: int = Field(default=1, ge=1, le=99)
    show_name: bool = True
    show_code: bool = True
    show_price: bool = False
    # First slot used on the first sheet (A4 preset only): reuse a partly printed sheet.
    start_at: int = Field(default=1, ge=1, le=24)


class LabelRequest(LabelOptionsIn):
    """Which items to print: exactly one of `ids`, `filter`, or `all_included`."""

    ids: list[str] | None = Field(default=None, max_length=5000)
    filter: ItemFilter | None = None
    all_included: bool = False


class OutputJobOut(BaseModel):
    id: str
    status: str  # queued | running | done | error
    progress: int
    total: int
    page_count: int | None
    scan_warning: bool
    error: str | None
    # For a customer-catalog job: the version it created once `status` is 'done'.
    result_id: str | None = None


# --------------------------------------------------------------------------
# customer catalogs
# --------------------------------------------------------------------------


class CustomerCatalogRequest(BaseModel):
    """Which items go in, and how. Choose exactly one of `ids`, `filter`, `all_included`."""

    ids: list[str] | None = Field(default=None, max_length=5000)
    filter: ItemFilter | None = None
    all_included: bool = False

    columns: Literal[2, 3, 4] = 3
    group_by: Literal["group", "none"] = "group"
    show_code: bool = True
    # 'pack' = the price for the pack as quoted; 'piece' = price per piece.
    price_basis: Literal["pack", "piece"] = "pack"
    contents: bool = True
    # Cover title for customers. Defaults to the catalog's name.
    title: str | None = Field(default=None, min_length=1, max_length=200)


class CustomerCatalogOut(BaseModel):
    id: str
    version: int
    title: str
    options: dict[str, object]
    item_count: int
    page_count: int
    byte_size: int
    # True once prices or items changed after this version was made: regenerate.
    stale: bool
    created_at: datetime


class LinkProductIn(BaseModel):
    product_id: str


# --------------------------------------------------------------------------
# S5: promote to items, push to Tally
# --------------------------------------------------------------------------


class SelectionIn(BaseModel):
    """Which rows: exactly one of `ids`, `filter`, `all_included`."""

    ids: list[str] | None = Field(default=None, max_length=5000)
    filter: ItemFilter | None = None
    all_included: bool = False


class PromoteOut(BaseModel):
    total: int
    create: int
    link_existing: int
    reuse: int
    already: int
    renamed: int
    examples: list[str]


class TallySettingsIn(BaseModel):
    """Only the fields sent change. A blank root means Primary."""

    stock_group_root: str | None = Field(default=None, max_length=99)
    tally_group_create_policy: Literal["create_missing", "existing_only"] | None = None
    # {item_category_id: Tally stock group}; a blank value removes that mapping.
    stock_group_map: dict[str, str] | None = None


class TallySettingsOut(BaseModel):
    connected: bool
    company_name: str | None
    stock_group_root: str | None
    tally_group_create_policy: str
    stock_group_map: dict[str, str]
    checked_at: datetime | None
    check_is_fresh: bool
    known_groups: list[str]


class TallyCheckOut(BaseModel):
    id: str
    status: str  # queued | sent | running | ok | error
    error: str | None
    groups: int | None = None
    items: int | None = None
    linked: int | None = None
    created_at: datetime


class PreflightCheckOut(BaseModel):
    code: str
    ok: bool
    message: str
    blocking: bool


class PreflightGroupOut(BaseModel):
    category_id: str | None
    our_name: str
    tally_name: str
    status: str  # existing | create | missing
    item_count: int


class PreflightCollisionOut(BaseModel):
    item_id: str
    code: str
    name: str


class TallyPreflightOut(BaseModel):
    ok: bool
    total: int
    to_push: int
    already_synced: int
    skipped_existing: int = 0
    not_promoted: int
    batches: int
    root: str | None
    checked_at: datetime | None
    checks: list[PreflightCheckOut]
    groups: list[PreflightGroupOut]
    collisions: list[PreflightCollisionOut]


class TallyPushIn(SelectionIn):
    # also re-send items already in Tally (an alter: use after changing names or codes)
    include_synced: bool = False
    # leave out items whose name already exists in Tally (never overwrite them)
    skip_existing: bool = False


class TallyRunOut(BaseModel):
    run_id: str | None
    state: str  # none | running | done | error | stopped
    batches: int
    batches_done: int
    total_items: int
    synced: int
    error: str | None
    updated_at: datetime | None
    # while running: "tally_unavailable" | "no_company_loaded" when Tally is not ready and the
    # batch is being retried
    waiting: str | None = None
