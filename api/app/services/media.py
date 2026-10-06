"""Item photos and other images: normalise on ingest, store once per firm, serve by signed URL.

Rules (see docs/PLAN-erp-foundation-masters-documents-media.md, section 11):
  - never store the raw upload: cap the long edge, apply then strip EXIF, re-encode as WebP,
    and make a small thumbnail for lists;
  - content-addressed and deduplicated per firm (`media/<tenant>/<sha256>.webp`): the same
    picture, however many times it is uploaded or imported, is one object;
  - reject what is not a plain image, is too big, or has too many pixels (decompression bombs);
  - rows nothing refers to are removed by `sweep_orphans` after a grace period.
"""

from __future__ import annotations

import hashlib
import io
import re
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta

from PIL import Image, ImageOps, UnidentifiedImageError
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.models import Item, MediaAsset
from app.services.catalog.storage import CatalogStorage

MAX_UPLOAD_BYTES = 10 * 1024 * 1024
MAX_PIXELS = 40_000_000
MAIN_EDGE = 1000
THUMB_EDGE = 240
MAIN_QUALITY = 78
THUMB_QUALITY = 72
ACCEPTED = {"JPEG", "PNG", "WEBP"}
CONTENT_TYPE = "image/webp"


class ImageRejected(ValueError):
    """The upload is not an acceptable image. The message is safe to show the user."""


@dataclass
class Rendition:
    main: bytes
    thumb: bytes
    width: int
    height: int


def _encode(im: Image.Image, edge: int, quality: int) -> tuple[bytes, tuple[int, int]]:
    copy = im.copy()
    copy.thumbnail((edge, edge), Image.Resampling.LANCZOS)
    buf = io.BytesIO()
    copy.save(buf, format="WEBP", quality=quality, method=4)
    return buf.getvalue(), copy.size


def normalise(data: bytes) -> Rendition:
    """Raw upload -> main + thumbnail renditions. Raises `ImageRejected`."""
    if not data:
        raise ImageRejected("That file is empty.")
    if len(data) > MAX_UPLOAD_BYTES:
        raise ImageRejected(f"That photo is over {MAX_UPLOAD_BYTES // (1024 * 1024)} MB.")
    try:
        im = Image.open(io.BytesIO(data))
        fmt = im.format
        w, h = im.size
    except (UnidentifiedImageError, OSError, ValueError) as exc:
        raise ImageRejected("That file is not a photo we can read (use JPG, PNG or WebP).") from exc
    if fmt not in ACCEPTED:
        raise ImageRejected("Use a JPG, PNG or WebP photo.")
    if w * h > MAX_PIXELS:
        raise ImageRejected("That photo is too large (over 40 megapixels). Resize it first.")
    try:
        im.seek(0)  # animated files: the first frame
        im = ImageOps.exif_transpose(im)  # apply the phone's rotation, then drop the EXIF
        if im.mode in ("RGBA", "LA", "P"):
            rgba = im.convert("RGBA")
            flat = Image.new("RGB", rgba.size, (255, 255, 255))
            flat.paste(rgba, mask=rgba.getchannel("A"))
            im = flat
        else:
            im = im.convert("RGB")
        main, size = _encode(im, MAIN_EDGE, MAIN_QUALITY)
        thumb, _ = _encode(im, THUMB_EDGE, THUMB_QUALITY)
    except (OSError, ValueError, Image.DecompressionBombError) as exc:
        raise ImageRejected("That photo could not be processed.") from exc
    return Rendition(main=main, thumb=thumb, width=size[0], height=size[1])


def media_key(tenant_id: str, sha256: str, variant: str = "main") -> str:
    suffix = "_t" if variant == "thumb" else ""
    return f"media/{tenant_id}/{sha256}{suffix}.webp"


