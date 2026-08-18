import { describe, expect, it } from 'vitest';

import {
  LEGACY_STARS_CHECKOUT_STORAGE_KEY,
  PAYMENT_CHECKOUT_STORAGE_KEY,
  STARS_CHECKOUT_STORAGE_KEY,
  clearStoredStarsCheckout,
  createCheckoutIntent,
  createStarsCheckoutIntent,
  normalizePaymentAttempt,
  parseStoredCheckout,
  parseStoredStarsCheckout,
  readStoredStarsCheckout,
  resolveCheckoutAttemptId,
  writeStoredStarsCheckout,
} from '../src/utils/paymentCheckout';

function createMemoryStorage(initial: Record<string, string> = {}) {
  const values = new Map(Object.entries(initial));
  return {
    getItem: (key: string) => values.get(key) ?? null,
    setItem: (key: string, value: string) => values.set(key, value),
    removeItem: (key: string) => values.delete(key),
  };
}

describe('payment checkout helpers', () => {
  it('creates a stable UUID intent for the same unfinished checkout', () => {
    const now = Date.parse('2026-08-02T12:00:00.000Z');
    const first = createCheckoutIntent(null, {
      provider: 'yookassa',
      planId: 7,
      promoCode: 'SAVE10',
      now,
      randomUuid: () => '11111111-1111-4111-8111-111111111111',
    });
    const repeated = createCheckoutIntent(first, {
      provider: 'yookassa',
      planId: 7,
      promoCode: 'save10',
      now: now + 60_000,
      randomUuid: () => '22222222-2222-4222-8222-222222222222',
    });

    expect(first.intent_id).toBe('11111111-1111-4111-8111-111111111111');
    expect(repeated.intent_id).toBe(first.intent_id);
    expect(PAYMENT_CHECKOUT_STORAGE_KEY).toBe('shamrai_pending_checkout_v1');
  });

  it('reuses a persisted Stars intent for the same bet after a lost response', () => {
    const now = Date.parse('2026-08-02T12:00:00.000Z');
    const first = createStarsCheckoutIntent(null, {
      betId: 'aaaaaaaa-aaaa-4aaa-8aaa-aaaaaaaaaaaa',
      now,
      randomUuid: () => '11111111-1111-4111-8111-111111111111',
    });
    const persisted = {
      ...first,
      attempt_id: '22222222-2222-4222-8222-222222222222',
    };
    const repeated = createStarsCheckoutIntent(persisted, {
      betId: 'aaaaaaaa-aaaa-4aaa-8aaa-aaaaaaaaaaaa',
      now: now + 60_000,
      randomUuid: () => '33333333-3333-4333-8333-333333333333',
    });

    expect(repeated).toEqual(persisted);
    expect(repeated.intent_id).toBe(first.intent_id);
    expect(STARS_CHECKOUT_STORAGE_KEY).toBe('shamrai_pending_stars_checkout_v2');
  });

  it('rotates Stars intent for another bet without expiring an unfinished intent by age', () => {
    const now = Date.parse('2026-08-02T12:00:00.000Z');
    const first = createStarsCheckoutIntent(null, {
      betId: 'aaaaaaaa-aaaa-4aaa-8aaa-aaaaaaaaaaaa',
      now,
      randomUuid: () => '11111111-1111-4111-8111-111111111111',
    });
    const next = createStarsCheckoutIntent(first, {
      betId: 'bbbbbbbb-bbbb-4bbb-8bbb-bbbbbbbbbbbb',
      now: now + 1,
      randomUuid: () => '22222222-2222-4222-8222-222222222222',
    });

    expect(next.intent_id).not.toBe(first.intent_id);
    const aged = {
      ...first,
      created_at: new Date(now - 90 * 24 * 60 * 60 * 1000).toISOString(),
    };
    expect(parseStoredStarsCheckout(JSON.stringify(aged), now)).toEqual(aged);
    expect(createStarsCheckoutIntent(aged, {
      betId: aged.bet_id,
      now,
      randomUuid: () => '33333333-3333-4333-8333-333333333333',
    })).toEqual(aged);
    expect(parseStoredStarsCheckout(JSON.stringify({
      ...first,
      attempt_id: 'invalid',
    }), now)).toBeNull();
  });

  it('stores unfinished Stars checkouts independently for each bet', () => {
    const storage = createMemoryStorage();
    const first = createStarsCheckoutIntent(null, {
      betId: 'aaaaaaaa-aaaa-4aaa-8aaa-aaaaaaaaaaaa',
      randomUuid: () => '11111111-1111-4111-8111-111111111111',
    });
    const second = createStarsCheckoutIntent(null, {
      betId: 'bbbbbbbb-bbbb-4bbb-8bbb-bbbbbbbbbbbb',
      randomUuid: () => '22222222-2222-4222-8222-222222222222',
    });

    writeStoredStarsCheckout(first, storage);
    writeStoredStarsCheckout(second, storage);

    expect(readStoredStarsCheckout(first.bet_id, storage)).toEqual(first);
    expect(readStoredStarsCheckout(second.bet_id, storage)).toEqual(second);
    expect(JSON.parse(storage.getItem(STARS_CHECKOUT_STORAGE_KEY) || '{}')).toMatchObject({
      version: 2,
      checkouts: {
        [first.bet_id]: first,
        [second.bet_id]: second,
      },
    });
  });

  it('migrates the legacy single Stars checkout without dropping it', () => {
    const legacy = createStarsCheckoutIntent(null, {
      betId: 'aaaaaaaa-aaaa-4aaa-8aaa-aaaaaaaaaaaa',
      randomUuid: () => '11111111-1111-4111-8111-111111111111',
    });
    const storage = createMemoryStorage({
      [LEGACY_STARS_CHECKOUT_STORAGE_KEY]: JSON.stringify(legacy),
    });

    expect(readStoredStarsCheckout(legacy.bet_id, storage)).toEqual(legacy);
    expect(storage.getItem(LEGACY_STARS_CHECKOUT_STORAGE_KEY)).toBeNull();
    expect(JSON.parse(storage.getItem(STARS_CHECKOUT_STORAGE_KEY) || '{}')).toMatchObject({
      version: 2,
      checkouts: { [legacy.bet_id]: legacy },
    });
  });

  it('clears only the terminal Stars checkout and protects a newer intent', () => {
    const storage = createMemoryStorage();
    const first = createStarsCheckoutIntent(null, {
      betId: 'aaaaaaaa-aaaa-4aaa-8aaa-aaaaaaaaaaaa',
      randomUuid: () => '11111111-1111-4111-8111-111111111111',
    });
    const second = createStarsCheckoutIntent(null, {
      betId: 'bbbbbbbb-bbbb-4bbb-8bbb-bbbbbbbbbbbb',
      randomUuid: () => '22222222-2222-4222-8222-222222222222',
    });
    writeStoredStarsCheckout(first, storage);
    writeStoredStarsCheckout(second, storage);

    expect(clearStoredStarsCheckout(first.bet_id, 'ffffffff-ffff-4fff-8fff-ffffffffffff', storage)).toBe(false);
    expect(clearStoredStarsCheckout(first.bet_id, first.intent_id, storage)).toBe(true);
    expect(readStoredStarsCheckout(first.bet_id, storage)).toBeNull();
    expect(readStoredStarsCheckout(second.bet_id, storage)).toEqual(second);
  });

  it('rejects malformed or expired persisted checkout state', () => {
    const now = Date.parse('2026-08-02T12:00:00.000Z');
    expect(parseStoredCheckout('{broken', now)).toBeNull();
    expect(parseStoredCheckout(JSON.stringify({
      version: 1,
      intent_id: 'not-a-uuid',
      attempt_id: 'also-invalid',
      provider: 'tegro',
      plan_id: 2,
      promo_code: null,
      created_at: new Date(now).toISOString(),
    }), now)).toBeNull();
    expect(parseStoredCheckout(JSON.stringify({
      version: 1,
      intent_id: '11111111-1111-4111-8111-111111111111',
      attempt_id: '22222222-2222-4222-8222-222222222222',
      provider: 'tegro',
      plan_id: 2,
      promo_code: null,
      created_at: new Date(now - 25 * 60 * 60 * 1000).toISOString(),
    }), now)).toBeNull();
  });

  it('prefers a valid return URL attempt id and falls back to persisted state', () => {
    const stored = {
      version: 1 as const,
      intent_id: '11111111-1111-4111-8111-111111111111',
      attempt_id: '22222222-2222-4222-8222-222222222222',
      provider: 'tegro' as const,
      plan_id: 2,
      promo_code: null,
      created_at: '2026-08-02T12:00:00.000Z',
    };

    expect(resolveCheckoutAttemptId('?attempt_id=33333333-3333-4333-8333-333333333333', stored))
      .toBe('33333333-3333-4333-8333-333333333333');
    expect(resolveCheckoutAttemptId('?attempt_id=unsafe', stored)).toBe(stored.attempt_id);
  });

  it('normalizes checkout_state and creation_status without guessing terminal success', () => {
    expect(normalizePaymentAttempt({
      attempt_id: '11111111-1111-4111-8111-111111111111',
      checkout_state: 'creating',
      status: 'pending',
    }).phase).toBe('checking');

    expect(normalizePaymentAttempt({
      id: '11111111-1111-4111-8111-111111111111',
      creation_status: 'failed',
      status: 'pending',
    }).phase).toBe('failed');

    expect(normalizePaymentAttempt({
      attempt_id: '11111111-1111-4111-8111-111111111111',
      checkout_state: 'ready',
      payment_status: 'succeeded',
      flat_setup_required: true,
    }).phase).toBe('configure_flat');

    expect(normalizePaymentAttempt({
      attempt_id: '11111111-1111-4111-8111-111111111111',
      checkout_state: 'ready',
      status: 'paid',
      flat_subscription: { status: 'active', flat_amount_rub: '10000.00' },
    }).phase).toBe('succeeded');

    expect(normalizePaymentAttempt({
      attempt_id: '11111111-1111-4111-8111-111111111111',
      checkout_state: 'ready',
      status: 'paid',
      flat_setup_required: false,
      flat_subscription: { status: 'pending_setup', flat_amount_rub: '10000.00' },
    }).phase).toBe('succeeded');
  });
});
