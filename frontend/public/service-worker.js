const CACHE_NAME = 'shamrai-pwa-v5';
const APP_SHELL_URLS = ['/', '/app', '/manifest.json'];

self.addEventListener('install', (event) => {
  event.waitUntil(
    caches.open(CACHE_NAME).then((cache) => cache.addAll(APP_SHELL_URLS)).catch(() => undefined)
  );
  self.skipWaiting();
});

self.addEventListener('activate', (event) => {
  event.waitUntil(
    caches
      .keys()
      .then((keys) => Promise.all(keys.filter((key) => key !== CACHE_NAME).map((key) => caches.delete(key))))
      .then(() => self.clients.claim())
  );
});

self.addEventListener('fetch', (event) => {
  if (event.request.method !== 'GET') return;
  const requestUrl = new URL(event.request.url);
  if (requestUrl.origin !== self.location.origin) return;
  if (requestUrl.pathname.startsWith('/api') || requestUrl.pathname.startsWith('/static')) return;

  event.respondWith(
    fetch(event.request)
      .then((response) => {
        const responseClone = response.clone();
        caches.open(CACHE_NAME).then((cache) => cache.put(event.request, responseClone)).catch(() => undefined);
        return response;
      })
      .catch(() => caches.match(event.request).then((cached) => cached || caches.match('/')))
  );
});

self.addEventListener('push', (event) => {
  let payload = {
    title: 'Личный бот Shamrai',
    body: 'Новый персональный сигнал уже в чате.',
    tag: 'shamrai-signal',
    url: '/app?open=web-bot-chat',
    image: '',
    data: {},
  };

  if (event.data) {
    try {
      payload = { ...payload, ...event.data.json() };
    } catch {
      payload.body = event.data.text();
    }
  }

  const notificationData = {
    ...(payload.data || {}),
    url: payload.url || payload.data?.url || '/app?open=web-bot-chat',
  };

  event.waitUntil(
    self.registration.showNotification(payload.title || 'Shamrai Analytics', {
      body: payload.body || 'Новый персональный сигнал.',
      tag: payload.tag || `shamrai-signal-${Date.now()}`,
      data: notificationData,
      image: payload.image || undefined,
      icon: '/brand/shamrai-favicon.png',
      badge: '/brand/shamrai-favicon.png',
      vibrate: [80, 40, 80],
      requireInteraction: true,
      renotify: true,
      actions: [
        {
          action: 'open',
          title: 'Открыть',
        },
      ],
    })
  );
});

self.addEventListener('notificationclick', (event) => {
  event.notification.close();

  const targetUrl = new URL(event.notification.data?.url || '/app?open=web-bot-chat', self.location.origin);

  event.waitUntil(
    self.clients.matchAll({ type: 'window', includeUncontrolled: true }).then((clientList) => {
      for (const client of clientList) {
        const clientUrl = new URL(client.url);
        if (clientUrl.origin === targetUrl.origin) {
          if ('navigate' in client) {
            return client.navigate(targetUrl.href).then((navigatedClient) => {
              if (navigatedClient && 'focus' in navigatedClient) return navigatedClient.focus();
              return undefined;
            });
          }
          if ('focus' in client) return client.focus();
        }
      }

      if (self.clients.openWindow) {
        return self.clients.openWindow(targetUrl.href);
      }
      return undefined;
    })
  );
});
