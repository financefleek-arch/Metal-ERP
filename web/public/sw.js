/* Minimal service worker: keeps the app installable and shows the shell when offline.
 * It never touches /api, /c/ or /o/ (live data and customer links), so nothing stale is served. */
const CACHE = "metalerp-shell-v1";

self.addEventListener("install", (e) => {
  e.waitUntil(caches.open(CACHE).then((c) => c.add("/")));
  self.skipWaiting();
});

self.addEventListener("activate", (e) => {
  e.waitUntil(
    caches
      .keys()
      .then((keys) => Promise.all(keys.filter((k) => k !== CACHE).map((k) => caches.delete(k))))
      .then(() => self.clients.claim()),
  );
});

self.addEventListener("fetch", (e) => {
  const req = e.request;
  if (req.method !== "GET" || req.mode !== "navigate") return;
  const { pathname } = new URL(req.url);
  if (pathname.startsWith("/api/")) return;
  // Network first so a deploy shows up immediately; the cached shell is the offline fallback.
  e.respondWith(
    fetch(req).catch(() => caches.match("/").then((r) => r || Response.error())),
  );
});
