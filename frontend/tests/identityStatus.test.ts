import { describe, expect, it } from 'vitest';
import { getTelegramIdentityStatus } from '../src/utils/identityStatus';

describe('getTelegramIdentityStatus', () => {
  it('shows Telegram as linked for owner profiles that logged in through VK but have a Telegram provider', () => {
    const status = getTelegramIdentityStatus({
      identity_providers: ['telegram', 'vk'],
      is_web_only: false,
      telegram_id: 1106710042,
      tg_chat_joined: false,
      username: 'Daniil_Tverezovsky',
    });

    expect(status.linked).toBe(true);
    expect(status.badge).toBe('Готово');
    expect(status.detail).toBe('Telegram привязан: @Daniil_Tverezovsky');
    expect(status.deliveryDetail).toContain('Авторизация Telegram активна');
  });

  it('keeps legacy Telegram users linked even when identity providers are missing', () => {
    const status = getTelegramIdentityStatus({
      identity_providers: [],
      is_web_only: false,
      telegram_id: 123,
      tg_chat_joined: true,
      username: null,
    });

    expect(status.linked).toBe(true);
    expect(status.detail).toBe('Telegram привязан: ID 123');
    expect(status.deliveryDetail).toBe('Telegram-бот и чат подтверждены.');
  });

  it('shows a VK-only web profile as ready to connect Telegram', () => {
    const status = getTelegramIdentityStatus({
      identity_providers: ['vk'],
      is_web_only: true,
      telegram_id: -1001,
      tg_chat_joined: false,
      username: null,
    });

    expect(status.linked).toBe(false);
    expect(status.badge).toBe('можно подключить');
  });
});
