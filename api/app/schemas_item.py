"""Pydantic models for the item catalogue API.

Split from `schemas.py` to keep the item surface self-contained.
"""

from __future__ import annotations

from datetime import datetime
from decimal import Decimal
from typing import Annotated

from pydantic import BaseModel, ConfigDict, Field, model_validator

from app.models._mixins import ItemSource, ItemStatus, ItemType, RateMode

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
    rate_mode: RateMode | None = None
    weight_per_piece: Decimal | None = Field(default=None, max_digits=12, decimal_places=3, ge=0)
    sku: str | None = _SHORT
    size_label: str | None = Field(default=None, max_length=50)
    uom: str | None = Field(default=None, max_length=20)
    hsn_code: str | None = Field(default=None, max_length=8)

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
    rate_mode: RateMode | None = None
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


class ItemListItem(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: str
    name: str
    item_type: ItemType
    category: str | None
    category_id: str | None
    group_id: str | None
    rate_mode: RateMode
    sku: str | None
    size_label: str | None
    uom: str | None
    hsn_code: str | None
    metal: str | None
    shape: str | None
    grade: str | None
    size_text: str | None
    default_rate: Money | None
    last_rate: Money | None
    last_purchase_rate: Money | None
    gst_rate: Money | None
    price_min: Money | None
    price_max: Money | None
    times_billed: int
    status: ItemStatus
    source: ItemSource


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
