"""Take an uploaded PDF onto disk without holding it in memory.

FastAPI has already put a big upload in a temp file; reading it with `.read()` would pull the whole
thing into memory again (a 200 MB catalog = 200 MB of RAM, and more while it is parsed). This copies
it a megabyte at a time to a named temp file, checking the size cap and the PDF signature and working
out the SHA-256 on the way, so the rest of the code can open it by path and read page by page.
"""

from __future__ import annotations

import contextlib
import hashlib
import os
import tempfile
from collections.abc import Iterator
from contextlib import contextmanager
from dataclasses import dataclass
from typing import BinaryIO

from fastapi import HTTPException, status

_CHUNK = 1024 * 1024
_PDF_MAGIC = b"%PDF-"


@dataclass(frozen=True)
class SpooledPdf:
    path: str
    sha256: str
    size: int


@contextmanager
def spool_pdf(source: BinaryIO, *, max_bytes: int, too_big: str) -> Iterator[SpooledPdf]:
    """Yield the upload as a file on disk; the file is deleted when the block ends.

    413 with `too_big` as soon as the upload passes `max_bytes` (the rest is never read);
    415 when it is not a PDF.
    """
    fd, path = tempfile.mkstemp(prefix="upload-", suffix=".pdf")
    try:
        digest = hashlib.sha256()
        size = 0
        with os.fdopen(fd, "wb") as out:
            while chunk := source.read(_CHUNK):
                if size == 0 and not chunk.startswith(_PDF_MAGIC):
                    raise HTTPException(
                        status.HTTP_415_UNSUPPORTED_MEDIA_TYPE, detail="Upload a PDF file."
                    )
                size += len(chunk)
                if size > max_bytes:
                    raise HTTPException(status.HTTP_413_CONTENT_TOO_LARGE, detail=too_big)
                digest.update(chunk)
                out.write(chunk)
        if size == 0:
            raise HTTPException(status.HTTP_415_UNSUPPORTED_MEDIA_TYPE, detail="Upload a PDF file.")
        yield SpooledPdf(path=path, sha256=digest.hexdigest(), size=size)
    finally:
        with contextlib.suppress(OSError):
            os.unlink(path)
