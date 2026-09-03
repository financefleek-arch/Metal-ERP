"""Pre-signed R2 (S3-compatible) PUT URLs for the Tally companion agent.

The Windows tally-agent tool never holds R2 credentials — it calls
`/api/tally-agent/upload-request`, gets a short-lived pre-signed PUT URL
scoped to that shop's prefix, and uploads directly to R2. If a shop PC is
compromised, the blast radius is one shop's API key (revocable), not the
bucket's credentials.
"""

from __future__ import annotations

import boto3
from botocore.client import Config as BotoConfig

from app.config import get_settings

_settings = get_settings()

_PUT_URL_EXPIRY_SECONDS = 900  # 15 minutes — long enough for a slow shop-PC upload


class R2NotConfigured(Exception):
    """`TALLY_R2_*` env vars are not set."""


def _client():  # type: ignore[no-untyped-def]
    if not _settings.tally_r2_configured:
        raise R2NotConfigured("TALLY_R2_* settings are not configured")
    return boto3.client(
        "s3",
        endpoint_url=_settings.tally_r2_endpoint_url,
        aws_access_key_id=_settings.tally_r2_access_key_id,
        aws_secret_access_key=_settings.tally_r2_secret_access_key,
        config=BotoConfig(signature_version="s3v4"),
        region_name="auto",
    )


def presigned_put_url(r2_key: str) -> tuple[str, int]:
    """Returns (url, expires_in_seconds)."""
    url = _client().generate_presigned_url(
        "put_object",
        Params={"Bucket": _settings.tally_r2_bucket, "Key": r2_key},
        ExpiresIn=_PUT_URL_EXPIRY_SECONDS,
    )
    return url, _PUT_URL_EXPIRY_SECONDS
