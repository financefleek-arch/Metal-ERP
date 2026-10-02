"""Supplier Catalog API schemas."""

from __future__ import annotations

from datetime import datetime
from decimal import Decimal
from typing import Annotated, Literal

from pydantic import BaseModel, ConfigDict, Field, PlainSerializer

# Always serialised with fixed decimals, so the JSON is the same on every database
# (SQLite drops trailing zeros; Postgres NUMERIC keeps them).
Money = Annotated[Decimal, PlainSerializer(lambda v: f"{v:.2f}", return_type=str)]
MultiplierOut = Annotated[Decimal, PlainSerializer(lambda v: f"{v:.3f}", return_type=str)]
MultiplierOrNone = Annotated[
    Decimal | None,
    PlainSerializer(lambda v: None if v is None else f"{v:.3f}", return_type=str | None),
]


class CatalogListItem(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: str
    title: str
    source_filename: str
    page_count: int
    item_count: int
    code_prefix: str
    multiplier: MultiplierOut
    rounding_step: int
    status: str
    created_at: datetime


class CatalogDetail(CatalogListItem):
    # Items that carry their own multiplier instead of the catalog's.
    override_count: int = 0


class CatalogUploadOut(CatalogListItem):
    # True when this exact file was uploaded before and the existing catalog is returned.
    already_exists: bool = False
    # Photos with no price line (blank filler cells) that were left out.
    skipped_cells: int = 0
    warnings: list[str] = []


# One multiplier for the catalog, or one per item: 3 decimals, 0.001 to 99.999.
Multiplier = Annotated[Decimal | None, Field(gt=0, le=Decimal("99.999"), decimal_places=3)]


class CatalogPatch(BaseModel):
    """Changing `multiplier` or `rounding_step` reprices every item."""

    title: str | None = Field(default=None, min_length=1, max_length=200)
    multiplier: Multiplier = None
    rounding_step: Literal[1, 5, 10] | None = None


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
    multiplier_override: MultiplierOrNone
    sell_price: Money
    category_id: str | None
    category_name: str | None
    suggested_group: str | None
    included: bool
    image_url: str | None
    # Code 128 bar pattern for `code` ('1' = bar, '0' = space), drawn as SVG in the browser.
    barcode: str | None
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
    # A number sets this item's own multiplier; null clears it (back to the catalog's).
    multiplier_override: Multiplier = None


class ItemFilter(BaseModel):
    q: str | None = None
    category_id: str | None = None
    no_group: bool = False
    included: bool | None = None
    brand: str | None = None
    # True = only items with their own multiplier; False = only those using the catalog's.
    has_override: bool | None = None


class BulkChanges(BaseModel):
    group_name: str | None = Field(default=None, max_length=120)
    included: bool | None = None
    # A number sets the override on every target item; null clears it.
    multiplier_override: Multiplier = None


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
