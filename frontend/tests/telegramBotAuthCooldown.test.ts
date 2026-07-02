import { beforeEach, describe, expect, it, vi } from 'vitest';
import { ApiRequestError } from '../src/api/client';
import {
  clearTelegramBotAuthCooldown,
  getTelegramBotAuthCooldownStatus,
  rememberTelegramBotAuthCooldown,
  rememberTelegramBotAuthCooldownForError,
} from '../src/utils/telegramBotAuthCooldown';

function memoryStorage() {
  const values = new Map<string, string>();
  return {
    getItem: vi.fn((key: string) => values.get(key) ?? null),
    setItem: vi.fn((key: string, value: string) => {
      values.set(key, value);
    }),
    removeItem: vi.fn((key: string) => {
      values.delete(key);
    }),
  };
}

describe('Telegram bot auth cooldown', () => {
  beforeEach(() => {
    vi.useRealTimers();
    vi.unstubAllGlobals();
    vi.stubGlobal('localStorage', memoryStorage());
  });

  it('persists an active cooldown and reports the remaining seconds', () => {
    const now = new Date('2026-07-03T12:00:00.000Z').getTime();
    vi.setSystemTime(now);

    rememberTelegramBotAuthCooldown(90_000);

    expect(getTelegramBotAuthCooldownStatus(now + 15_000)).toMatchObject({
      active: true,
      remainingSeconds: 75,
    });
  });

  it('uses Retry-After from 429 API errors', () => {
    const now = new Date('2026-07-03T12:00:00.000Z').getTime();
    vi.setSystemTime(now);

    rememberTelegramBotAuthCooldownForError(new ApiRequestError('rate limited', 429, 12));

    expect(getTelegramBotAuthCooldownStatus(now + 1_000)).toMatchObject({
      active: true,
      remainingSeconds: 11,
    });
  });

  it('clears expired cooldowns', () => {
    const now = new Date('2026-07-03T12:00:00.000Z').getTime();
    vi.setSystemTime(now);

    rememberTelegramBotAuthCooldown(1_000);
    expect(getTelegramBotAuthCooldownStatus(now + 2_000).active).toBe(false);
    clearTelegramBotAuthCooldown();
    expect(localStorage.removeItem).toHaveBeenCalled();
  });
});
