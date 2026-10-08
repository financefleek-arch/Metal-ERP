"""Pydantic models for the item catalogue API.

Split from `schemas.py` to keep the item surface self-contained.
"""

from __future__ import annotations

from datetime import datetime
from decimal import Decimal
from typing import Annotated

from pydantic import BaseModel, ConfigDict, Field, computed_field, model_validator

from app.models._mixins import Availability, ItemSource, ItemStatus, ItemType
from app.services.media_urls import media_url

# Money / quantity as Decimal so we don't lose paise to float.
Money = Annotated[Decimal, Field(max_digits=15, decimal_places=2)]
Dim = Annotated[Decimal, Field(max_digits=9, decimal_places=2, ge=0)]
Factor = Annotated[Decimal, Field(max_digits=12, decimal_places=4, gt=0)]
Pct = Annotated[Decimal, Field(max_digits=5, decimal_places=2, ge=0, le=100)]

_NAME = Field(min_length=1, max_length=200)
_SHORT = Field(default=None, max_length=64)


class ItemBase(BaseModel):
    name: str = _NAME
    item_type: ItemType = ItemType.bulk
    category: str | None = Field(default=None, max_length=50)
    category_id: str | None = None
    group_id: str | None = None
    weight_per_piece: Decimal | None = Field(default=None, max_digits=12, decimal_places=3, ge=0)
    sku: str | None = _SHORT
    size_label: str | None = Field(default=None, max_length=50)
    uom: str | None = Field(default=None, max_length=20)
    hsn_code: str | None = Field(default=None, max_length=8)
    availability: Availability = Availability.in_stock
    pack_qty: int | None = Field(default=None, ge=1, le=100000)
    carton_qty: int | None = Field(default=None, ge=1, le=1000000)

    # metal-trade attributes
    metal: str | None = Field(default=None, max_length=20)
    shape: str | None = Field(default=None, max_length=24)
    grade: str | None = Field(default=None, max_length=32)
    size_text: str | None = Field(default=None, max_length=60)
    thickness_mm: Dim | None = None
    width_mm: Dim | None = None
    length_mm: Dim | None = None
    finish: str | None = Field(default=None, max_length=24)

    # units & conversion
    secondary_uom: str | None = Field(default=None, max_length=20)
    conversion_factor: Factor | None = None
    weight_per_uom: Dim | None = None
    purchase_uom: str | None = Field(default=None, max_length=20)

    # pricing
    default_rate: Money | None = None
    mrp: Money | None = None
    default_discount_pct: Pct | None = None
    price_min: Money | None = None
    price_max: Money | None = None

    notes: str | None = Field(default=None, max_length=1000)

    @model_validator(mode="after")
    def _price_band_ordered(self) -> ItemBase:
        if (
            self.price_min is not None
            and self.price_max is not None
            and self.price_min > self.price_max
        ):
            raise ValueError("price_min must be less than or equal to price_max")
        return self


class ItemCreate(ItemBase):
    pass


class ItemUpdate(BaseModel):
    """Every field optional (PATCH). Same validators as ItemBase where relevant."""

    name: str | None = Field(default=None, min_length=1, max_length=200)
    item_type: ItemType | None = None
    category: str | None = Field(default=None, max_length=50)
    category_id: str | None = None
    group_id: str | None = None
    weight_per_piece: Decimal | None = Field(default=None, max_digits=12, decimal_places=3, ge=0)
    sku: str | None = _SHORT
    size_label: str | None = Field(default=None, max_length=50)
    uom: str | None = Field(default=None, max_length=20)
    hsn_code: str | None = Field(default=None, max_length=8)
    metal: str | None = Field(default=None, max_length=20)
    shape: str | None = Field(default=None, max_length=24)
    grade: str | None = Field(default=None, max_length=32)
    size_text: str | None = Field(default=None, max_length=60)
    thickness_mm: Dim | None = None
    width_mm: Dim | None = None
    length_mm: Dim | None = None
    finish: str | None = Field(default=None, max_length=24)
    secondary_uom: str | None = Field(default=None, max_length=20)
    conversion_factor: Factor | None = None
    weight_per_uom: Dim | None = None
    purchase_uom: str | None = Field(default=None, max_length=20)
    default_rate: Money | None = None
    mrp: Money | None = None
    default_discount_pct: Pct | None = None
    price_min: Money | None = None
    price_max: Money | None = None
    status: ItemStatus | None = None
    availability: Availability | None = None
    pack_qty: int | None = Field(default=None, ge=1, le=100000)
    carton_qty: int | None = Field(default=None, ge=1, le=1000000)
    notes: str | None = Field(default=None, max_length=1000)

    @model_validator(mode="after")
    def _price_band_ordered(self) -> ItemUpdate:
        if (
            self.price_min is not None
            and self.price_max is not None
            and self.price_min > self.price_max
        ):
            raise ValueError("price_min must be less than or equal to price_max")
        return self


