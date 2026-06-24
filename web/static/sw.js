const CACHE = "sleeping-fox-v1";
const ASSETS = [
  "/",
  "/static/katex.min.css",
  "/static/katex.min.js",
  "/static/katex-auto-render.min.js",
  "/static/fox-192.png",
  "/static/fox-512.png",
  "/static/manifest.json",
];

self.addEventListener("install", (e) => {
  e.waitUntil(
    caches.open(CACHE).then((cache) => cache.addAll(ASSETS))
  );
});

self.addEventListener("fetch", (e) => {
  // API 请求不缓存
  if (e.request.url.includes("/api/")) return;
  e.respondWith(
    caches.match(e.request).then((r) => r || fetch(e.request))
  );
});
