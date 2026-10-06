"""Photos: attach to items one at a time or in bulk, serve by signed URL, show usage.

Photos are normalised and stored once per firm (see `services/media.py`). Owners and managers
write; anyone logged in reads.
"""

from __future__ import annotations

import io
import zipfile
from typing import Annotated

from fastapi import APIRouter, Depends, File, HTTPException, Response, UploadFile, status
from pydantic import BaseModel
from sqlalchemy import select

from app.deps import CurrentUser, SessionDep, WriteUser
from app.domain.normalize import load_synonym_map, normalize_name
from app.models import Item, MediaAsset
from app.services import media as media_svc
from app.services.catalog import image_url
from app.services.catalog.storage import CatalogStorage, get_storage
from app.services.media_urls import SCOPE, media_url

router = APIRouter(prefix="/api", tags=["media"])
StorageDep = Annotated[CatalogStorage, Depends(get_storage)]

_MAX_BULK = 200
# A zip may hold more than one request's worth, but not without limit (a zip can hide a lot).
_MAX_ZIP_IMAGES = 300
_MAX_ZIP_BYTES = 150 * 1024 * 1024  # uncompressed, all images together
_IMAGE_EXT = (".jpg", ".jpeg", ".png", ".webp")


class PhotoOut(BaseModel):
    photo_url: str | None
    thumb_url: str | None


class StagedMatch(BaseModel):
    item_id: str
    name: str
    code: str | None
    method: str  # code | name


class StagedPhoto(BaseModel):
    file_name: str
    media_id: str | None
    thumb_url: str | None
    match: StagedMatch | None
    error: str | None = None


class ApplyPair(BaseModel):
    media_id: str
    item_id: str


class ApplyIn(BaseModel):
    pairs: list[ApplyPair]


class ApplyOut(BaseModel):
    applied: int
    skipped: int


class UsageOut(BaseModel):
    images: int
    bytes: int


def _item(session: SessionDep, tenant_id: str, item_id: str) -> Item:
    it = session.scalar(select(Item).where(Item.id == item_id, Item.tenant_id == tenant_id))
    if it is None:
        raise HTTPException(status_code=404, detail="Item not found")
    return it


def _read(file: UploadFile) -> bytes:
    data = file.file.read(media_svc.MAX_UPLOAD_BYTES + 1)
    if len(data) > media_svc.MAX_UPLOAD_BYTES:
        raise HTTPException(
            status_code=status.HTTP_413_REQUEST_ENTITY_TOO_LARGE,
            detail=f"That photo is over {media_svc.MAX_UPLOAD_BYTES // (1024 * 1024)} MB.",
        )
    return data


def _photo_out(item: Item) -> PhotoOut:
    if not item.primary_media_id:
        return PhotoOut(photo_url=None, thumb_url=None)
    return PhotoOut(
        photo_url=media_url(item.primary_media_id, "photo"),
        thumb_url=media_url(item.primary_media_id, "thumb"),
    )


# --------------------------------------------------------------------------- one item


@router.post("/items/{item_id}/photo", response_model=PhotoOut)
def set_photo(
    item_id: str,
    session: SessionDep,
    user: WriteUser,
    storage: StorageDep,
    file: Annotated[UploadFile, File()],
) -> PhotoOut:
    item = _item(session, user.tenant_id, item_id)
    try:
        asset = media_svc.ingest(
            session, storage, user.tenant_id, _read(file), source="upload", user_id=user.id
        )
    except media_svc.ImageRejected as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc
    media_svc.set_item_photo(session, item, asset)
    return _photo_out(item)


@router.delete("/items/{item_id}/photo", status_code=status.HTTP_204_NO_CONTENT)
def delete_photo(item_id: str, session: SessionDep, user: WriteUser) -> None:
    media_svc.remove_item_photo(session, _item(session, user.tenant_id, item_id))


# --------------------------------------------------------------------------- many items


def _match(
    session: SessionDep,
    tenant_id: str,
    filename: str,
    by_code: dict[str, Item],
    by_name: dict[str, Item],
    syn: dict[str, str],
) -> StagedMatch | None:
    for cand in media_svc.filename_stems(filename):
        hit = by_code.get(cand.lower())
        if hit is not None:
            return StagedMatch(
                item_id=hit.id, name=hit.name, code=hit.barcode or hit.sku, method="code"
            )
    for cand in media_svc.filename_stems(filename)[:1]:
        key = normalize_name(cand.replace("_", " "), syn)
        hit = by_name.get(key) if key else None
        if hit is not None:
            return StagedMatch(
                item_id=hit.id, name=hit.name, code=hit.barcode or hit.sku, method="name"
            )
    return None


@router.post("/items/photos/stage", response_model=list[StagedPhoto])
def stage_photos(
    session: SessionDep,
    user: WriteUser,
    storage: StorageDep,
    files: Annotated[list[UploadFile], File()],
) -> list[StagedPhoto]:
    """Process a batch of photos and propose which item each belongs to (by file name: the
    item's code, or its name). Nothing is attached yet: the caller reviews, then applies."""
    if len(files) > _MAX_BULK:
        raise HTTPException(status_code=422, detail=f"At most {_MAX_BULK} photos at a time.")
    return _stage_named(session, user, storage, [(f.filename or "photo", _read(f)) for f in files])


