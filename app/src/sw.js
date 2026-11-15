/* The service worker: keeps every file of the app so it opens without a network after the first visit.
   Written by vite.config.ts from this file, which fills in the version and the list of files.
   It never touches anything outside the app's own files, and the app makes no other requests. */
const VERSION = "__VERSION__";
const FILES = __FILES__;
const CACHE = `troskel-${VERSION}`;

self.addEventListener("install", (event) => {
  event.waitUntil(
    caches.open(CACHE)
      .then((c) => c.addAll(FILES.map((f) => new URL(f, self.registration.scope).href)))
      .then(() => self.skipWaiting()),
  );
});

self.addEventListener("activate", (event) => {
  event.waitUntil(
    caches.keys()
      .then((keys) => Promise.all(keys.filter((k) => k.startsWith("troskel-") && k !== CACHE).map((k) => caches.delete(k))))
      .then(() => self.clients.claim()),
  );
});

self.addEventListener("fetch", (event) => {
  const req = event.request;
  if (req.method !== "GET" || !req.url.startsWith(self.registration.scope)) return;
  if (req.mode === "navigate") {
    // The link carries the plan after #, which never reaches here: every page is the app itself.
    event.respondWith(fetch(req).catch(() => caches.match(new URL("./", self.registration.scope).href)));
    return;
  }
  event.respondWith(caches.match(req, { ignoreSearch: true }).then((hit) => hit || fetch(req)));
});