def ingest(
    session: Session,
    storage: CatalogStorage,
    tenant_id: str,
    data: bytes,
    *,
    source: str = "upload",
    user_id: str | None = None,
) -> MediaAsset:
    """Normalise and store an image, or return the firm's existing copy of the same picture."""
    r = normalise(data)
    sha = hashlib.sha256(r.main).hexdigest()
    existing = session.scalar(
        select(MediaAsset).where(MediaAsset.tenant_id == tenant_id, MediaAsset.sha256 == sha)
    )
    if existing is not None:
        return existing
    key, thumb_key = media_key(tenant_id, sha), media_key(tenant_id, sha, "thumb")
    storage.put_many([(key, r.main, CONTENT_TYPE), (thumb_key, r.thumb, CONTENT_TYPE)])
    asset = MediaAsset(
        tenant_id=tenant_id,
        sha256=sha,
        key=key,
        thumb_key=thumb_key,
        content_type=CONTENT_TYPE,
        width=r.width,
        height=r.height,
        bytes=len(r.main),
        thumb_bytes=len(r.thumb),
        source=source,
        created_by=user_id,
    )
    session.add(asset)
    session.flush()
    return asset


# --------------------------------------------------------------------------- print rendition

PRINT_EDGE = 600
PRINT_QUALITY = 82


def print_key(tenant_id: str, sha256: str) -> str:
    return f"media/{tenant_id}/{sha256}_p.jpg"


def print_jpegs(storage: CatalogStorage, assets: list[MediaAsset]) -> dict[str, bytes]:
    """media id -> a JPEG sized for printing (a PDF cannot embed WebP). Made once from the stored
    main rendition and kept, so a rebuild does not decode everything again. A photo that cannot
    be read is left out: the PDF shows a placeholder."""
    from concurrent.futures import ThreadPoolExecutor

    by_key = {print_key(a.tenant_id, a.sha256): a for a in assets}
    have = storage.get_many(list(by_key))
    out: dict[str, bytes] = {}
    todo: list[tuple[str, MediaAsset]] = []
    for key, a in by_key.items():
        if have.get(key):
            out[a.id] = have[key]  # type: ignore[assignment]
        else:
            todo.append((key, a))

    def make(pair: tuple[str, MediaAsset]) -> tuple[str, MediaAsset, bytes | None]:
        key, a = pair
        try:
            im = Image.open(io.BytesIO(storage.get(a.key))).convert("RGB")
            im.thumbnail((PRINT_EDGE, PRINT_EDGE), Image.Resampling.LANCZOS)
            buf = io.BytesIO()
            im.save(buf, format="JPEG", quality=PRINT_QUALITY)
            return key, a, buf.getvalue()
        except Exception:  # noqa: BLE001 - a lost or unreadable photo is a placeholder, not a failure
            return key, a, None

    if todo:
        with ThreadPoolExecutor(max_workers=8) as pool:
            made = list(pool.map(make, todo))
        storage.put_many([(k, b, "image/jpeg") for k, _, b in made if b])
        for _, a, b in made:
            if b:
                out[a.id] = b
    return out


# --------------------------------------------------------------------------- items


def set_item_photo(session: Session, item: Item, asset: MediaAsset) -> None:
    item.primary_media_id = asset.id
    session.flush()


def remove_item_photo(session: Session, item: Item) -> None:
    """Detach the photo. The image itself stays until nothing refers to it (see sweep)."""
    item.primary_media_id = None
    session.flush()


# Filenames that name an item: "100234.jpg", "100234_front.jpg", "100234 (2).png".
_CODE_TOKEN = re.compile(r"[^A-Za-z0-9]+")


def filename_stems(filename: str) -> list[str]:
    """Candidate ids from a file name: the whole stem, then its first word."""
    stem = filename.rsplit("/", 1)[-1].rsplit("\\", 1)[-1]
    stem = stem.rsplit(".", 1)[0] if "." in stem else stem
    stem = stem.strip()
    out = [stem]
    first = _CODE_TOKEN.split(stem, maxsplit=1)[0]
    if first and first != stem:
        out.append(first)
    return [s for s in out if s]


