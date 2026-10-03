"""Builds three self-contained test releases of the agent for the update e2e.

Works on a scratch COPY of ../../TallyAgent (the repo source is never modified):
1.0.0 and 1.1.0 are normal builds, 1.2.0 exits right after startup so the
rollback path is exercised. Each is signed with a throwaway ECDSA P-256 key whose
public half is compiled into the scratch copy. Output: tools/e2e/work/ (git-ignored).

See tally-agent/README.md, "Update end-to-end test"."""
import base64, hashlib, json, os, shutil, subprocess, sys, zipfile
from cryptography.hazmat.primitives import hashes, serialization
from cryptography.hazmat.primitives.asymmetric import ec, utils

HERE = os.path.dirname(os.path.abspath(__file__))
WORK = os.path.join(HERE, "work")
SRC = os.path.join(WORK, "src")
OUT = os.path.join(WORK, "out")
shutil.rmtree(WORK, ignore_errors=True)
os.makedirs(OUT)
shutil.copytree(
    os.path.join(HERE, "..", "..", "TallyAgent"), SRC,
    ignore=shutil.ignore_patterns("bin", "obj"),
)

key = ec.generate_private_key(ec.SECP256R1())
pub_b64 = base64.b64encode(
    key.public_key().public_bytes(serialization.Encoding.DER, serialization.PublicFormat.SubjectPublicKeyInfo)
).decode()

# compile the test public key into the scratch copy
p = os.path.join(SRC, "Updates", "ReleaseVerifier.cs")
s = open(p, encoding="utf-8").read()
assert "PublicKeysSpkiBase64 = [];" in s
open(p, "w", encoding="utf-8").write(s.replace("PublicKeysSpkiBase64 = [];", 'PublicKeysSpkiBase64 = ["%s"];' % pub_b64))


def publish(version):
    dest = os.path.join(OUT, "v" + version)
    r = subprocess.run(
        ["dotnet", "publish", os.path.join(SRC, "TallyAgent.csproj"), "-c", "Release", "-r", "win-x64",
         "--self-contained", "-p:Version=" + version, "-o", dest, "--nologo", "-v", "q"],
        capture_output=True, text=True)
    if r.returncode != 0:
        print(r.stdout[-3000:], r.stderr[-3000:])
        sys.exit("publish failed " + version)
    return dest


def make_release(version, pub_dir):
    zpath = os.path.join(OUT, f"agent-{version}.zip")
    with zipfile.ZipFile(zpath, "w", zipfile.ZIP_DEFLATED) as z:
        for root, _, files in os.walk(pub_dir):
            for f in files:
                full = os.path.join(root, f)
                rel = os.path.relpath(full, pub_dir).replace("\\", "/")
                if rel.lower().startswith("appsettings") or rel.endswith(".pdb"):
                    continue
                z.write(full, "publish/" + rel)
    data = open(zpath, "rb").read()
    sha = hashlib.sha256(data).hexdigest()
    payload = f"{version}|{sha}|{len(data)}".encode()
    der = key.sign(payload, ec.ECDSA(hashes.SHA256()))
    r, s_ = utils.decode_dss_signature(der)
    sig = base64.b64encode(r.to_bytes(32, "big") + s_.to_bytes(32, "big")).decode()
    return {"version": version, "sha256": sha, "size_bytes": len(data), "signature": sig, "zip": zpath}


releases = {}
for v in ("1.0.0", "1.1.0"):
    d = publish(v)
    releases[v] = make_release(v, d)
    print("built", v, releases[v]["size_bytes"])

# 1.2.0: same code but dies right after startup handling -> must be rolled back
svc = os.path.join(SRC, "TallyAgentService.cs")
t = open(svc, encoding="utf-8").read()
marker = "var ctx = new AgentContext(backend, tallyGateway, loggerFactory);"
assert marker in t
open(svc, "w", encoding="utf-8").write(
    t.replace(marker, 'if (AgentVersion.Current == "1.2.0") { Exit(3); }\n        ' + marker, 1))
d = publish("1.2.0")
releases["1.2.0"] = make_release("1.2.0", d)
print("built 1.2.0", releases["1.2.0"]["size_bytes"])

json.dump(releases, open(os.path.join(OUT, "releases.json"), "w"), indent=2)
print("OK")
