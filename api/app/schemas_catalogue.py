"""Pydantic models for item categories + product groups (the catalogue hierarchy)."""

from __future__ import annotations

from decimal import Decimal
from typing import Annotated

from pydantic import BaseModel, ConfigDict, Field, model_validator

from app.models._mixins import ItemType, RateMode

Money = Annotated[Decimal, Field(max_digits=15, decimal_places=2)]


# --------------------------------------------------------------------------
# item_category
# --------------------------------------------------------------------------


class CategoryIn(BaseModel):
    name: str = Field(min_length=1, max_length=60)
    sort: int = 0


class CategoryUpdate(BaseModel):
    name: str | None = Field(default=None, min_length=1, max_length=60)
    sort: int | None = None


class CategoryOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: str
    name: str
    sort: int
    group_count: int = 0
    item_count: int = 0


class CategoryDeleteIn(BaseModel):
    """Where to move this category's groups/items before deleting it."""

    reassign_to: str | None = None  # another category id; None => set null


# --------------------------------------------------------------------------
# product_group
# --------------------------------------------------------------------------


class GroupIn(BaseModel):
    name: str = Field(min_length=1, max_length=200)
    category_id: str | None = None
    hsn_code: str | None = Field(default=None, max_length=8)
    uom: str | None = Field(default=None, max_length=20)
    item_type: ItemType = ItemType.mrp
    default_rate_mode: RateMode = RateMode.piece


class GroupUpdate(BaseModel):
    name: str | None = Field(default=None, min_length=1, max_length=200)
    category_id: str | None = None
    hsn_code: str | None = Field(default=None, max_length=8)
    uom: str | None = Field(default=None, max_length=20)
    item_type: ItemType | None = None
    default_rate_mode: RateMode | None = None


class GroupLeaf(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: str
    size_pos: int | None
    size_label: str | None
    size_text: str | None
    sku: str | None
    rate_mode: RateMode
    weight_per_piece: Decimal | None
    default_rate: Money | None
    last_rate: Money | None
    last_sold_at: str | None = None
    generated_name: str = ""


class GroupOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: str
    name: str
    name_normalized: str
    category_id: str | None
    category_name: str | None = None
    hsn_code: str | None
    uom: str | None
    item_type: ItemType
    default_rate_mode: RateMode
    item_count: int = 0


class GroupDetail(GroupOut):
    leaves: list[GroupLeaf] = Field(default_factory=list)


class SizeOrderIn(BaseModel):
    """New ordering: leaf ids in the intended display order."""

    leaf_ids: list[str]

    @model_validator(mode="after")
    def _non_empty(self) -> SizeOrderIn:
        if not self.leaf_ids:
            raise ValueError("leaf_ids must not be empty")
        return self