# --------------------------------------------------------------------------- catalog photos


def copy_catalog_photos(
    tenant_id: str, pairs: list[tuple[str, str]], storage: CatalogStorage, chunk: int = 50
) -> int:
    """Give each item the photo of the catalog row it came from. `pairs` are (item id, catalog
    row id). An item that already has a photo of its own is never touched. Runs in a background
    task with its own session; returns how many items got a photo."""
    from app.db import SessionLocal
    from app.models import SupplierCatalogItem

    done = 0
    for i in range(0, len(pairs), chunk):
        part = pairs[i : i + chunk]
        with SessionLocal() as s:
            keys = dict(
                s.execute(
                    select(SupplierCatalogItem.id, SupplierCatalogItem.image_key).where(
                        SupplierCatalogItem.id.in_([rid for _, rid in part]),
                        SupplierCatalogItem.image_key.is_not(None),
                    )
                ).all()
            )
            blobs = storage.get_many({k for k in keys.values() if k})
            for item_id, row_id in part:
                item = s.get(Item, item_id)
                data = blobs.get(keys.get(row_id) or "")
                if item is None or item.tenant_id != tenant_id or item.primary_media_id or not data:
                    continue
                try:
                    asset = ingest(s, storage, tenant_id, data, source="catalog")
                except ImageRejected:
                    continue
                item.primary_media_id = asset.id
                done += 1
            s.commit()
    return done


def items_needing_photos(session: Session, tenant_id: str, rows: list) -> list[tuple[str, str]]:
    """(item id, catalog row id) for promoted rows whose item has no photo yet and whose row has
    one."""
    pairs: list[tuple[str, str]] = []
    ids = [r.item_id for r in rows if r.item_id and r.image_key]
    have = (
        set(
            session.scalars(
                select(Item.id).where(
                    Item.id.in_(ids),
                    Item.tenant_id == tenant_id,
                    Item.primary_media_id.is_not(None),
                )
            )
        )
        if ids
        else set()
    )
    seen: set[str] = set()
    for r in rows:
        if r.item_id and r.image_key and r.item_id not in have and r.item_id not in seen:
            seen.add(r.item_id)
            pairs.append((r.item_id, r.id))
    return pairs


# --------------------------------------------------------------------------- usage + cleanup


def tenant_usage(session: Session, tenant_id: str) -> tuple[int, int]:
    """(images, bytes) stored for a firm, thumbnails included."""
    n, b, t = session.execute(
        select(
            func.count(MediaAsset.id),
            func.coalesce(func.sum(MediaAsset.bytes), 0),
            func.coalesce(func.sum(MediaAsset.thumb_bytes), 0),
        ).where(MediaAsset.tenant_id == tenant_id)
    ).one()
    return int(n), int(b) + int(t)


def sweep_orphans(
    session: Session,
    storage: CatalogStorage,
    *,
    tenant_id: str | None = None,
    grace: timedelta = timedelta(days=1),
    now: datetime | None = None,
) -> tuple[int, int]:
    """Delete images nothing refers to (older than `grace`, so a just-staged upload survives).
    Returns (images removed, bytes freed). Add new reference columns to `_referenced` as other
    things start using media."""
    cutoff = (now or datetime.now(UTC)) - grace
    stmt = select(MediaAsset).where(MediaAsset.created_at < cutoff)
    if tenant_id:
        stmt = stmt.where(MediaAsset.tenant_id == tenant_id)
    removed = freed = 0
    for asset in session.scalars(stmt):
        if _referenced(session, asset.id):
            continue
        storage.delete_many([asset.key, asset.thumb_key, print_key(asset.tenant_id, asset.sha256)])
        freed += asset.bytes + asset.thumb_bytes
        removed += 1
        session.delete(asset)
    session.flush()
    return removed, freed


def _referenced(session: Session, media_id: str) -> bool:
    return (
        session.scalar(select(Item.id).where(Item.primary_media_id == media_id).limit(1))
        is not None
    )
