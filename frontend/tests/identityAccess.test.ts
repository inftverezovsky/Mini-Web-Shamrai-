import { describe, expect, it } from 'vitest';
import {
  canEnterCabinet,
  getMissingIdentityActions,
  isVkDeliveryReady,
  pickPrimaryAuthProvider,
} from '../src/utils/identityAccess';
import type { UserResponse } from '../src/schemas/schemas';

function makeUser(overrides: Partial<UserResponse>): UserResponse {
  return {
    telegram_id: 123,
    username: null,
    first_name: null,
    last_name: null,
    phone: null,
    photo_url: null,
    vk_photo_url: null,
    is_web_only: false,
    identity_complete: false,
    identity_providers: ['telegram'],
    missing_identity_providers: ['vk'],
    role: 'user',
    stats_display_mode: 'percent',
    bankroll: 0,
    is_onboarded: false,
    experience_level: null,
    bankroll_size: null,
    favorite_sports: [],
    risk_tolerance: null,
    primary_bookmaker: null,
    vk_user_id: null,
    vk_group_member: false,
    vk_messages_allowed: false,
    vk_notifications_allowed: false,
    currency_preference: 'RUB',
    purchased_bets_balance: 0,
    free_bets_available: 0,
    matches_remaining: 0,
    guarantee_active: false,
    guarantee_opened_from_bet_id: null,
    guarantee_closed_at: null,
    onboarding_goal: null,
    ab_group: null,
    tg_chat_joined: false,
    has_used_shield: false,
    alert_min_coef: 1,
    odds_drop_notifications_enabled: true,
    is_night_mode: false,
    night_mode_start: '23:00',
    night_mode_end: '08:00',
    preferred_sports: [],
    other_bookmaker_name: null,
    client_group: null,
    client_tag: null,
    created_at: '2026-01-01T00:00:00Z',
    updated_at: '2026-01-01T00:00:00Z',
    bookmakers: [],
    badges: [],
    ...overrides,
  };
}

describe('identityAccess', () => {
  it('lets a Telegram-only client enter while suggesting VK linking', () => {
    const user = makeUser({
      telegram_id: 323456789,
      identity_providers: ['telegram'],
      missing_identity_providers: ['vk'],
      vk_user_id: null,
    });

    expect(canEnterCabinet(user)).toBe(true);
    expect(getMissingIdentityActions(user).map((action) => action.provider)).toEqual(['vk']);
  });

  it('keeps legacy VK-only clients readable while suggesting Telegram linking', () => {
    const user = makeUser({
      telegram_id: -741852963,
      is_web_only: true,
      identity_providers: ['vk'],
      missing_identity_providers: ['telegram'],
      vk_user_id: '741852963',
    });

    expect(canEnterCabinet(user)).toBe(true);
    expect(getMissingIdentityActions(user).map((action) => action.provider)).toEqual(['telegram']);
  });

  it('does not treat VK ID alone as ready VK message delivery', () => {
    const linkedWithoutMessages = makeUser({
      vk_user_id: '741852963',
      vk_messages_allowed: false,
    });
    const linkedWithMessages = makeUser({
      vk_user_id: '741852963',
      vk_messages_allowed: true,
    });

    expect(isVkDeliveryReady(linkedWithoutMessages)).toBe(false);
    expect(isVkDeliveryReady(linkedWithMessages)).toBe(true);
  });

  it('uses Telegram as the first-login provider whenever Telegram auth is available', () => {
    expect(pickPrimaryAuthProvider({
      runsInTelegramMiniApp: true,
      vkReady: true,
      vkOriginCompatible: true,
      telegramAvailable: true,
    })).toBe('telegram');

    expect(pickPrimaryAuthProvider({
      runsInVkApp: true,
      vkReady: true,
      vkOriginCompatible: true,
      telegramAvailable: true,
    })).toBe('telegram');

    expect(pickPrimaryAuthProvider({
      vkReady: true,
      vkOriginCompatible: true,
      telegramAvailable: true,
    })).toBe('telegram');

    expect(pickPrimaryAuthProvider({
      vkReady: false,
      vkOriginCompatible: false,
      telegramAvailable: true,
    })).toBe('telegram');
  });

  it('falls back to VK only when Telegram auth is unavailable', () => {
    expect(pickPrimaryAuthProvider({
      runsInVkApp: true,
      vkReady: true,
      vkOriginCompatible: true,
      telegramAvailable: false,
    })).toBe('vk');

    expect(pickPrimaryAuthProvider({
      vkReady: true,
      vkOriginCompatible: true,
      telegramAvailable: false,
    })).toBe('vk');
  });
});
