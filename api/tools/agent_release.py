"""Sign and promote tally-agent releases.

The signing key is an ECDSA P-256 private key kept in Vault KV
(`secret/metalerp/core`, field `agent_signing_key`) like the rest of the stack's
secrets. It reaches this container the standard way: `load-vault-secrets.sh` exports
it at deploy time and docker-compose passes it in as `AGENT_SIGNING_KEY` (a single
line: base64 of the PKCS#8 DER key; a PEM is accepted too). Nothing about it is in
`.env`. The tool never talks to Vault itself.

Run on the VPS:
    docker exec -it metalerp-api python -m tools.agent_release sign 1.2.3
    docker exec -it metalerp-api python -m tools.agent_release promote 1.2.3

    gen-key      new key -> stdout (pipe into `vault kv put`), its public key -> stderr
    pubkey       read a key on stdin, print its public key (used when rotating)
    public-keys  print the CURRENT key's public key (paste into ReleaseKeys)
    sign / promote / status

Flow (CI side is .github/workflows/agent-release.yml):
  CI builds + uploads  agent-releases/<ver>/tally-agent-<ver>.zip  and  pending.json
  (unsigned, invisible to the API)  ->  `sign` re-hashes the zip itself, shows it to
  you, and only then signs "<ver>|<sha256>|<size>" and writes release.json
  ->  `promote` copies it to latest.json.

Signing is a deliberate human step so that "someone got CI to build and upload a
zip" is not enough to get code onto shops' PCs. The signer re-hashes the zip rather
than trusting pending.json, and makes you type the start of the hash.
"""

from __future__ import annotations

import argparse
import base64
import hashlib
import json
import os
import re
import sys
import zipfile
from io import BytesIO

from cryptography.exceptions import InvalidSignature
from cryptography.hazmat.primitives import hashes, serialization
from cryptography.hazmat.primitives.asymmetric import ec, utils

from app import backup_storage
from app.config import get_settings
from app.services.tally.agent_release import LATEST_KEY, RELEASE_PREFIX, Release, _parse

VERSION_RE = re.compile(r"^\d+\.\d+\.\d+$")
_MAX_ZIP = 512 * 1024 * 1024


class ReleaseError(Exception):
    """A user-facing failure (bad version, already signed, hash mismatch, ...)."""


def _bucket() -> str | None:
    """The agent-release bucket (not the main tally bucket - see config.py)."""
    return get_settings().agent_release_bucket


def _exists(key: str) -> bool:
    return backup_storage.object_exists(key, bucket=_bucket())


def _get(key: str, max_bytes: int) -> bytes:
    return backup_storage.get_object(key, max_bytes=max_bytes, bucket=_bucket())


def _put(key: str, body: bytes) -> None:
    backup_storage.put_object(key, body, "application/json", bucket=_bucket())


def payload(version: str, sha256: str, size_bytes: int) -> bytes:
    """The exact bytes the agent verifies (TallyAgent.Updates.ReleaseVerifier)."""
    return f"{version}|{sha256.lower()}|{size_bytes}".encode()


# --------------------------------------------------------------------------
# key handling
# --------------------------------------------------------------------------


def parse_private_key(text: str) -> ec.EllipticCurvePrivateKey:
    """base64(PKCS#8 DER) on one line (what gen-key emits), or a PEM."""
    text = text.strip()
    try:
        if text.startswith("-----"):
            key = serialization.load_pem_private_key(text.encode(), password=None)
        else:
            der = base64.b64decode(text, validate=True)
            key = serialization.load_der_private_key(der, password=None)
    except (ValueError, TypeError) as exc:
        raise ReleaseError(f"not a valid private key: {exc}") from exc
    if not isinstance(key, ec.EllipticCurvePrivateKey) or key.curve.name != "secp256r1":
        raise ReleaseError("the signing key must be an ECDSA P-256 key")
    return key


def load_private_key() -> ec.EllipticCurvePrivateKey:
    value = os.environ.get("AGENT_SIGNING_KEY", "")
    if not value.strip():
        raise ReleaseError(
            "AGENT_SIGNING_KEY is not set in this container - is metalerp/core#agent_signing_key "
            "in Vault and wired through load-vault-secrets.sh + docker-compose? (see the RUNBOOK)"
        )
    return parse_private_key(value)


