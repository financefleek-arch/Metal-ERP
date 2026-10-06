"""Customer catalogs built from items."""

from __future__ import annotations

from datetime import datetime
from typing import Literal

from pydantic import BaseModel, Field, model_validator

from app.schemas_item import ItemFilter


class CatalogSelection(BaseModel):
    """Which items: ticked ids, or everything matching a filter (exactly one)."""

    ids: list[str] | None = Field(default=None, min_length=1, max_length=5000)
    filter: ItemFilter | None = None

    @model_validator(mode="after")
    def _one(self) -> CatalogSelection:
        if (self.ids is None) == (self.filter is None):
            raise ValueError("send either ids or a filter, not both")
        return self


class CatalogLayout(BaseModel):
    columns: Literal[2, 3, 4] = 3
    group_by: Literal["group", "none"] = "group"
    show_code: bool = True
    # 'pack' = the price of one pack; 'piece' = price per piece.
    price_basis: Literal["pack", "piece"] = "pack"
    contents: bool = True
    # In stock items are always in. Expected ones only when asked, and the label is optional.
    include_expected: bool = False
    label_expected: bool = False


class CatalogRequest(CatalogLayout):
    selection: CatalogSelection


class MissingPhoto(BaseModel):
    item_id: str
    name: str
    code: str | None


class CatalogCheckOut(BaseModel):
    selected: int
    included: int
    # items left out because of their availability
    left_out_out_of_stock: int
    left_out_discontinued: int
    left_out_expected: int
    # in the selection and available, but without a selling price: left out
    no_price: int
    missing_photos: int
    missing_photo_items: list[MissingPhoto]


class CatalogCreate(CatalogRequest):
    title: str = Field(min_length=1, max_length=200)
    # new version of an existing catalog; omit for a new one
    series_id: str | None = None
    # what to do with items that have no photo
    missing_photos: Literal["placeholder", "block"] = "placeholder"


class CatalogRebuild(BaseModel):
    title: str | None = Field(default=None, min_length=1, max_length=200)
    missing_photos: Literal["placeholder", "block"] = "placeholder"


class CustomerCatalogOut(BaseModel):
    id: str
    series_id: str
    version: int
    title: str
    options: dict[str, object]
    # how it was chosen: a filter is re-evaluated on every rebuild
    selection_kind: Literal["ids", "filter"]
    item_count: int
    page_count: int
    byte_size: int
    # None for a version that has been replaced by a newer one (not checked)
    stale: bool | None
    latest: bool
    created_at: datetime
