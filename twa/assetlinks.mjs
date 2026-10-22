// Writes web/public/.well-known/assetlinks.json from the package id and signing-cert SHA-256.
//   node twa/assetlinks.mjs "AA:BB:...:FF" ["second:fingerprint"]
// Use the Play App Signing cert (Play Console > App integrity) for store installs, and add the
// upload key too if you also sideload the CI-built AAB/APK. Redeploy the web after changing it.
import { readFileSync, writeFileSync } from "node:fs";

const fps = process.argv.slice(2).map((s) => s.trim().toUpperCase());
if (!fps.length || fps.some((f) => !/^([0-9A-F]{2}:){31}[0-9A-F]{2}$/.test(f))) {
  console.error("usage: node twa/assetlinks.mjs <SHA-256 as AA:BB:..> [more...]");
  process.exit(1);
}
const { packageId } = JSON.parse(readFileSync(new URL("./twa-manifest.json", import.meta.url), "utf8"));
const out = [
  {
    relation: ["delegate_permission/common.handle_all_urls"],
    target: { namespace: "android_app", package_name: packageId, sha256_cert_fingerprints: fps },
  },
];
writeFileSync(new URL("../web/public/.well-known/assetlinks.json", import.meta.url), JSON.stringify(out, null, 2) + "\n");
console.log(`wrote assetlinks.json for ${packageId} with ${fps.length} fingerprint(s)`);
