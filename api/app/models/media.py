"""Media: one row per stored image, referenced by whatever uses it.

An image is normalised on ingest (long edge capped, EXIF applied and stripped, re-encoded as
WebP) into a main rendition and a thumbnail, and stored once per firm under its content hash.
Many things can point at one row (items today; party logos, documents later), so deleting the
thing that brought a photo in never deletes a photo something else uses. Rows nothing refers to
are swept later (`services/media.py::sweep_orphans`).
"""

from __future__ import annotations

from sqlalchemy import ForeignKey, Integer, String, UniqueConstraint
from sqlalchemy.orm import Mapped, mapped_column

from app.db import Base
from app.models._mixins import PkUuidMixin, TimestampMixin


class MediaAsset(PkUuidMixin, TimestampMixin, Base):
    __tablename__ = "media_asset"
    __table_args__ = (UniqueConstraint("tenant_id", "sha256", name="uq_media_tenant_sha"),)

    tenant_id: Mapped[str] = mapped_column(ForeignKey("tenant.id"), nullable=False, index=True)
    # sha256 of the normalised main rendition: the same picture is stored once per firm.
    sha256: Mapped[str] = mapped_column(String(64), nullable=False)
    key: Mapped[str] = mapped_column(String(300), nullable=False)
    thumb_key: Mapped[str] = mapped_column(String(300), nullable=False)
    content_type: Mapped[str] = mapped_column(String(40), default="image/webp", nullable=False)
    width: Mapped[int] = mapped_column(Integer, nullable=False)
    height: Mapped[int] = mapped_column(Integer, nullable=False)
    bytes: Mapped[int] = mapped_column(Integer, nullable=False)
    thumb_bytes: Mapped[int] = mapped_column(Integer, nullable=False)
    # upload | catalog | bulk
    source: Mapped[str] = mapped_column(String(12), default="upload", nullable=False)
    created_by: Mapped[str | None] = mapped_column(ForeignKey("app_user.id"))
