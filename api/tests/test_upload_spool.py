"""Uploads go to disk a megabyte at a time: the size cap, the PDF check and the clean-up."""

from __future__ import annotations

import hashlib
import io
import os

import pytest
from fastapi import HTTPException

from app.services.upload_spool import spool_pdf

_PDF = b"%PDF-1.4\n" + b"x" * 3_000_000


def test_the_file_lands_on_disk_with_its_hash_and_is_removed_after() -> None:
    with spool_pdf(io.BytesIO(_PDF), max_bytes=10_000_000, too_big="big") as pdf:
        assert pdf.size == len(_PDF)
        assert pdf.sha256 == hashlib.sha256(_PDF).hexdigest()
        with open(pdf.path, "rb") as fh:
            assert fh.read() == _PDF
        kept = pdf.path
    assert not os.path.exists(kept)


def test_an_oversize_upload_is_refused_without_reading_the_rest() -> None:
    class Counting(io.BytesIO):
        read_total = 0

        def read(self, n: int = -1) -> bytes:
            chunk = super().read(n)
            self.read_total += len(chunk)
            return chunk

    src = Counting(b"%PDF-" + b"x" * 50_000_000)
    with pytest.raises(HTTPException) as e, spool_pdf(src, max_bytes=2_000_000, too_big="too big"):
        pass
    assert e.value.status_code == 413
    assert e.value.detail == "too big"
    assert src.read_total <= 3 * 1024 * 1024  # stopped at the cap, did not drain 50 MB


@pytest.mark.parametrize("body", [b"", b"hello, not a pdf"])
def test_something_that_is_not_a_pdf_is_a_415(body: bytes) -> None:
    with pytest.raises(HTTPException) as e, spool_pdf(io.BytesIO(body), max_bytes=1_000, too_big="x"):
        pass
    assert e.value.status_code == 415


def test_the_temp_file_is_removed_when_the_work_inside_fails() -> None:
    seen: list[str] = []
    with pytest.raises(RuntimeError), spool_pdf(io.BytesIO(_PDF), max_bytes=10_000_000, too_big="x") as pdf:
        seen.append(pdf.path)
        raise RuntimeError("boom")
    assert not os.path.exists(seen[0])
