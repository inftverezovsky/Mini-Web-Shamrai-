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

## CI Hardening Notes

- CI secret scanning must use full git history (`fetch-depth: 0`).
- Compose and Docker validation jobs use dummy env values only.
- TODO(security): pin third-party GitHub Actions to full commit SHAs during a scheduled CI dependency refresh.
