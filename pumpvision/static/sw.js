/* Pumpvision service worker.
 *
 * Exists to make the app installable (Chrome requires a service worker with a
 * fetch handler) and to give a useful offline screen instead of the browser's
 * raw "Failed to fetch" -- which on this deployment almost always means
 * Tailscale is off on the phone, not that the server is down.
 *
 * DELIBERATELY DOES NOT CACHE HTML. Every page here is behind login and is
 * role-specific (owner / manager / attendant), and the phones are shared. A
 * cached page could be shown to the next person to open the app, or served
 * after logout. Navigations are therefore network-only; only immutable static
 * assets are cached.
 */
const VERSION = 'v1';
const STATIC_CACHE = 'pumpvision-static-' + VERSION;
const OFFLINE_URL = '/static/offline.html';

const PRECACHE = [
  OFFLINE_URL,
  '/static/css/design-system.css',
  '/static/icons/icon-192.png',
  '/static/icons/icon-512.png',
];

self.addEventListener('install', (event) => {
  event.waitUntil(
    caches.open(STATIC_CACHE)
      // Individual addAll failures must not abort the whole install, so add
      // each entry independently and tolerate misses.
      .then((cache) => Promise.all(
        PRECACHE.map((url) => cache.add(url).catch(() => null))
      ))
      .then(() => self.skipWaiting())
  );
});

self.addEventListener('activate', (event) => {
  event.waitUntil(
    caches.keys()
      .then((names) => Promise.all(
        names.filter((n) => n.startsWith('pumpvision-static-') && n !== STATIC_CACHE)
             .map((n) => caches.delete(n))
      ))
      .then(() => self.clients.claim())
  );
});

self.addEventListener('fetch', (event) => {
  const req = event.request;

  // Never interfere with writes, and never touch cross-origin (Google Fonts) --
  // the browser's own HTTP cache handles those correctly.
  if (req.method !== 'GET') return;
  const url = new URL(req.url);
  if (url.origin !== self.location.origin) return;

  // Page loads: always go to the network. On failure show the offline screen
  // rather than a browser error, so the Tailscale hint is visible.
  if (req.mode === 'navigate') {
    event.respondWith(
      fetch(req).catch(() => caches.match(OFFLINE_URL))
    );
    return;
  }

  // Static assets only: serve from cache, refresh in the background.
  if (url.pathname.startsWith('/static/')) {
    event.respondWith(
      caches.match(req).then((hit) => {
        const network = fetch(req).then((res) => {
          if (res && res.ok) {
            const copy = res.clone();
            caches.open(STATIC_CACHE).then((c) => c.put(req, copy));
          }
          return res;
        }).catch(() => hit);
        return hit || network;
      })
    );
  }
  // Everything else (JSON endpoints, exports) falls through to the network.
});
