# Shamrai Frontend Infrastructure Optimization

## Bundle Analysis

Run the bundle report locally:

```powershell
cd C:\Users\Sa1z1ngr0z\Desktop\Mini-Web(Shamrai)\frontend
npm run build:analyze
```

The report is generated at `frontend/dist/stats.html`. Do not deploy or commit this generated file.

## Optional PWA Runtime Cache

The app currently uses `frontend/public/service-worker.js` for push notifications. Do not replace it unless push flows are re-tested end to end. If the app later moves to `vite-plugin-pwa`, keep runtime caching limited to safe GET feed/history endpoints:

```ts
import { VitePWA } from 'vite-plugin-pwa';

VitePWA({
  registerType: 'autoUpdate',
  workbox: {
    runtimeCaching: [
      {
        urlPattern: ({ url, request }) => (
          request.method === 'GET'
          && url.origin === self.location.origin
          && (
            url.pathname === '/api/bets/feed-page'
            || url.pathname === '/api/chat/conversations/signals/messages'
            || url.pathname === '/api/signals/history'
          )
        ),
        handler: 'StaleWhileRevalidate',
        options: {
          cacheName: 'shamrai-signals-api-v1',
          expiration: {
            maxEntries: 60,
            maxAgeSeconds: 300,
          },
          cacheableResponse: {
            statuses: [200],
          },
        },
      },
    ],
  },
});
```

Never cache POST actions, auth, payments, admin APIs, read markers, stream tickets, or websocket endpoints.

## Brotli Activation

The active nginx configs keep gzip as a safe fallback because `nginx:stable-alpine` does not ship `ngx_brotli`. To enable Brotli on a Brotli-capable nginx build, include `deploy/nginx/brotli.conf.example` after this preflight passes:

```bash
nginx -V 2>&1 | grep -i brotli
nginx -t
```

If the preflight fails, do not enable `brotli on;`; nginx reload will fail.
