"""tools.agent_release: signing with the key delivered via AGENT_SIGNING_KEY, plus
promote / gen-key / pubkey. R2 is faked with an in-memory dict.
"""

from __future__ import annotations

import base64
import hashlib
import io
import json
import zipfile

import pytest
from cryptography.hazmat.primitives import hashes, serialization
from cryptography.hazmat.primitives.asymmetric import ec, utils

import tools.agent_release as tool
from app.services.tally.agent_release import _parse

VERSION = "1.4.0"


def _zip(extra: dict[str, bytes] | None = None, omit: str | None = None) -> bytes:
    files = {
        "publish/TallyAgent.exe": b"exe",
        "publish/TallyAgent.dll": b"dll",
        "install.ps1": b"#i",
        "uninstall.ps1": b"#u",
    }
    files.update(extra or {})
    if omit:
        files.pop(omit)
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w") as z:
        for name, data in files.items():
            z.writestr(name, data)
    return buf.getvalue()


def _b64_der(key: ec.EllipticCurvePrivateKey) -> str:
    der = key.private_bytes(
        serialization.Encoding.DER, serialization.PrivateFormat.PKCS8, serialization.NoEncryption()
    )
    return base64.b64encode(der).decode()


class FakeEnv:
    def __init__(self) -> None:
        self.bucket: dict[str, bytes] = {}
        self.buckets_used: set[str | None] = set()
        self.key = ec.generate_private_key(ec.SECP256R1())

    def exists(self, k: str, *, bucket: str | None = None) -> bool:
        self.buckets_used.add(bucket)
        return k in self.bucket

    def get(self, k: str, max_bytes: int = 0, *, bucket: str | None = None) -> bytes:
        self.buckets_used.add(bucket)
        return self.bucket[k]

    def put(
        self, k: str, body: bytes, content_type: str = "", *, bucket: str | None = None
    ) -> None:
        self.buckets_used.add(bucket)
        self.bucket[k] = body

    def stage_pending(self, blob: bytes | None = None, **overrides: object) -> bytes:
        blob = blob if blob is not None else _zip()
        self.bucket[f"agent-releases/{VERSION}/tally-agent-{VERSION}.zip"] = blob
        pending = {
            "version": VERSION,
            "sha256": hashlib.sha256(blob).hexdigest(),
            "size_bytes": len(blob),
            "zip_key": f"agent-releases/{VERSION}/tally-agent-{VERSION}.zip",
            "built_at": "2026-10-03T00:00:00Z",
            "git_sha": "abc1234",
        }
        pending.update(overrides)
        self.bucket[f"agent-releases/{VERSION}/pending.json"] = json.dumps(pending).encode()
        return blob


@pytest.fixture
def env(monkeypatch: pytest.MonkeyPatch) -> FakeEnv:
    e = FakeEnv()
    monkeypatch.setattr(tool.backup_storage, "object_exists", e.exists)
    monkeypatch.setattr(tool.backup_storage, "get_object", e.get)
    monkeypatch.setattr(tool.backup_storage, "put_object", e.put)
    monkeypatch.setenv("AGENT_SIGNING_KEY", _b64_der(e.key))
    return e


# --------------------------------------------------------------------------
# key handling
# --------------------------------------------------------------------------


def test_payload_is_the_exact_string_the_agent_verifies() -> None:
    assert tool.payload("1.2.3", "ABCD", 10) == b"1.2.3|abcd|10"


def test_parse_private_key_accepts_one_line_base64_and_pem() -> None:
    key = ec.generate_private_key(ec.SECP256R1())
    pem = key.private_bytes(
        serialization.Encoding.PEM, serialization.PrivateFormat.PKCS8, serialization.NoEncryption()
    ).decode()
    assert tool.spki_b64(tool.parse_private_key(_b64_der(key))) == tool.spki_b64(key)
    assert tool.spki_b64(tool.parse_private_key(pem)) == tool.spki_b64(key)
    assert tool.spki_b64(tool.parse_private_key("  " + _b64_der(key) + "\n")) == tool.spki_b64(key)


