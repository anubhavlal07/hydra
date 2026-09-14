const VERSION = "hydra-v2";
const SHELL = `${VERSION}-shell`;

const SHELL_URLS = ["/", "/manifest.webmanifest", "/icon-192.png", "/icon-512.png"];

self.addEventListener("install", (event) => {
  event.waitUntil(
    caches
      .open(SHELL)
      .then((cache) => Promise.allSettled(SHELL_URLS.map((url) => cache.add(url)))),
  );
});

self.addEventListener("activate", (event) => {
  event.waitUntil(
    caches
      .keys()
      .then((keys) =>
        Promise.all(keys.filter((k) => !k.startsWith(VERSION)).map((k) => caches.delete(k))),
      )
      .then(() => self.clients.claim()),
  );
});

self.addEventListener("fetch", (event) => {
  const { request } = event;
  if (request.method !== "GET") return;

  const url = new URL(request.url);

  if (url.origin !== self.location.origin) return;

  if (request.mode === "navigate") {
    event.respondWith(
      fetch(request)
        .then((response) => {
          const forCache = response.clone();
          const forPrune = response.clone();
          caches.open(SHELL).then(async (cache) => {
            await cache.put("/", forCache);
            await prune(cache, forPrune);
          });
          return response;
        })
        .catch(() => caches.match("/", { ignoreSearch: true })),
    );
    return;
  }

  const isHashed = url.pathname.startsWith("/_next/");

  if (!isHashed) {
    event.respondWith(
      fetch(request)
        .then((response) => {
          if (response.ok && response.type === "basic") {
            const copy = response.clone();
            caches.open(SHELL).then((cache) => cache.put(request, copy));
          }
          return response;
        })
        .catch(() => caches.match(request).then((hit) => hit || Response.error())),
    );
    return;
  }

  event.respondWith(
    caches.match(request).then(
      (hit) =>
        hit ||
        fetch(request).then((response) => {
          if (response.ok && response.type === "basic") {
            const copy = response.clone();
            caches.open(SHELL).then((cache) => cache.put(request, copy));
          }
          return response;
        }),
    ),
  );
});

async function prune(cache, response) {
  let html;
  try {
    html = await response.text();
  } catch {
    return;
  }

  const keys = await cache.keys();
  await Promise.all(
    keys.map((request) => {
      const path = new URL(request.url).pathname;
      if (!path.startsWith("/_next/")) return undefined;

      if (html.includes(path)) return undefined;
      return cache.delete(request);
    }),
  );
}

self.addEventListener("message", (event) => {
  if (event.data === "skip-waiting") self.skipWaiting();
});

const opens = (url) => (typeof url === "string" && url.startsWith("/#") ? url : "/#overview");

self.addEventListener("push", (event) => {
  let payload = {};
  try {
    payload = event.data ? event.data.json() : {};
  } catch {
    payload = { title: "Hydra DNS", body: event.data ? event.data.text() : "" };
  }

  const title = payload.title || "Hydra DNS";
  const options = {
    body: payload.body || "Something wants attention.",
    tag: payload.tag || "hydra",
    renotify: true,
    icon: "/icon-192.png",
    badge: "/icon-192.png",
    data: { url: opens(payload.url) },
  };

  event.waitUntil(self.registration.showNotification(title, options));
});

self.addEventListener("notificationclick", (event) => {
  event.notification.close();
  const target = (event.notification.data && event.notification.data.url) || "/";

  event.waitUntil(
    self.clients.matchAll({ type: "window", includeUncontrolled: true }).then((windows) => {
      for (const client of windows) {
        if (client.url.includes(self.location.origin) && "focus" in client) {
          client.navigate(target);
          return client.focus();
        }
      }
      return self.clients.openWindow(target);
    }),
  );
});
