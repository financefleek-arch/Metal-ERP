"""A big catalog must not be held in memory: photos are stored a page at a time and dropped."""

from __future__ import annotations

import io
import os
import tracemalloc
from unittest.mock import MagicMock

import pytest
from PIL import Image

from app.services.catalog import importer
from app.services.catalog.extract_grid import ExtractResult, extract_pages
from app.services.catalog.storage import LocalStorage
from tests.catalog.pdfs import TestCell, make_pdf

PAGES = 25  # 12 products a page


def _noise_jpeg(seed: int) -> bytes:
    img = Image.frombytes("RGB", (200, 150), os.urandom(200 * 150 * 3))
    buf = io.BytesIO()
    img.save(buf, "JPEG", quality=70)
    return buf.getvalue() + seed.to_bytes(4, "big")  # distinct bytes: no de-duplication


@pytest.fixture(scope="module")
def big_pdf(tmp_path_factory: pytest.TempPathFactory) -> str:
    n = 0
    pages = []
    for _ in range(PAGES):
        cells = []
        for _ in range(12):
            n += 1
            cells.append(TestCell(code=f"C{n:05d}", image=_noise_jpeg(n)))
        pages.append(cells)
    path = tmp_path_factory.mktemp("big") / "big.pdf"
    path.write_bytes(make_pdf(pages))
    return str(path)


def test_photos_are_stored_page_by_page_and_not_kept(big_pdf: str, tmp_path) -> None:
    file_bytes = os.path.getsize(big_pdf)
    storage = LocalStorage(tmp_path / "store")
    result, uploaded, cells = ExtractResult(), set(), []

    tracemalloc.start()
    for page in extract_pages(big_pdf, result):
        importer._store_page(storage, "t1", "c1", page, uploaded)
        cells.extend(page)
    _, peak = tracemalloc.get_traced_memory()
    tracemalloc.stop()

    assert len(cells) == PAGES * 12
    assert len(uploaded) == PAGES * 12
    assert all(c.image is None and c.image_key and c.image_sha256 for c in cells)
    # one page of photos plus the small records, never the file: well under a quarter of it
    assert peak < file_bytes / 4, f"peak {peak} bytes for a {file_bytes} byte file"
    assert peak < 3 * 1024 * 1024


def test_a_failed_import_deletes_the_photos_it_already_stored(monkeypatch: pytest.MonkeyPatch) -> None:
    session = MagicMock()
    session.scalar.return_value = None  # not seen before
    storage = MagicMock()

    def _stores_then_fails(*_a, uploaded: set[str], **_k):
        uploaded.update({"catalog/t/c/img/aa.jpeg", "catalog/t/c/img/bb.jpeg"})
        raise RuntimeError("database said no")

    monkeypatch.setattr(importer, "_build_catalog", _stores_then_fails)
    with pytest.raises(RuntimeError):
        importer.import_catalog(
            session,
            storage,
            tenant=MagicMock(id="t"),
            user_id=None,
            filename="x.pdf",
            pdf_path="x",
            sha256="abc",
        )
    (deleted,) = storage.delete_many.call_args.args
    assert {"catalog/t/c/img/aa.jpeg", "catalog/t/c/img/bb.jpeg"} <= set(deleted)


def test_progress_store_answers_only_its_own_firm_and_forgets() -> None:
    from app.services.catalog import progress

    progress.report("firm-a", "p1", "saving", 250, 1000)
    assert progress.read("firm-a", "p1") == {"stage": "saving", "done": 250, "total": 1000}
    assert progress.read("firm-b", "p1") is None  # another firm cannot read it
    progress.report("firm-a", None, "saving", 1, 1)  # no id: nothing stored
    progress.clear("firm-a", "p1")
    assert progress.read("firm-a", "p1") is None
