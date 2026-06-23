# Native Web Chat V2 Reference Review

Date: 2026-06-23

## Reviewed Pack

Source folder: `C:\Users\Sa1z1ngr0z\Downloads\shamrai_native_chat_codex_pack_v2`

Useful references:

- `docs/ARCHITECTURE.md`
- `docs/API_CONTRACT.yaml`
- `docs/WEBSOCKET_EVENTS.md`
- `docs/DB_SCHEMA.sql`
- `docs/ACCEPTANCE_CRITERIA.md`
- `contracts/chat-types.ts`
- `contracts/chat-schemas.py`

## Decisions Applied

- V2 support chat uses new native tables: `chat_conversations`, `chat_messages`, `chat_read_cursors`, `personal_signal_read_cursors`.
- Existing `personal_signals`, `/api/signals/history`, `/api/signals/stream`, forecast take/decline, Telegram/VK delivery, and old v1 support endpoints remain compatible.
- Support V2 starts empty in production. Old `support_staff_message` and `support_client_message` rows in `personal_signals` are not migrated into V2 support history.
- The user chat UI still has a read-only "Личный бот Shamrai" view for signal history, but the V2 signals adapter filters support message types out.
- Staff replies are stored as `chat_messages` and are shown to clients as "Shamrai"; concrete staff names are not exposed to the client.
- WebSocket remains an accelerator only. REST pagination is the source of truth after reconnect.
- Web Push for support messages targets `?open=web-chat&conversation=support`. Telegram/VK copies of support chat messages are not sent.

## Isolation Notes

- No external chat SaaS, iframe, Redis, Firebase, Pusher, submodule, or paid API was added.
- No destructive migration or rewrite of `personal_signals` was added.
- The Telegram Mini App keeps the V2 web chat hidden through the existing web-only tab guard.
- Private message bodies are not logged by the new chat service.

## Follow-Up Verification

- Backend: compile, Alembic head, targeted V2 unit tests, then full backend test discovery.
- Frontend: `npm run lint` and `npm run build`.
- Manual QA: user send/retry, staff reply realtime, close/reopen, REST resync after reconnect, old signal rich cards, take/decline, unread badges, mobile and desktop layout.
