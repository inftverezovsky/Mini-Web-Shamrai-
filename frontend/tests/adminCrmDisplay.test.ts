import { describe, expect, it } from 'vitest';
import {
  getClientAvatarSources,
  getClientChannelStatuses,
  getCrmClientTagLabel,
  getClientPriority,
  getClientRecentMatchSummary,
  getCrmBookmakerPreview,
  clientMatchesBookmakerFilter,
} from '../src/utils/adminCrmDisplay';

describe('admin CRM display helpers', () => {
  it('uses Telegram avatar first, then VK avatar fallback', () => {
    expect(getClientAvatarSources({
      photo_url: ' https://telegram.example/avatar.jpg ',
      vk_photo_url: 'data:image/png;base64,vk-avatar',
    })).toEqual([
      'https://telegram.example/avatar.jpg',
      'data:image/png;base64,vk-avatar',
    ]);
  });

  it('shows Telegram as linked without chat-state warnings', () => {
    const statuses = getClientChannelStatuses({
      telegram_id: 42,
      telegram_connected: true,
      telegram_delivery_enabled: false,
      tg_chat_joined: false,
      vk_user_id: 'vk-42',
      vk_connected: true,
      vk_delivery_enabled: false,
      vk_messages_allowed: false,
      web_push_enabled: true,
    });

    expect(statuses.map((status) => `${status.shortLabel}:${status.label}`)).toEqual([
      'TG:привязан',
      'VK:нет разрешения',
      'Web:push включен',
    ]);
    expect(statuses[0]).toMatchObject({
      tone: 'ready',
      ready: false,
      detail: 'Telegram: аккаунт привязан',
    });
  });

  it('localizes legacy onboarding goal CRM tags', () => {
    expect(getCrmClientTagLabel('goal: trust_check')).toBe('Цель: проверить честность');
    expect(getCrmClientTagLabel('goal: fast_signals')).toBe('Цель: быстрые входы');
    expect(getCrmClientTagLabel('  важный клиент  ')).toBe('важный клиент');
    expect(getCrmClientTagLabel(null)).toBeNull();
  });

  it('prioritizes debt, guarantee, active, contact, then demo', () => {
    expect(getClientPriority({ matches_remaining: -2 }).label).toBe('Долг');
    expect(getClientPriority({ guarantee_active: true, matches_remaining: 0 }).label).toBe('Гарантия');
    expect(getClientPriority({ matches_remaining: 4 }).label).toBe('Активен');
    expect(getClientPriority({ matches_remaining: 0, telegram_delivery_enabled: true }).label).toBe('Связаться');
    expect(getClientPriority({ matches_remaining: 0 }).label).toBe('Демо');
  });

  it('summarizes empty match history without a cryptic zero split', () => {
    expect(getClientRecentMatchSummary([])).toMatchObject({
      headline: '0 матчей',
      splitLabel: 'нет истории',
      wins: 0,
      losses: 0,
    });
  });

  it('summarizes streaks and bookmaker overflow', () => {
    const summary = getClientRecentMatchSummary([
      { bet_id: '1', status: 'win', taken_at: null },
      { bet_id: '2', status: 'win', taken_at: null },
      { bet_id: '3', status: 'loss', taken_at: null },
    ]);

    expect(summary.headline).toBe('2 победы подряд');
    expect(summary.splitLabel).toBe('2 победы / 1 неудача');

    expect(getCrmBookmakerPreview(
      [
        { id: 1, name: 'Пари', code: 'pari' },
        { id: 2, name: 'Зенит', code: 'zenit' },
        { id: 3, name: 'Марафонбет', code: 'marathon' },
        { id: 4, name: 'Другая', code: 'other' },
      ],
      'Локальная БК',
    )).toMatchObject({
      extraCount: 1,
      otherLabel: 'Локальная БК',
    });
  });

  it('matches clients by selected bookmaker id', () => {
    const fonbetClient = {
      bookmakers: [{ id: 1, name: 'Фонбет', code: 'fonbet' }],
      other_bookmaker_name: null,
    };
    const otherClient = {
      bookmakers: [{ id: 99, name: 'Другая', code: 'other' }],
      other_bookmaker_name: 'Локальная БК',
    };

    expect(clientMatchesBookmakerFilter(fonbetClient, 'all')).toBe(true);
    expect(clientMatchesBookmakerFilter(fonbetClient, '1')).toBe(true);
    expect(clientMatchesBookmakerFilter(fonbetClient, '2')).toBe(false);
    expect(clientMatchesBookmakerFilter(otherClient, '99')).toBe(true);
  });
});
