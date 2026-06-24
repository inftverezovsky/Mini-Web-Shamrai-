# Backend latency optimization

## Redis hot cache

The production implementation is in `backend/src/core/redis_cache.py` and is wired into
`backend/src/services/chat.py::paginated_signal_messages`.

```python
@router.get("/chat/conversations/signals/messages")
async def get_signal_conversation_messages(...):
    return await paginated_signal_messages(db, user=current_user, before_id=before_id, limit=limit)
```

The service path is cache-first:

```python
cache_key = signal_page_cache_key(user_id=user.telegram_id, before_id=before_id, limit=safe_limit)
cached_page = await cache_get_json(cache_key)
if isinstance(cached_page, dict):
    return cached_page

# DB query runs only on miss, then the JSON payload is stored for 5 minutes.
await cache_set_json(cache_key, page, ttl_seconds=settings.REDIS_HOT_CACHE_TTL_SECONDS)
```

Invalidation is queued when a new `PersonalSignal` is created in
`deliver_personal_signal`, `broadcast_personal_signals`, and `broadcast_live_signal`,
then flushed after successful PostgreSQL commit. This avoids repopulating Redis with
an old page between `flush()` and `commit()`.

Required runtime variables:

```dotenv
REDIS_URL=redis://redis:6379/0
REDIS_CACHE_ENABLED=true
REDIS_HOT_CACHE_TTL_SECONDS=300
```

## Database indexes

This repository uses SQLAlchemy and Alembic, not Prisma. The real migration is
`backend/alembic/versions/20260624_0030_hot_path_cache_indexes.py`.

Prisma-equivalent model snippets for the requested index shape:

```prisma
model User {
  telegram_id BigInt @id
  createdAt   DateTime @default(now())

  @@index([createdAt])
}

model Transaction {
  id          String   @id @default(uuid())
  workspaceId String
  createdAt   DateTime @default(now())

  @@index([workspaceId, createdAt])
}

model JournalEntry {
  id          String   @id @default(uuid())
  workspaceId String
  createdAt   DateTime @default(now())

  @@index([workspaceId, createdAt])
}
```

## Optimistic UI

`frontend/src/pages/user/BetFeed.tsx` now uses `useMutation` for `POST /api/bets/{id}/take`:

```tsx
onMutate: async ({ betId }) => {
  await queryClient.cancelQueries({ queryKey: BETS_FEED_QUERY_KEY });
  const previousFeed = queryClient.getQueryData(BETS_FEED_QUERY_KEY);
  queryClient.setQueryData(BETS_FEED_QUERY_KEY, (current) => markBetAsTakenInFeed(current, betId));
  return { previousFeed };
},
onError: (_err, _vars, context) => {
  queryClient.setQueryData(BETS_FEED_QUERY_KEY, context?.previousFeed);
},
onSettled: () => {
  queryClient.invalidateQueries({ queryKey: BETS_FEED_QUERY_KEY });
}
```

## Nginx HTTP/2, Brotli and immutable assets

Use this block on the public host after `nginx -V 2>&1 | grep -i brotli` confirms
the Brotli module is installed. Without Brotli support, keep the active gzip fallback
from `deploy/nginx/shamrai.conf`.

```nginx
server {
    listen 443 ssl http2;
    listen [::]:443 ssl http2;
    server_name shamra1.pro www.shamra1.pro;

    ssl_certificate /etc/letsencrypt/live/shamra1.pro/fullchain.pem;
    ssl_certificate_key /etc/letsencrypt/live/shamra1.pro/privkey.pem;
    include /etc/letsencrypt/options-ssl-nginx.conf;
    ssl_dhparam /etc/letsencrypt/ssl-dhparams.pem;

    brotli on;
    brotli_comp_level 5;
    brotli_static on;
    brotli_types application/json text/css application/javascript text/javascript text/plain image/svg+xml;

    gzip on;
    gzip_vary on;
    gzip_types application/json text/css application/javascript text/javascript text/plain image/svg+xml;

    location ^~ /assets/ {
        try_files $uri =404;
        add_header Cache-Control "public, max-age=31536000, immutable" always;
    }

    location = /service-worker.js {
        try_files $uri =404;
        add_header Cache-Control "no-store" always;
    }

    location / {
        add_header Cache-Control "no-store, no-cache, must-revalidate, proxy-revalidate" always;
        try_files $uri $uri/ /index.html;
    }
}
```