def _unzip_images(data: bytes) -> list[tuple[str, bytes]]:
    """The pictures inside a zip, as (file name, bytes). Folders inside the zip are ignored (a
    picture is matched by its file name), as are hidden and macOS metadata entries."""
    try:
        zf = zipfile.ZipFile(io.BytesIO(data))
    except zipfile.BadZipFile as exc:
        raise HTTPException(status_code=422, detail="That is not a valid zip file.") from exc
    out: list[tuple[str, bytes]] = []
    total = 0
    with zf:
        for info in zf.infolist():
            base = info.filename.replace("\\", "/").rsplit("/", 1)[-1]
            if (
                info.is_dir()
                or not base
                or base.startswith(".")
                or "__MACOSX" in info.filename
                or not base.lower().endswith(_IMAGE_EXT)
            ):
                continue
            if info.file_size > media_svc.MAX_UPLOAD_BYTES:
                out.append((base, b""))  # reported as too large by the staging step
                continue
            total += info.file_size
            if total > _MAX_ZIP_BYTES:
                raise HTTPException(
                    status_code=413, detail="That zip is too large. Split it into smaller zips."
                )
            if len(out) >= _MAX_ZIP_IMAGES:
                raise HTTPException(
                    status_code=422,
                    detail=f"That zip has more than {_MAX_ZIP_IMAGES} pictures. Split it up.",
                )
            out.append((base, zf.read(info)))
    if not out:
        raise HTTPException(status_code=422, detail="There are no pictures in that zip.")
    return out


@router.post("/items/photos/stage-zip", response_model=list[StagedPhoto])
def stage_zip(
    session: SessionDep,
    user: WriteUser,
    storage: StorageDep,
    file: Annotated[UploadFile, File()],
) -> list[StagedPhoto]:
    """Like `stage`, for the pictures inside one zip (folders in it are fine)."""
    data = file.file.read(_MAX_ZIP_BYTES + 1)
    if len(data) > _MAX_ZIP_BYTES:
        raise HTTPException(status_code=413, detail="That zip is too large. Split it up.")
    return _stage_named(session, user, storage, _unzip_images(data))


def _stage_named(
    session: SessionDep, user: WriteUser, storage: StorageDep, files: list[tuple[str, bytes]]
) -> list[StagedPhoto]:
    items = list(session.scalars(select(Item).where(Item.tenant_id == user.tenant_id)))
    by_code: dict[str, Item] = {}
    for it in items:
        for c in (it.barcode, it.sku):
            if c:
                by_code.setdefault(c.lower(), it)
    syn = load_synonym_map(session, user.tenant_id)
    by_name = {it.name_normalized: it for it in items if it.name_normalized}
    out: list[StagedPhoto] = []
    for name, raw in files:
        try:
            if not raw:
                raise media_svc.ImageRejected(
                    f"That picture is over {media_svc.MAX_UPLOAD_BYTES // (1024 * 1024)} MB."
                )
            asset = media_svc.ingest(
                session, storage, user.tenant_id, raw, source="bulk", user_id=user.id
            )
        except media_svc.ImageRejected as exc:
            out.append(
                StagedPhoto(
                    file_name=name, media_id=None, thumb_url=None, match=None, error=str(exc)
                )
            )
            continue
        out.append(
            StagedPhoto(
                file_name=name,
                media_id=asset.id,
                thumb_url=media_url(asset.id, "thumb"),
                match=_match(session, user.tenant_id, name, by_code, by_name, syn),
            )
        )
    return out


@router.post("/items/photos/apply", response_model=ApplyOut)
def apply_photos(body: ApplyIn, session: SessionDep, user: WriteUser) -> ApplyOut:
    """Attach reviewed photos. A photo or an item that is not yours is skipped."""
    applied = skipped = 0
    for pair in body.pairs:
        asset = session.scalar(
            select(MediaAsset).where(
                MediaAsset.id == pair.media_id, MediaAsset.tenant_id == user.tenant_id
            )
        )
        item = session.scalar(
            select(Item).where(Item.id == pair.item_id, Item.tenant_id == user.tenant_id)
        )
        if asset is None or item is None:
            skipped += 1
            continue
        media_svc.set_item_photo(session, item, asset)
        applied += 1
    return ApplyOut(applied=applied, skipped=skipped)


# --------------------------------------------------------------------------- serving + usage


@router.get("/media/usage", response_model=UsageOut)
def usage(session: SessionDep, user: CurrentUser) -> UsageOut:
    n, b = media_svc.tenant_usage(session, user.tenant_id)
    return UsageOut(images=n, bytes=b)


@router.get("/media/{media_id}/{variant}")
def serve(
    media_id: str,
    variant: str,
    session: SessionDep,
    storage: StorageDep,
    e: int,
    s: str,
) -> Response:
    """The image bytes. Authenticated by the signed link, since an <img> cannot send a bearer
    header. Content never changes for a given id, so it is cacheable."""
    if variant not in ("photo", "thumb"):
        raise HTTPException(status_code=404, detail="Not found")
    if not image_url.verify(media_id, e, s, scope=SCOPE):
        raise HTTPException(status_code=403, detail="Link expired")
    asset = session.get(MediaAsset, media_id)
    if asset is None:
        raise HTTPException(status_code=404, detail="Not found")
    try:
        data = storage.get(asset.thumb_key if variant == "thumb" else asset.key)
    except Exception as exc:  # noqa: BLE001 - missing object, storage hiccup
        raise HTTPException(status_code=404, detail="Image not found") from exc
    return Response(
        content=data,
        media_type=asset.content_type,
        headers={"Cache-Control": "private, max-age=86400"},
    )
