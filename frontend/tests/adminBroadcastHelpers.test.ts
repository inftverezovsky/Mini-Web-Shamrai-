import { describe, expect, it } from 'vitest';
import {
  betHasSavedFullForecast,
  buildForecastRequestDateSections,
  forecastRequestStatusUsesDateSections,
  formatForecastRequestStatusCount,
} from '../src/pages/admin/AdminBroadcast.helpers';

function makeBet(overrides: Record<string, unknown> = {}) {
  return {
    id: 'bet-1',
    event_name: 'Закрытый прогноз',
    coefficient: '2.10',
    fair_coefficient: null,
    bookmaker_id: 1,
    description: null,
    teaser_text: null,
    status: 'pending',
    author_id: null,
    created_at: '2026-07-01T10:00:00.000Z',
    resolved_at: null,
    bookmaker: null,
    bookmakers: [{ id: 1, name: 'Фонбет', code: 'fonbet', is_active: true }],
    price_stars: null,
    is_unlocked: true,
    is_taken: false,
    guarantee_count: 0,
    supercompensation_count: 0,
    refund_count: 0,
    category: 'prematch',
    live_ends_at: null,
    brain_score: null,
    api_match_id: null,
    sport_type: null,
    outcome: 'П1',
    coupon_image_url: null,
    match_link: null,
    bookmaker_links: [],
    delivery_mode: 'sales_private',
    auto_send_on_interest: false,
    odds_dropped_to: null,
    odds_drop_notified_at: null,
    ...overrides,
  } as any;
}

function makeRequest(overrides: Record<string, unknown> = {}) {
  const bet = makeBet();
  return {
    id: 'request-1',
    bet_id: bet.id,
    user_id: 5853905385,
    status: 'sent',
    delivery_method: 'bot',
    handled_by: null,
    responded_at: '2026-07-01T12:00:00.000Z',
    delivered_at: '2026-07-02T12:00:00.000Z',
    balance_before: 99,
    balance_after: 98,
    no_balance_warning: false,
    created_at: '2026-07-01T10:00:00.000Z',
    updated_at: '2026-07-02T12:00:00.000Z',
    bet,
    user: {
      telegram_id: 5853905385,
      username: null,
      first_name: 'Илья',
      last_name: 'Рачевский',
      photo_url: null,
      vk_user_id: null,
      is_web_only: false,
      matches_remaining: 98,
      guarantee_active: false,
      bookmakers: [],
    },
    ...overrides,
  } as any;
}

describe('AdminBroadcast helpers', () => {
  it('allows saved full forecasts without match and coupon when outcome and odds exist', () => {
    expect(betHasSavedFullForecast(makeBet())).toBe(true);
  });

  it('still requires an outcome before a full forecast is considered saved', () => {
    expect(betHasSavedFullForecast(makeBet({ outcome: '' }))).toBe(false);
  });

  it('uses handled labels for processed forecast request counts', () => {
    expect(formatForecastRequestStatusCount('sent', 1)).toBe('1 взял');
    expect(formatForecastRequestStatusCount('sent', 2)).toBe('2 взяли');
    expect(formatForecastRequestStatusCount('sent', 21)).toBe('21 взял');
    expect(formatForecastRequestStatusCount('manual_sent', 3)).toBe('3 взяли');
    expect(formatForecastRequestStatusCount('declined', 2)).toBe('2 отказались');
    expect(formatForecastRequestStatusCount('removed', 5)).toBe('5 удалены');
    expect(formatForecastRequestStatusCount('interested', 2)).toBe('2 заявки');
  });

  it('enables date sections only for archived request tabs', () => {
    expect(forecastRequestStatusUsesDateSections('sent')).toBe(true);
    expect(forecastRequestStatusUsesDateSections('manual_sent')).toBe(true);
    expect(forecastRequestStatusUsesDateSections('declined')).toBe(true);
    expect(forecastRequestStatusUsesDateSections('removed')).toBe(true);
    expect(forecastRequestStatusUsesDateSections('interested')).toBe(false);
  });

  it('groups processed requests by action month, day, and match', () => {
    const betA = makeBet({ id: 'bet-a', event_name: '111114---22222' });
    const betB = makeBet({ id: 'bet-b', event_name: '33333---44444' });
    const sections = buildForecastRequestDateSections([
      makeRequest({ id: 'request-a1', bet_id: betA.id, bet: betA }),
      makeRequest({ id: 'request-a2', bet_id: betA.id, bet: betA, user_id: 2 }),
      makeRequest({
        id: 'request-b1',
        bet_id: betB.id,
        bet: betB,
        delivered_at: '2026-08-03T12:00:00.000Z',
      }),
    ]);

    expect(sections.map((section) => section.key)).toEqual(['2026-07', '2026-08']);
    expect(sections[0].days[0].key).toBe('2026-07-02');
    expect(sections[0].days[0].groups).toHaveLength(1);
    expect(sections[0].days[0].groups[0].requests).toHaveLength(2);
    expect(sections[1].days[0].key).toBe('2026-08-03');
  });
});
