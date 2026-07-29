// muxplex soft deck — minimal service worker.
//
// Exists ONLY to satisfy Chrome's install-PROMPT requirement (the "Add to
// phone" / "Install app" menu item works without a service worker since
// Chrome 108, but Chrome's own installability docs still require a `fetch`
// handler for the automatic install banner/prompt to appear).
//
// Deliberately caches NOTHING. This project has already shipped stale-state
// bugs five times (see AGENTS.md's "Frontend delivery: the no-cache header
// is load-bearing" section) from exactly this class of problem: something
// serving old bytes after a deploy. A caching service worker is the single
// most effective way to manufacture that bug on purpose. Every fetch is
// passed straight through to the network; on failure (offline), fall back
// to a tiny inline offline notice instead of a broken cross-origin error
// page.

self.addEventListener('install', function (event) {
  // Activate immediately -- no version to "wait out", there is no cache.
  self.skipWaiting();
});

self.addEventListener('activate', function (event) {
  event.waitUntil(self.clients.claim());
});

self.addEventListener('fetch', function (event) {
  event.respondWith(
    fetch(event.request).catch(function () {
      return new Response(
        '<!doctype html><meta charset="utf-8">' +
          '<body style="background:#0D1117;color:#E6EDF3;' +
          'font:16px -apple-system,sans-serif;padding:32px;text-align:center">' +
          "Can't reach muxplex right now &mdash; check your connection.</body>",
        { headers: { 'Content-Type': 'text/html; charset=utf-8' } }
      );
    })
  );
});
