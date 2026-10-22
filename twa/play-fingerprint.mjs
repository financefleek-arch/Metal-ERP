// After a release is on Play, reads the Play App Signing certificate from the Publishing API
// (generatedApks) and rewrites assetlinks.json with it plus the upload key. No dependencies.
//   env:  PLAY_SERVICE_ACCOUNT_JSON  (service-account key JSON)
//   args: <versionCode> <upload-key-sha256>
import { createSign } from "node:crypto";
import { readFileSync } from "node:fs";
import { execFileSync } from "node:child_process";
import { fileURLToPath } from "node:url";

const [versionCode, uploadFp] = process.argv.slice(2);
const sa = JSON.parse(process.env.PLAY_SERVICE_ACCOUNT_JSON || "null");
if (!sa || !versionCode || !uploadFp) {
  console.error("usage: PLAY_SERVICE_ACCOUNT_JSON=... node twa/play-fingerprint.mjs <versionCode> <upload-sha256>");
  process.exit(1);
}
const here = (f) => fileURLToPath(new URL(f, import.meta.url));
const { packageId } = JSON.parse(readFileSync(here("./twa-manifest.json"), "utf8"));
const b64 = (s) => Buffer.from(s).toString("base64url");

async function accessToken() {
  const now = Math.floor(Date.now() / 1000);
  const head = b64(JSON.stringify({ alg: "RS256", typ: "JWT" }));
  const claim = b64(
    JSON.stringify({
      iss: sa.client_email,
      scope: "https://www.googleapis.com/auth/androidpublisher",
      aud: "https://oauth2.googleapis.com/token",
      iat: now,
      exp: now + 3000,
    }),
  );
  const sig = createSign("RSA-SHA256").update(`${head}.${claim}`).sign(sa.private_key).toString("base64url");
  const r = await fetch("https://oauth2.googleapis.com/token", {
    method: "POST",
    headers: { "content-type": "application/x-www-form-urlencoded" },
    body: new URLSearchParams({
      grant_type: "urn:ietf:params:oauth:grant-type:jwt-bearer",
      assertion: `${head}.${claim}.${sig}`,
    }),
  });
  if (!r.ok) throw new Error(`token: ${r.status} ${await r.text()}`);
  return (await r.json()).access_token;
}

// Play takes a few minutes to generate the APKs after it processes the bundle.
const token = await accessToken();
let hash;
for (let i = 0; i < 20 && !hash; i++) {
  const r = await fetch(
    `https://androidpublisher.googleapis.com/androidpublisher/v3/applications/${packageId}/generatedApks/${versionCode}`,
    { headers: { authorization: `Bearer ${token}` } },
  );
  if (r.ok) hash = (await r.json()).generatedApks?.[0]?.certificateSha256Hash;
  else console.error(`generatedApks ${r.status}: waiting`);
  if (!hash) await new Promise((res) => setTimeout(res, 30000));
}
if (!hash) {
  console.error("Play did not report a signing certificate in time");
  process.exit(2);
}

const play = hash.replace(/[^0-9a-f]/gi, "").toUpperCase().match(/../g).join(":");
execFileSync("node", [here("./assetlinks.mjs"), play, uploadFp], { stdio: "inherit" });