class ItemMergeIn(BaseModel):
    target_id: str


# --------------------------------------------------------------------------
# bulk operations — select N items, change one/few fields, preview, apply
# --------------------------------------------------------------------------

# Fields a shop actually sets in bulk. Identity / dimension fields
# (name, size, thickness, …) stay per-item and are deliberately excluded.
BULK_EDITABLE_FIELDS = frozenset(
    {
        "uom",
        "purchase_uom",
        "secondary_uom",
        "default_discount_pct",
        "default_rate",
        "item_type",
        "hsn_code",
        "metal",
        "shape",
        "finish",
        "category_id",
        "group_id",
        "status",
        "notes",
        "availability",
        "pack_qty",
        "carton_qty",
    }
)

# Selecting by filter or by ticks share one ceiling; a runaway selection is refused.
MAX_BULK_FILTER = 20000
MAX_BULK_IDS = MAX_BULK_FILTER


class NodeFilter(BaseModel):
    """One place in the tree: a group, a category, or an "Ungrouped" bucket."""

    group_id: str | None = None
    category_id: str | None = None
    uncategorised: bool = False
    ungrouped: bool = False

    @model_validator(mode="after")
    def _not_empty(self) -> NodeFilter:
        if not (self.group_id or self.category_id or self.uncategorised or self.ungrouped):
            raise ValueError("a tree node needs a group or a category")
        return self


class ItemFilter(BaseModel):
    """Which items, by what they look like instead of by ticking rows. Every field optional;
    all given fields must match."""

    q: str | None = Field(default=None, max_length=100)
    type: ItemType | None = None
    status: ItemStatus | None = None
    no_hsn: bool = False
    price_review: bool = False
    availability: list[Availability] | None = None
    no_photo: bool = False
    in_tally: bool | None = None
    # in Tally, but at a different selling price than the item's rate now
    tally_price_due: bool = False
    # came from this supplier / this supplier catalog
    supplier_id: str | None = None
    catalog_id: str | None = None
    # where it sits in the tree. The three combine: category + ungrouped is a tree "Ungrouped"
    # row, uncategorised + ungrouped is the loose bucket of the "Uncategorised" node.
    group_id: str | None = None
    category_id: str | None = None
    uncategorised: bool = False
    ungrouped: bool = False
    # several tree nodes at once: an item matches if it is in ANY of them
    any_of: list[NodeFilter] | None = Field(default=None, max_length=200)


class ItemCountOut(BaseModel):
    count: int


class ItemBulkUpdate(BaseModel):
    """`ids` + a partial `ItemUpdate`. Only keys the caller sends are touched;
    everything else on every item is left alone. `notes_mode` picks whether a
    supplied `notes` value replaces or is appended as a new line.
    """

    ids: list[str] | None = Field(default=None, min_length=1, max_length=MAX_BULK_IDS)
    # or: everything matching a filter (exactly one of ids / filter)
    filter: ItemFilter | None = None
    fields: ItemUpdate
    fields_set: list[str] = Field(
        min_length=1,
        description="the field names to apply — the sheet's enabled toggles",
    )
    notes_mode: str = Field(default="replace", pattern="^(replace|append)$")

    @model_validator(mode="after")
    def _check_fields(self) -> ItemBulkUpdate:
        if (self.ids is None) == (self.filter is None):
            raise ValueError("send either ids or a filter, not both")
        chosen = set(self.fields_set)
        bad = chosen - BULK_EDITABLE_FIELDS
        if bad:
            raise ValueError(f"not bulk-editable: {', '.join(sorted(bad))}")
        supplied = set(self.fields.model_dump(exclude_unset=True))
        missing = chosen - supplied
        if missing:
            raise ValueError(f"enabled but no value given: {', '.join(sorted(missing))}")
        return self


class ItemBulkPrice(BaseModel):
    """Move the selling rate of many items by a percentage or a fixed amount (use a negative
    value to lower it), optionally rounded to a step. Items with no rate are left alone."""

    ids: list[str] | None = Field(default=None, min_length=1, max_length=MAX_BULK_IDS)
    filter: ItemFilter | None = None
    field: str = Field(default="default_rate", pattern="^(default_rate|mrp)$")
    mode: str = Field(pattern="^(percent|amount)$")
    value: Decimal = Field(max_digits=10, decimal_places=2)
    # round the new rate to the nearest multiple of this (1 = whole rupee, 0.5, 5, 10)
    round_to: Decimal | None = Field(default=None, gt=0, le=1000)

    @model_validator(mode="after")
    def _check(self) -> ItemBulkPrice:
        if (self.ids is None) == (self.filter is None):
            raise ValueError("send either ids or a filter, not both")
        if self.value == 0:
            raise ValueError("the change is zero")
        if self.mode == "percent" and self.value <= Decimal("-100"):
            raise ValueError("cannot lower a rate by 100% or more")
        return self


