# Security Policy

## Auth Invariants

- Production authentication is cookie-only.
- Login endpoints set `shamrai_access_token` as an `HttpOnly` cookie and issue a separate CSRF cookie.
- Login JSON responses must not expose bearer JWTs in production or by default.
- `ENABLE_BEARER_AUTH_COMPAT` and `VITE_ENABLE_BEARER_AUTH_COMPAT` default to `false` for production-like deployments. Enable them only for temporary local or legacy compatibility.
- Unsafe API methods with an auth cookie require a valid CSRF token. If `Origin` or `Referer` is present, it must match the configured allowed origins.

## Secret Handling Checklist

- Do not commit `.env`, private keys, tokens, passwords, database dumps, or service account JSON.
- Keep real secrets only in local ignored env files, runtime environment variables, or the hosting secret store.
- Enable GitHub Secret Scanning and Push Protection.
- Rotate credentials immediately after any suspected exposure.
- Run `gitleaks` or `trufflehog` before publishing release branches, archives, or public mirrors.

## Legacy `SystemSetting` Secret Migration

Secret definitions are env-only. The application must not create or use plaintext database overrides for them. Older `system_settings` rows may still contain plaintext and therefore remain sensitive in the live database and every backup that contains them. Do not query, export, log, or copy the `value` column while migrating.

### Runtime placement

- In production, place each active credential in the hosting or orchestration secret store and inject it into the backend process environment. Do not place values in Compose files, deployment scripts, repository files, or `SystemSetting.value`.
- For local development only, the real-value file is `backend/.env`. Create it from `backend/.env.example` if it does not exist. `backend/.env` must remain ignored by Git and must never be attached to tickets, logs, or chat.
- Use the following key-name mapping. This table contains identifiers only; never add example or real values here.

| Legacy `SystemSetting.key` | Runtime environment key |
| --- | --- |
| `VK_ACCESS_TOKEN` | `VK_GROUP_ACCESS_TOKEN` |
| `TELEGRAM_BOT_TOKEN` | `TELEGRAM_BOT_TOKEN` |
| `TELEGRAM_WEBHOOK_SECRET_TOKEN` | `TELEGRAM_WEBHOOK_SECRET_TOKEN` |
| `VK_CALLBACK_CONFIRMATION_CODE` | `VK_CALLBACK_CONFIRMATION_CODE` |
| `VK_CALLBACK_SECRET` | `VK_CALLBACK_SECRET` |
| `VK_ID_CLIENT_SECRET` | `VK_ID_CLIENT_SECRET` |
| `YOOKASSA_SECRET_KEY` | `YOOKASSA_SECRET_KEY` |
| `TEGRO_API_KEY` | `TEGRO_API_KEY` |
| `TEGRO_SECRET_KEY` | `TEGRO_SECRET_KEY` |
| `WEB_PUSH_VAPID_PRIVATE_KEY` | `WEB_PUSH_VAPID_PRIVATE_KEY` |
| `GOOGLE_OAUTH_CLIENT_SECRET` | `GOOGLE_OAUTH_CLIENT_SECRET` |
| `GOOGLE_OAUTH_REFRESH_TOKEN` | `GOOGLE_OAUTH_REFRESH_TOKEN` |
| `GOOGLE_SERVICE_ACCOUNT_JSON_B64` | `GOOGLE_SERVICE_ACCOUNT_JSON_B64` |
| `PAYMENT_GATEWAY_TOKEN` | No supported runtime mapping; confirm that no deployed legacy consumer remains, then retire it. Do not invent a new env key without a reviewed configuration change. |

### Controlled migration procedure

