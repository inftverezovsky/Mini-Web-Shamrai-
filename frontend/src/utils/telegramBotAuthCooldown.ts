import { ApiRequestError } from '../api/client';

export interface TelegramBotAuthCooldownStatus {
  active: boolean;
  retryAt: number | null;
  remainingSeconds: number;
  message: string | null;
}

const TELEGRAM_BOT_AUTH_COOLDOWN_STORAGE_KEY = 'shamrai_telegram_bot_auth_retry_after';
const TELEGRAM_BOT_AUTH_RATE_LIMIT_COOLDOWN_MS = 2 * 60 * 1000;

function safeStorageSet(storage: Storage | undefined, key: string, value: string) {
  try {
    storage?.setItem(key, value);
  } catch {
    // Private modes can block storage; the in-memory request still fails safely.
  }
}

function safeStorageGet(storage: Storage | undefined, key: string) {
  try {
    return storage?.getItem(key) || null;
  } catch {
    return null;
  }
}

function safeStorageRemove(storage: Storage | undefined, key: string) {
  try {
    storage?.removeItem(key);
  } catch {
    // Ignore storage failures.
  }
}

function readTelegramBotAuthRetryAt() {
  const rawValue = safeStorageGet(localStorage, TELEGRAM_BOT_AUTH_COOLDOWN_STORAGE_KEY);
  const retryAt = rawValue ? Number(rawValue) : NaN;
  return Number.isFinite(retryAt) && retryAt > 0 ? retryAt : null;
}

export function clearTelegramBotAuthCooldown() {
  safeStorageRemove(localStorage, TELEGRAM_BOT_AUTH_COOLDOWN_STORAGE_KEY);
}

export function rememberTelegramBotAuthCooldown(durationMs = TELEGRAM_BOT_AUTH_RATE_LIMIT_COOLDOWN_MS) {
  safeStorageSet(
    localStorage,
    TELEGRAM_BOT_AUTH_COOLDOWN_STORAGE_KEY,
    String(Date.now() + Math.max(1_000, durationMs)),
  );
}

export function getTelegramBotAuthCooldownStatus(now = Date.now()): TelegramBotAuthCooldownStatus {
  const retryAt = readTelegramBotAuthRetryAt();
  if (!retryAt || retryAt <= now) {
    if (retryAt) clearTelegramBotAuthCooldown();
    return {
      active: false,
      retryAt: null,
      remainingSeconds: 0,
      message: null,
    };
  }

  const remainingSeconds = Math.max(1, Math.ceil((retryAt - now) / 1000));
  const minutes = Math.max(1, Math.ceil(remainingSeconds / 60));
  return {
    active: true,
    retryAt,
    remainingSeconds,
    message: `Telegram временно ограничил попытки входа. Подождите ${minutes} мин. и повторите вход.`,
  };
}

export function rememberTelegramBotAuthCooldownForError(error: unknown) {
  if (error instanceof ApiRequestError && error.status === 429) {
    rememberTelegramBotAuthCooldown(
      error.retryAfterSeconds
        ? error.retryAfterSeconds * 1000
        : TELEGRAM_BOT_AUTH_RATE_LIMIT_COOLDOWN_MS,
    );
  }
}
