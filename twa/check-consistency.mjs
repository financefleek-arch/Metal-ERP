// CI guard: the web manifest, the files in dist/ and twa-manifest.json must agree, so a rename or
// colour change in one place can't silently break the Android build. Run after `npm run build` in web/.
import { existsSync, readFileSync } from "node:fs";

const root = new URL("../", import.meta.url);
const read = (p) => JSON.parse(readFileSync(new URL(p, root), "utf8"));
const web = read("web/public/manifest.webmanifest");
const twa = read("twa/twa-manifest.json");
const errors = [];

for (const icon of web.icons) {
  if (!existsSync(new URL(`web/dist${icon.src}`, root))) errors.push(`dist is missing ${icon.src}`);
}
const path = (u) => new URL(u).pathname;
const srcs = web.icons.map((i) => i.src);
if (!srcs.includes(path(twa.iconUrl))) errors.push(`twa iconUrl ${twa.iconUrl} is not in the web manifest`);
if (!srcs.includes(path(twa.maskableIconUrl))) errors.push(`twa maskableIconUrl ${twa.maskableIconUrl} is not in the web manifest`);
if (path(twa.webManifestUrl) !== "/manifest.webmanifest") errors.push("twa webManifestUrl does not point at /manifest.webmanifest");
if (twa.themeColor !== web.theme_color) errors.push(`themeColor ${twa.themeColor} != manifest ${web.theme_color}`);
if (twa.backgroundColor !== web.background_color) errors.push(`backgroundColor ${twa.backgroundColor} != manifest ${web.background_color}`);
if (twa.startUrl !== web.start_url) errors.push(`startUrl ${twa.startUrl} != manifest ${web.start_url}`);
if (!twa.fullScopeUrl.startsWith(`https://${twa.host}/`)) errors.push("fullScopeUrl is not on the TWA host");

const links = read("web/public/.well-known/assetlinks.json");
if (links.length && !links.some((l) => l.target?.package_name === twa.packageId)) {
  errors.push(`assetlinks.json has entries but none for ${twa.packageId}`);
}

if (errors.length) {
  console.error(errors.map((e) => `- ${e}`).join("\n"));
  process.exit(1);
}
console.log("web manifest and twa-manifest agree");
