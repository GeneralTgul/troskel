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
    // The page from the cache first: the kept page of that address (the privacy page), otherwise the app itself (the
    // link carries the plan after #, which never reaches here). The network was asked first before, and on a weak
    // connection (a gym in a basement) the app then stood on a white page until the request gave up, with every file
    // in the cache. A new version comes with a new sw.js, which the browser looks for at each visit.
    const shell = new URL("./", self.registration.scope).href;
    event.respondWith(
      caches.match(req, { ignoreSearch: true, ignoreVary: true })
        .then((hit) => hit || caches.match(shell, { ignoreVary: true }))
        .then((hit) => hit || fetch(req)),
    );
    return;
  }
  // ignoreVary: a module script asks with an Origin header that the cached copy was not fetched with, and a server
  // that answers "Vary: Origin" (vite preview, maybe a CDN) would otherwise miss the cache without a network (#787).
  event.respondWith(caches.match(req, { ignoreSearch: true, ignoreVary: true }).then((hit) => hit || fetch(req)));
});