class ItemBulkDelete(BaseModel):
    ids: list[str] | None = Field(default=None, min_length=1, max_length=MAX_BULK_IDS)
    filter: ItemFilter | None = None
    # what to do with items that are on documents (can't be deleted)
    on_blocked: str = Field(default="skip", pattern="^(skip|archive)$")

    @model_validator(mode="after")
    def _one_selection(self) -> ItemBulkDelete:
        if (self.ids is None) == (self.filter is None):
            raise ValueError("send either ids or a filter, not both")
        return self


class ItemBulkRename(BaseModel):
    """Find and replace in item names, for the chosen items (or everything a filter matches)."""

    ids: list[str] | None = Field(default=None, min_length=1, max_length=MAX_BULK_IDS)
    filter: ItemFilter | None = None
    find: str = Field(min_length=1, max_length=100)
    replace: str = Field(default="", max_length=100)
    case_sensitive: bool = False
    whole_word: bool = False

    @model_validator(mode="after")
    def _one_selection(self) -> ItemBulkRename:
        if (self.ids is None) == (self.filter is None):
            raise ValueError("send either ids or a filter, not both")
        return self


class BulkOutcome(BaseModel):
    """One row of a bulk result / preview — same shape for dry-run and real."""

    id: str
    name: str
    result: str  # changed | skipped | deleted | archived | blocked | error
    detail: str | None = None


class ItemBulkUpdateResult(BaseModel):
    dry_run: bool
    changed: int
    unchanged: int
    errors: int
    learned_rule_ids: list[str] = Field(default_factory=list)
    rows: list[BulkOutcome]


class ItemBulkDeleteResult(BaseModel):
    dry_run: bool
    deleted: int
    archived: int
    blocked: int
    errors: int
    rows: list[BulkOutcome]


class ItemListItem(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: str
    name: str
    item_type: ItemType
    category: str | None
    category_id: str | None
    group_id: str | None
    sku: str | None
    size_label: str | None
    uom: str | None
    secondary_uom: str | None  # the alternate sell unit, when sold two ways
    hsn_code: str | None
    metal: str | None
    shape: str | None
    grade: str | None
    size_text: str | None
    default_rate: Money | None
    last_rate: Money | None
    last_purchase_rate: Money | None
    last_sold_at: datetime | None
    gst_rate: Money | None
    price_min: Money | None
    price_max: Money | None
    times_billed: int
    status: ItemStatus
    source: ItemSource
    availability: Availability = Availability.in_stock
    pack_qty: int | None = None
    carton_qty: int | None = None
    primary_media_id: str | None = Field(default=None, exclude=True)
    tally_guid: str | None = Field(default=None, exclude=True)
    tally_status: str = Field(default="none", exclude=True)
    tally_price: Money | None = Field(default=None, exclude=True)
    tally_seen_price: Money | None = Field(default=None, exclude=True)

    @computed_field  # type: ignore[prop-decorator]
    @property
    def tally_state(self) -> str:
        """none | queued | error | imported | synced | price_due | price_differs."""
        if self.tally_status == "synced":
            if self.default_rate is not None and (
                self.tally_price is None or self.tally_price != self.default_rate
            ):
                return "price_due"
            if self.tally_seen_price is not None and self.tally_seen_price != self.tally_price:
                return "price_differs"
            return "synced"
        if self.tally_status in ("queued", "error"):
            return self.tally_status
        return "imported" if self.tally_guid else "none"

    @computed_field  # type: ignore[prop-decorator]
    @property
    def photo_url(self) -> str | None:
        """The item's photo (about 1000 px), a signed URL that works in an <img> tag."""
        return media_url(self.primary_media_id, "photo") if self.primary_media_id else None

    @computed_field  # type: ignore[prop-decorator]
    @property
    def thumb_url(self) -> str | None:
        """A small version for lists."""
        return media_url(self.primary_media_id, "thumb") if self.primary_media_id else None


class ItemOut(ItemListItem):
    name_normalized: str
    weight_per_piece: Decimal | None
    thickness_mm: Dim | None
    width_mm: Dim | None
    length_mm: Dim | None
    finish: str | None
    secondary_uom: str | None
    conversion_factor: Factor | None
    weight_per_uom: Dim | None
    purchase_uom: str | None
    mrp: Money | None
    default_discount_pct: Pct | None
    last_sold_at: datetime | None
    last_purchased_at: datetime | None
    merged_into_id: str | None
    notes: str | None
    # advisory: does default_rate sit inside [price_min, price_max]?
    rate_in_band: bool | None = None
    # count of invoice lines referencing this item (0 until Sales ships)
    document_count: int = 0