def spki_b64(key: ec.EllipticCurvePrivateKey) -> str:
    """The base64 SubjectPublicKeyInfo the agent trusts (ReleaseKeys.PublicKeysSpkiBase64)."""
    der = key.public_key().public_bytes(
        serialization.Encoding.DER, serialization.PublicFormat.SubjectPublicKeyInfo
    )
    return base64.b64encode(der).decode()


def sign_payload(key: ec.EllipticCurvePrivateKey, data: bytes) -> str:
    """ECDSA P-256 / SHA-256, IEEE P1363 (R||S, 64 bytes), standard base64."""
    r, s = utils.decode_dss_signature(key.sign(data, ec.ECDSA(hashes.SHA256())))
    return base64.b64encode(r.to_bytes(32, "big") + s.to_bytes(32, "big")).decode()


def verify_signature(spki: str, data: bytes, signature_b64: str) -> bool:
    raw = base64.b64decode(signature_b64)
    if len(raw) != 64:
        return False
    r, s = int.from_bytes(raw[:32], "big"), int.from_bytes(raw[32:], "big")
    pub = serialization.load_der_public_key(base64.b64decode(spki))
    assert isinstance(pub, ec.EllipticCurvePublicKey)
    try:
        pub.verify(utils.encode_dss_signature(r, s), data, ec.ECDSA(hashes.SHA256()))
        return True
    except InvalidSignature:
        return False


def gen_key() -> None:
    """New key pair. The private key (one line: base64 PKCS#8 DER) goes to stdout ONLY, so it
    can be handed straight to `vault kv patch secret/metalerp/core agent_signing_key=-` and never
    lands in a file or on the terminal; the public key goes to stderr, where you can see it."""
    key = ec.generate_private_key(ec.SECP256R1())
    der = key.private_bytes(
        serialization.Encoding.DER,
        serialization.PrivateFormat.PKCS8,
        serialization.NoEncryption(),
    )
    sys.stdout.write(base64.b64encode(der).decode() + "\n")
    sys.stdout.flush()
    print("Public key (for ReleaseKeys.PublicKeysSpkiBase64):", file=sys.stderr)
    print(f'    "{spki_b64(key)}",', file=sys.stderr)


def pubkey_from_stdin() -> None:
    """`vault kv get -field=agent_signing_key_next ... | docker exec -i ... pubkey` - the public
    key of a staged (not yet active) key, so a build can trust it before it signs anything."""
    key = parse_private_key(sys.stdin.read())
    print(f'    "{spki_b64(key)}",')


# --------------------------------------------------------------------------
# release store
# --------------------------------------------------------------------------


def _check_version(version: str) -> None:
    if not VERSION_RE.match(version):
        raise ReleaseError(f"version must look like 1.2.3, got {version!r}")


def _release_key(version: str) -> str:
    return f"{RELEASE_PREFIX}/{version}/release.json"


def _pending_key(version: str) -> str:
    return f"{RELEASE_PREFIX}/{version}/pending.json"


def inspect_zip(blob: bytes) -> list[str]:
    """Sanity-check a release zip; returns its entry names. Refuses anything that
    would ship a shop's config, or escape the install dir."""
    with zipfile.ZipFile(BytesIO(blob)) as z:
        names = [n for n in z.namelist() if not n.endswith("/")]
    for required in ("publish/TallyAgent.exe", "install.ps1", "uninstall.ps1"):
        if required not in names:
            raise ReleaseError(f"zip is missing {required}")
    for n in names:
        low = n.lower().rsplit("/", 1)[-1]
        if low.startswith("appsettings"):
            raise ReleaseError(f"zip contains {n} - releases must not carry config")
        if n.startswith("/") or ".." in n.split("/"):
            raise ReleaseError(f"zip has an unsafe path: {n}")
    return names