1. Restrict the change window to authorized operators. Record only operator identity, timestamps, affected key names, and verification results; never record values.
2. Inventory candidate rows with a key-only query. Do not use `SELECT *` or select `value`:

   ```sql
   SELECT key
   FROM system_settings
   WHERE key IN (
       'VK_ACCESS_TOKEN',
       'TELEGRAM_BOT_TOKEN',
       'PAYMENT_GATEWAY_TOKEN',
       'TELEGRAM_WEBHOOK_SECRET_TOKEN',
       'VK_CALLBACK_CONFIRMATION_CODE',
       'VK_CALLBACK_SECRET',
       'VK_ID_CLIENT_SECRET',
       'YOOKASSA_SECRET_KEY',
       'TEGRO_API_KEY',
       'TEGRO_SECRET_KEY',
       'WEB_PUSH_VAPID_PRIVATE_KEY',
       'GOOGLE_OAUTH_CLIENT_SECRET',
       'GOOGLE_OAUTH_REFRESH_TOKEN',
       'GOOGLE_SERVICE_ACCOUNT_JSON_B64'
   )
   ORDER BY key;
   ```

3. Prefer issuing replacement credentials at each provider instead of extracting legacy values from the database. Put replacements directly into the approved secret store under the mapped runtime keys. For an intentionally disabled integration, document that decision and confirm that no runtime consumer requires the key.
4. Roll or restart the backend through the normal deployment process so it receives the new runtime environment. Verify configuration without revealing values:

   - startup validation succeeds;
   - the masked admin settings response reports the expected `is_configured` state while secret `value` fields remain empty;
   - read-only integration diagnostics report the expected status using the owner-only password and short-lived unlock flow;
   - relevant health checks and provider-safe test flows succeed;
   - logs, caches, diagnostics, and audit records contain no secret values.

5. Before deletion, create an encrypted, access-restricted database backup and verify its integrity. Treat that backup as secret-bearing legacy material, apply the shortest approved retention, and do not use it as a routine rollback source.
6. Only after runtime verification, remove the allowlisted rows in a manually reviewed transaction. `RETURNING key` must return key names only:

   ```sql
   BEGIN;

   DELETE FROM system_settings
   WHERE key IN (
       'VK_ACCESS_TOKEN',
       'TELEGRAM_BOT_TOKEN',
       'PAYMENT_GATEWAY_TOKEN',
       'TELEGRAM_WEBHOOK_SECRET_TOKEN',
       'VK_CALLBACK_CONFIRMATION_CODE',
       'VK_CALLBACK_SECRET',
       'VK_ID_CLIENT_SECRET',
       'YOOKASSA_SECRET_KEY',
       'TEGRO_API_KEY',
       'TEGRO_SECRET_KEY',
       'WEB_PUSH_VAPID_PRIVATE_KEY',
       'GOOGLE_OAUTH_CLIENT_SECRET',
       'GOOGLE_OAUTH_REFRESH_TOKEN',
       'GOOGLE_SERVICE_ACCOUNT_JSON_B64'
   )
   RETURNING key;
   ```

   Stop and review the returned key names before ending the transaction. If they exactly match the approved inventory, issue `COMMIT` as a separate command. Otherwise issue `ROLLBACK` and investigate without inspecting values. Never run deletion and commit as an unattended batch.

7. Repeat the key-only inventory query and require zero rows. Re-run the masked configuration and integration checks, then create and integrity-check a fresh post-cleanup backup.
8. Revoke superseded provider credentials after the new runtime configuration is confirmed. If plaintext exposure cannot be excluded, treat rotation as mandatory. Historical backups cannot be sanitized in place: keep them encrypted and access-restricted until approved destruction, or invalidate their credentials immediately.

### Rollback and restore rules

- Before commit, rollback is the database transaction rollback.
- After cleanup, rollback must use a previous version from the approved secret store plus a runtime deployment rollback. Never reinsert plaintext `SystemSetting` rows.
- If an old database backup must be restored, assume that it reintroduces plaintext rows and possibly valid old credentials. Reapply the key-only cleanup, rotate affected credentials, produce a clean backup, and record the incident without recording values.
- Do not delete the last verified backup until the post-cleanup backup has passed integrity and restore-readiness checks.

## CI Hardening Notes

- CI secret scanning must use full git history (`fetch-depth: 0`).
- Compose and Docker validation jobs use dummy env values only.
- TODO(security): pin third-party GitHub Actions to full commit SHAs during a scheduled CI dependency refresh.
