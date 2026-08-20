/* 홈 화면 앱(PWA)의 심부름꾼.
 *
 * 브라우저가 이 파일을 따로 돌려 두고, 화면이 무언가를 불러올 때마다 먼저 들른다.
 * 하는 일은 둘뿐이다.
 *   ① 모양에 쓰이는 것(스타일·글꼴·아이콘)은 한 번 받아 두었다가 다음부터 바로 내준다 — 켜는 속도.
 *   ② 인터넷이 끊겼을 때 첫 화면만이라도 뜨게 한다.
 * 검사 결과처럼 매번 달라지는 것(/api/…)은 손대지 않고 그대로 지나보낸다.
 */

const VERSION = "v1";
const SHELL_CACHE = `domainchecker-shell-${VERSION}`;
const ASSET_CACHE = `domainchecker-assets-${VERSION}`;
const MINE = new Set([SHELL_CACHE, ASSET_CACHE]);

// 처음 설치할 때 미리 받아 둘 것들 (하나라도 못 받아도 설치는 계속된다)
const PRECACHE = [
  "/",
  "/style.css",
  "/manifest.webmanifest",
  "/fonts/MemomentKkukkukk.woff2",
  "/static/icons/icon-192.png",
  "/static/icons/icon-512.png",
];

self.addEventListener("install", (event) => {
  event.waitUntil(
    (async () => {
      const cache = await caches.open(SHELL_CACHE);
      // addAll 은 하나만 실패해도 전부 되돌린다 — 비밀번호 화면이나 잠깐의 오류로
      // 설치가 통째로 엎어지지 않도록 하나씩 따로 담는다.
      await Promise.all(
        PRECACHE.map(async (url) => {
          try {
            const res = await fetch(url, { cache: "no-cache" });
            // 로그인 화면으로 넘겨진 답장은 담지 않는다 — 담아 두면 나중에 꺼내 줄 때
            // 브라우저가 화면 자체를 거부해서 끊긴 상태에서 아무것도 안 뜬다.
            if (res.ok && !res.redirected) await cache.put(url, res);
          } catch (_) {
            /* 지금 못 받으면 다음에 쓸 때 받는다 */
          }
        }),
      );
      await self.skipWaiting();
    })(),
  );
});

self.addEventListener("activate", (event) => {
  event.waitUntil(
    (async () => {
      const names = await caches.keys();
      await Promise.all(names.map((n) => (MINE.has(n) ? null : caches.delete(n))));
      await self.clients.claim();
    })(),
  );
});

/** 새 화면을 먼저 받아 보고, 안 되면 저장해 둔 것을 내준다(끊겨도 뜨게). */
async function networkFirst(request, cacheName, fallbackKey) {
  const cache = await caches.open(cacheName);
  try {
    const res = await fetch(request);
    // 다른 주소로 넘겨진(로그인 화면 등) 답장은 담지 않는다 — 그걸 저장해 두면
    // 다음에 꺼내 줄 때 브라우저가 화면 자체를 거부한다.
    if (res.ok && !res.redirected) cache.put(request, res.clone());
    return res;
  } catch (err) {
    const hit =
      (await cache.match(request)) || (fallbackKey ? await cache.match(fallbackKey) : undefined);
    if (hit) return hit;
    throw err;
  }
}

/** 저장해 둔 것을 바로 내주고, 뒤에서 조용히 새것으로 바꿔 둔다(다음 실행부터 최신). */
async function cacheFirst(request, cacheName) {
  const cache = await caches.open(cacheName);
  const hit = await cache.match(request);
  const fresh = fetch(request)
    .then((res) => {
      if (res.ok) cache.put(request, res.clone());
      return res;
    })
    .catch(() => null);
  if (hit) return hit;
  const res = await fresh;
  if (res) return res;
  throw new Error("offline");
}

self.addEventListener("fetch", (event) => {
  const { request } = event;
  if (request.method !== "GET") return;

  const url = new URL(request.url);
  if (url.origin !== self.location.origin) return;

  // 검사 진행 상황(/api/events)은 끊기지 않고 계속 흘러야 하는 통로다.
  // 결과·설정도 매번 달라지니 심부름꾼이 끼어들지 않는다.
  if (url.pathname.startsWith("/api/")) return;
  // 화면 갈무리 그림과 저장된 보고서는 검사할 때마다 바뀌므로 그대로 둔다.
  if (url.pathname.startsWith("/captures/") || url.pathname.startsWith("/report/")) return;

  if (request.mode === "navigate") {
    event.respondWith(networkFirst(request, SHELL_CACHE, "/"));
    return;
  }

  if (url.pathname === "/style.css" || url.pathname === "/manifest.webmanifest") {
    event.respondWith(networkFirst(request, SHELL_CACHE));
    return;
  }

  if (url.pathname.startsWith("/static/") || url.pathname.startsWith("/fonts/")) {
    event.respondWith(cacheFirst(request, ASSET_CACHE));
  }
});