def test_parse_private_key_rejects_garbage_and_wrong_curve() -> None:
    with pytest.raises(tool.ReleaseError):
        tool.parse_private_key("not a key")
    p384 = ec.generate_private_key(ec.SECP384R1())
    with pytest.raises(tool.ReleaseError, match="P-256"):
        tool.parse_private_key(_b64_der(p384))


def test_missing_key_names_the_wiring(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("AGENT_SIGNING_KEY", raising=False)
    with pytest.raises(tool.ReleaseError, match="load-vault-secrets"):
        tool.load_private_key()


def test_sign_payload_is_64_byte_p1363_that_verifies() -> None:
    key = ec.generate_private_key(ec.SECP256R1())
    sig = tool.sign_payload(key, b"data")
    raw = base64.b64decode(sig)
    assert len(raw) == 64
    assert tool.verify_signature(tool.spki_b64(key), b"data", sig)
    assert not tool.verify_signature(tool.spki_b64(key), b"other", sig)
    other = ec.generate_private_key(ec.SECP256R1())
    assert not tool.verify_signature(tool.spki_b64(other), b"data", sig)


def test_gen_key_prints_private_to_stdout_only_and_public_to_stderr(
    capsys: pytest.CaptureFixture[str],
) -> None:
    tool.gen_key()
    out, err = capsys.readouterr()
    key = tool.parse_private_key(out)  # stdout is exactly one parseable key
    assert out.strip().count("\n") == 0
    assert tool.spki_b64(key) in err
    assert out.strip() not in err  # the private key never reaches the terminal stream


def test_pubkey_reads_a_staged_key_from_stdin(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    key = ec.generate_private_key(ec.SECP256R1())
    monkeypatch.setattr("sys.stdin", io.StringIO(_b64_der(key) + "\n"))
    tool.pubkey_from_stdin()
    assert tool.spki_b64(key) in capsys.readouterr().out


def test_inspect_zip_rules() -> None:
    assert "install.ps1" in tool.inspect_zip(_zip())
    with pytest.raises(tool.ReleaseError, match="missing"):
        tool.inspect_zip(_zip(omit="publish/TallyAgent.exe"))
    with pytest.raises(tool.ReleaseError, match="config"):
        tool.inspect_zip(_zip({"publish/appsettings.json": b"{}"}))
    with pytest.raises(tool.ReleaseError, match="unsafe"):
        tool.inspect_zip(_zip({"publish/../evil.dll": b"x"}))


# --------------------------------------------------------------------------
# sign
# --------------------------------------------------------------------------


def test_sign_writes_a_manifest_the_agent_would_accept(env: FakeEnv) -> None:
    blob = env.stage_pending()
    rel = tool.sign(VERSION, assume_yes=True)

    manifest = json.loads(env.bucket[f"agent-releases/{VERSION}/release.json"])
    assert manifest["sha256"] == hashlib.sha256(blob).hexdigest()
    assert _parse(json.dumps(manifest).encode()) == rel

    # exactly what the agent checks: ECDSA P-256 / SHA-256 over version|sha|size, R||S
    raw = base64.b64decode(manifest["signature"])
    r, s = int.from_bytes(raw[:32], "big"), int.from_bytes(raw[32:], "big")
    env.key.public_key().verify(
        utils.encode_dss_signature(r, s),
        tool.payload(VERSION, manifest["sha256"], len(blob)),
        ec.ECDSA(hashes.SHA256()),
    )


def test_sign_refuses_to_overwrite_a_signed_release(env: FakeEnv) -> None:
    env.stage_pending()
    tool.sign(VERSION, assume_yes=True)
    with pytest.raises(tool.ReleaseError, match="already signed"):
        tool.sign(VERSION, assume_yes=True)


def test_sign_needs_a_pending_release(env: FakeEnv) -> None:
    with pytest.raises(tool.ReleaseError, match="no pending"):
        tool.sign(VERSION, assume_yes=True)


def test_sign_without_a_key_fails_before_anything_else(
    env: FakeEnv, monkeypatch: pytest.MonkeyPatch
) -> None:
    env.stage_pending()
    monkeypatch.delenv("AGENT_SIGNING_KEY")
    with pytest.raises(tool.ReleaseError, match="AGENT_SIGNING_KEY"):
        tool.sign(VERSION, assume_yes=True)
    assert f"agent-releases/{VERSION}/release.json" not in env.bucket


def test_sign_refuses_when_the_zip_changed_after_ci(env: FakeEnv) -> None:
    env.stage_pending()
    zip_key = f"agent-releases/{VERSION}/tally-agent-{VERSION}.zip"
    env.bucket[zip_key] = _zip({"publish/extra.dll": b"!"})
    with pytest.raises(tool.ReleaseError, match="does NOT match"):
        tool.sign(VERSION, assume_yes=True)
    assert f"agent-releases/{VERSION}/release.json" not in env.bucket


def test_sign_refuses_a_zip_that_carries_config(env: FakeEnv) -> None:
    env.stage_pending(_zip({"publish/appsettings.json": b"{}"}))
    with pytest.raises(tool.ReleaseError, match="config"):
        tool.sign(VERSION, assume_yes=True)


def test_sign_refuses_a_pending_that_points_elsewhere(env: FakeEnv) -> None:
    env.stage_pending(zip_key="installers/someone-elses.zip")
    with pytest.raises(tool.ReleaseError, match="unexpected object"):
        tool.sign(VERSION, assume_yes=True)


def test_hash_prefix_confirmation(env: FakeEnv, monkeypatch: pytest.MonkeyPatch) -> None:
    blob = env.stage_pending()
    monkeypatch.setattr("builtins.input", lambda *_: "deadbeef")
    with pytest.raises(tool.ReleaseError, match="not signed"):
        tool.sign(VERSION)
    assert f"agent-releases/{VERSION}/release.json" not in env.bucket

    monkeypatch.setattr("builtins.input", lambda *_: hashlib.sha256(blob).hexdigest()[:8].upper())
    tool.sign(VERSION)
    assert f"agent-releases/{VERSION}/release.json" in env.bucket


# --------------------------------------------------------------------------
# promote / misc
# --------------------------------------------------------------------------


def test_promote_requires_a_signed_release(env: FakeEnv) -> None:
    with pytest.raises(tool.ReleaseError, match="not signed"):
        tool.promote(VERSION)


def test_promote_copies_release_to_latest(env: FakeEnv) -> None:
    env.stage_pending()
    tool.sign(VERSION, assume_yes=True)
    tool.promote(VERSION)
    assert (
        env.bucket["agent-releases/latest.json"]
        == env.bucket[f"agent-releases/{VERSION}/release.json"]
    )


def test_version_must_be_semver(env: FakeEnv) -> None:
    for fn in (tool.sign, tool.promote):
        with pytest.raises(tool.ReleaseError, match="1.2.3"):
            fn("v1.4")  # type: ignore[operator]


def test_public_keys_prints_the_current_key(
    env: FakeEnv, capsys: pytest.CaptureFixture[str]
) -> None:
    tool.public_keys()
    assert tool.spki_b64(env.key) in capsys.readouterr().out


def test_release_files_use_the_dedicated_release_bucket(
    env: FakeEnv, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Sign/promote must touch ONLY the agent-release bucket - never the main tally bucket that
    holds customer backups and the per-shop installers."""
    monkeypatch.setattr(tool.get_settings(), "tally_r2_release_bucket", "metaerp-tallyagent")
    env.stage_pending()
    tool.sign(VERSION, assume_yes=True)
    tool.promote(VERSION)
    tool.status()
    assert env.buckets_used == {"metaerp-tallyagent"}


def test_release_bucket_falls_back_to_the_main_bucket_when_unset(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    settings = tool.get_settings()
    monkeypatch.setattr(settings, "tally_r2_bucket", "main-bucket")
    monkeypatch.setattr(settings, "tally_r2_release_bucket", None)
    assert settings.agent_release_bucket == "main-bucket"
    monkeypatch.setattr(settings, "tally_r2_release_bucket", "metaerp-tallyagent")
    assert settings.agent_release_bucket == "metaerp-tallyagent"


def test_main_returns_nonzero_and_prints_on_error(
    env: FakeEnv, capsys: pytest.CaptureFixture[str]
) -> None:
    assert tool.main(["promote", VERSION]) == 1
    assert "ERROR" in capsys.readouterr().err
