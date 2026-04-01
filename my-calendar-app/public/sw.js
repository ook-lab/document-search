const CACHE_NAME = "family-calendar-v5";

// インストール時：静的アセットのみキャッシュ（HTMLは除外）
self.addEventListener("install", (event) => {
  event.waitUntil(
    caches.open(CACHE_NAME).then((cache) =>
      cache.addAll(["/manifest.json", "/icon-192x192.png", "/icon-512x512.png"])
    )
  );
  self.skipWaiting();
});

// アクティベート時：古いキャッシュを削除
self.addEventListener("activate", (event) => {
  event.waitUntil(
    caches.keys()
      .then((keys) =>
        Promise.all(keys.filter((k) => k !== CACHE_NAME).map((k) => caches.delete(k)))
      )
      .then(() => self.clients.claim())
  );
});

// フェッチ：API・HTMLはネットワーク優先、静的アセットはキャッシュ優先
self.addEventListener("fetch", (event) => {
  const url = new URL(event.request.url);

  // http/https 以外（chrome-extension等）は無視
  if (!url.protocol.startsWith("http")) return;

  // API・認証はキャッシュしない
  if (url.pathname.startsWith("/api/")) {
    event.respondWith(fetch(event.request));
    return;
  }

  // HTML（ナビゲーション）は常にネットワーク優先
  if (event.request.mode === "navigate") {
    event.respondWith(
      fetch(event.request).catch(() =>
        caches.match("/").then((r) => r || fetch(event.request))
      )
    );
    return;
  }

  // 静的アセット（JS/CSS/画像等）はキャッシュ優先
  event.respondWith(
    caches.match(event.request).then((cached) => {
      if (cached) return cached;
      return fetch(event.request).then((res) => {
        if (res && res.ok && event.request.method === "GET") {
          const clone = res.clone();
          caches.open(CACHE_NAME).then((c) => c.put(event.request, clone));
        }
        return res;
      });
    })
  );
});
