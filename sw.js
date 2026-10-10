// App-shell cache. Bump CACHE whenever the shell changes — the old cache is
// dropped on activate, so the phone can't get stuck on a stale build.
const CACHE = 'tasks-v2';

const SHELL = [
  './',
  './index.html',
  './style.css',
  './app.js',
  './logic.js',
  './people.js',
  './store.js',
  './sync.js',
  './manifest.webmanifest',
  './icons/icon-192.png',
  './icons/icon-512.png',
];

self.addEventListener('install', (e) => {
  e.waitUntil(caches.open(CACHE).then((c) => c.addAll(SHELL)).then(() => self.skipWaiting()));
});

self.addEventListener('activate', (e) => {
  e.waitUntil(
    caches.keys()
      .then((keys) => Promise.all(keys.filter((k) => k !== CACHE).map((k) => caches.delete(k))))
      .then(() => self.clients.claim())
  );
});

self.addEventListener('fetch', (e) => {
  const url = new URL(e.request.url);

  // Never touch the API. Task data has its own durability story (the pending
  // queue in localStorage); a cached API response would be actively harmful.
  if (url.origin !== self.location.origin) return;
  if (e.request.method !== 'GET') return;

  // Stale-while-revalidate: instant on a cold subway platform, still fresh
  // by the next launch.
  e.respondWith(
    caches.open(CACHE).then(async (cache) => {
      const cached = await cache.match(e.request, { ignoreSearch: true });
      const network = fetch(e.request)
        .then((res) => {
          if (res && res.ok) cache.put(e.request, res.clone());
          return res;
        })
        .catch(() => cached);
      return cached || network;
    })
  );
});
