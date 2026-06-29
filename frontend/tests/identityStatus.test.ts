import { describe, expect, it } from 'vitest';
import { getTelegramIdentityStatus } from '../src/utils/identityStatus';

describe('getTelegramIdentityStatus', () => {
  it('shows Telegram as linked for owner profiles that logged in through VK but have a Telegram provider', () => {
    const status = getTelegramIdentityStatus({
      identity_providers: ['telegram', 'vk'],
      is_web_only: false,
      telegram_id: 1106710042,
      username: 'Daniil_Tverezovsky',
    });

    expect(status.linked).toBe(true);
    expect(status.badge).toBe('привязан');
    expect(status.detail).toBe('Telegram привязан: @Daniil_Tverezovsky');
    expect(status.deliveryDetail).toBeNull();
  });

  it('keeps legacy Telegram users linked even when identity providers are missing', () => {
    const status = getTelegramIdentityStatus({
      identity_providers: [],
      is_web_only: false,
      telegram_id: 123,
      username: null,
    });

    expect(status.linked).toBe(true);
    expect(status.detail).toBe('Telegram привязан: ID 123');
    expect(status.deliveryDetail).toBeNull();
  });

  it('shows a VK-only web profile as ready to connect Telegram', () => {
    const status = getTelegramIdentityStatus({
      identity_providers: ['vk'],
      is_web_only: true,
      telegram_id: -1001,
      username: null,
    });

    expect(status.linked).toBe(false);
    expect(status.badge).toBe('не связан');
  });
});