def sign(version: str, *, assume_yes: bool = False) -> Release:
    _check_version(version)
    key = load_private_key()  # fail early, before any prompting
    if _exists(_release_key(version)):
        raise ReleaseError(f"{version} is already signed - bump the version")
    if not _exists(_pending_key(version)):
        raise ReleaseError(f"no pending release {version} in R2 - did the CI tag build finish?")

    pending = json.loads(_get(_pending_key(version), 1024 * 1024))
    zip_key = pending.get("zip_key", "")
    if zip_key != f"{RELEASE_PREFIX}/{version}/tally-agent-{version}.zip":
        raise ReleaseError(f"pending.json points at an unexpected object: {zip_key!r}")

    blob = _get(zip_key, _MAX_ZIP)
    sha = hashlib.sha256(blob).hexdigest()
    names = inspect_zip(blob)
    recorded = (pending.get("version"), pending.get("sha256"), pending.get("size_bytes"))
    if recorded != (version, sha, len(blob)):
        raise ReleaseError(
            "the zip in R2 does NOT match what CI recorded in pending.json "
            f"(pending sha {pending.get('sha256')} vs actual {sha}) - do not sign; investigate"
        )

    print(f"Release      {version}")
    print(f"sha256       {sha}")
    print(f"size         {len(blob):,} bytes   ({len(names)} files)")
    print(f"git commit   {pending.get('git_sha', '(not recorded)')}")
    print(f"built by CI  {pending.get('built_at', '?')}")
    if not assume_yes:
        typed = input("Compare with the CI log; type the first 8 chars of the sha256 to sign: ")
        if typed.strip().lower() != sha[:8]:
            raise ReleaseError("hash prefix did not match - not signed")

    data = payload(version, sha, len(blob))
    signature = sign_payload(key, data)
    if not verify_signature(spki_b64(key), data, signature):  # belt and braces
        raise ReleaseError("freshly made signature does not verify - not written")

    manifest = {
        "version": version,
        "sha256": sha,
        "size_bytes": len(blob),
        "signature": signature,
        "zip_key": zip_key,
        "built_at": pending.get("built_at"),
        "git_sha": pending.get("git_sha"),
    }
    _put(_release_key(version), json.dumps(manifest, indent=2).encode())
    rel = _parse(json.dumps(manifest).encode())
    assert rel is not None
    print(f"Signed. {_release_key(version)} written. Make it live with:  promote {version}")
    return rel


def promote(version: str) -> None:
    _check_version(version)
    if not _exists(_release_key(version)):
        raise ReleaseError(f"{version} is not signed yet - run `sign {version}` first")
    raw = _get(_release_key(version), 1024 * 1024)
    if _parse(raw) is None:
        raise ReleaseError(f"{_release_key(version)} is malformed")
    _put(LATEST_KEY, raw)
    print(
        f"latest.json -> {version}. Un-pinned agents update on their next checkin "
        "(the API caches latest for up to 60s)."
    )


def status() -> None:
    if not _exists(LATEST_KEY):
        print("no promoted release yet")
        return
    rel = _parse(_get(LATEST_KEY, 1024 * 1024))
    print(f"latest -> {rel.version if rel else '(malformed latest.json)'}")


def public_keys() -> None:
    key = load_private_key()
    print("Paste into ReleaseKeys.PublicKeysSpkiBase64 (TallyAgent/Updates/ReleaseVerifier.cs).")
    print("Keep the line for EVERY key an installed agent may still be signed with:")
    print(f'    "{spki_b64(key)}",')


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawTextHelpFormatter)
    sub = ap.add_subparsers(dest="cmd", required=True)
    s = sub.add_parser("sign")
    s.add_argument("version")
    s.add_argument("--yes", action="store_true", help="skip the hash-prefix confirmation")
    p = sub.add_parser("promote")
    p.add_argument("version")
    sub.add_parser("gen-key")
    sub.add_parser("pubkey")
    sub.add_parser("public-keys")
    sub.add_parser("status")
    a = ap.parse_args(argv)
    try:
        if a.cmd == "sign":
            sign(a.version, assume_yes=a.yes)
        elif a.cmd == "promote":
            promote(a.version)
        elif a.cmd == "gen-key":
            gen_key()
        elif a.cmd == "pubkey":
            pubkey_from_stdin()
        elif a.cmd == "public-keys":
            public_keys()
        else:
            status()
    except ReleaseError as exc:
        print(f"ERROR: {exc}", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
