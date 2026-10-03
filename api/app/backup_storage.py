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


def presigned_get_url(r2_key: str, expires_in: int = _PUT_URL_EXPIRY_SECONDS) -> str:
    """Short-lived pre-signed GET — hands the agent a build download without
    giving it bucket credentials (auto-update, see services/tally/agent_release)."""
    return _client().generate_presigned_url(
        "get_object",
        Params={"Bucket": _settings.tally_r2_bucket, "Key": r2_key},
        ExpiresIn=expires_in,
    )


def put_object(r2_key: str, body: bytes, content_type: str = "application/octet-stream") -> None:
    """Server-side upload of an object we built in this process (the per-shop
    tally-agent installer zip). Unlike the agent's uploads there's no presign
    step — the API holds the creds. Raises `R2NotConfigured` if unset.
    """
    _client().put_object(
        Bucket=_settings.tally_r2_bucket,
        Key=r2_key,
        Body=body,
        ContentType=content_type,
    )


def object_exists(r2_key: str) -> bool:
    """True if the key exists. Any error other than 'not found' propagates."""
    from botocore.exceptions import ClientError

    try:
        _client().head_object(Bucket=_settings.tally_r2_bucket, Key=r2_key)
        return True
    except ClientError as exc:
        if exc.response.get("Error", {}).get("Code") in ("404", "NoSuchKey", "NotFound"):
            return False
        raise


def delete_object(r2_key: str) -> None:
    """Best-effort delete. Used when an installer is being rebuilt on key
    rotation; harmless if the key doesn't exist. Raises `R2NotConfigured`.
    """
    _client().delete_object(Bucket=_settings.tally_r2_bucket, Key=r2_key)


# 64 MB — matches the Tally-import upload ceiling; a full "All Masters"
# export is well under this.
_MAX_FETCH_BYTES = 64 * 1024 * 1024


def get_object(r2_key: str, max_bytes: int = _MAX_FETCH_BYTES) -> bytes:
    """Download an object the agent uploaded (the Tally masters XML the
    connector pull needs to parse). Raises `R2NotConfigured` if credentials
    are missing, `ValueError` if the object is larger than `max_bytes`
    (default `_MAX_FETCH_BYTES`; the agent-release zip passes a bigger cap).
    """
    resp = _client().get_object(Bucket=_settings.tally_r2_bucket, Key=r2_key)
    size = int(resp.get("ContentLength") or 0)
    if size > max_bytes:
        raise ValueError(
            f"R2 object {r2_key} is {size} bytes, over the "
            f"{max_bytes // (1024 * 1024)} MB limit"
        )
    body = resp["Body"].read(max_bytes + 1)
    if len(body) > max_bytes:
        raise ValueError(f"R2 object {r2_key} exceeds the size limit")
    return body
